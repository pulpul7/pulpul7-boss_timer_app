"""Small Tk adapter; precision workers never read or write Tk widgets."""
from __future__ import annotations

import copy
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import tkinter as tk

from precision_capture_session import PrecisionCaptureSession
from precision_time_tracker import PrecisionConfig
from schedule_precision import clock_text


def apply_measurements(result, report):
    """Keep the measured datetime unchanged, including in editable output."""
    result = copy.deepcopy(result)
    measurements = {p['boss_name']: p for p in report['results']}
    for slot in result.get('slot_results', []):
        measured = measurements.get(slot.get('boss_name'))
        if measured:
            exact = datetime.fromisoformat(measured['target_datetime'])
            slot.update(target_datetime=exact, precision='second', precision_measurement=measured,
                        rendered_text=f"{clock_text(exact)} {slot.get('display_name') or slot['boss_name']}")
            slot.setdefault('warnings', []).append(
                f"정밀 계측 {exact:%H:%M:%S.%f} (추정 ±{measured['uncertainty_seconds']:.3f}s; 원본 유지)")
        elif slot.get('state') == 'TIMED' and 0 < (slot.get('remaining_seconds') or 0) < 86400:
            if slot.get('severity') != 'error':
                slot['severity'] = 'warn'
            slot.setdefault('warnings', []).append('정밀 미확정: 최초 OCR1 값 유지')
    return result


def start(app, slots):
    if getattr(app, '_precision_session', None) or app.schedule_input_ocr_worker_active or app.schedule_input_ocr_addon_busy:
        return
    hwnd = app._get_preferred_odin_window_handle()
    if not hwnd:
        app.schedule_input_status_var.set('오딘 창을 찾지 못했습니다.')
        return
    rect = app._get_odin_client_screen_rect(hwnd)
    if not rect or (rect.get('width'),rect.get('height')) != (1600,900):
        app.schedule_input_status_var.set('초단위 찍기는 오딘 클라이언트 1600×900에서만 지원합니다.')
        return
    session = PrecisionCaptureSession(app,hwnd,slots,
        config=PrecisionConfig.from_rate(getattr(app,'precision_capture_rate',2)))
    input_window = app.schedule_input_window
    profile = app._get_schedule_server_profile_dir()
    target = app._get_schedule_input_ocr_text_widget_for_mode('ocr')
    original_text = target.get('1.0','end-1c') if target else ''
    app._precision_session = session
    app.schedule_input_ocr_worker_active = True
    app.schedule_input_ocr_addon_busy = True
    app._set_schedule_input_ocr_loading_lock(True)
    app._update_schedule_input_ocr_addon_controls()
    # Keep the progress window above the board, never over any measured ROI.
    restored = []
    for window in (app.root,input_window,app.schedule_input_ocr_addon_window):
        if window is not None and window.winfo_exists():
            restored.append((window,window.state()))
            window.withdraw()
    dialog = tk.Toplevel(app.root)
    dialog.title('초단위 정밀 캡처')
    dialog.geometry(f"390x120{int(rect['left'])+470:+d}{int(rect['top'])+20:+d}")
    dialog.resizable(False,False)
    dialog.attributes('-topmost',True)
    label = tk.Label(dialog,text='초단위 정밀 스캔 준비 중...\n시간표를 움직이거나 다른 창으로 가리지 마세요.',justify='left')
    label.pack(padx=10,pady=10)
    tk.Button(dialog,text='취소',command=session.cancel.set).pack()
    dialog.protocol('WM_DELETE_WINDOW',session.cancel.set)
    dialog.update_idletasks()
    dialog.grab_set()
    terminal = False

    def valid():
        return (getattr(app,'_precision_session',None) is session
                and app.schedule_input_window is input_window and input_window is not None
                and input_window.winfo_exists() and app._get_schedule_server_profile_dir()==profile
                and target is not None and target.winfo_exists()
                and target.get('1.0','end-1c')==original_text)

    def finish():
        nonlocal terminal
        terminal = True
        try:
            dialog.grab_release()
            dialog.destroy()
        except tk.TclError:
            pass
        if getattr(app,'_precision_session',None) is session:
            app._precision_session = None
            app.schedule_input_ocr_worker_active = False
            app.schedule_input_ocr_addon_busy = False
            app._set_schedule_input_ocr_loading_lock(False)
            app._update_schedule_input_ocr_addon_controls()
        for window,state in restored:
            if window.winfo_exists() and state!='withdrawn':
                window.state(state)

    def poll():
        if terminal:
            return
        try:
            if not valid():
                session.cancel.set()
            while True:
                kind,data = session.events.get_nowait()
                if kind=='log':
                    app._append_debug_log(data)
                elif kind=='progress' and dialog.winfo_exists():
                    elapsed,tracking,completed = data
                    label.config(text=f'초단위 정밀 스캔 중... {elapsed:.0f} / {session.config.duration:.0f}초\n추적 {tracking} / 완료 {completed}')
                elif kind=='error':
                    app.schedule_input_status_var.set(f'정밀 스캔 중단: {data}')
                elif kind=='cancelled':
                    app.schedule_input_status_var.set('정밀 스캔을 취소했습니다. 기존 입력은 유지됩니다.')
                elif kind=='done':
                    result,report = data
                    app._append_debug_log(f"precision tracker_end at={report['ended_at']:.6f} duration={report['ended_at']-report['t0']:.6f} max_interval={report['max_capture_interval']:.6f} max_jitter={report['max_jitter']:.6f}")
                    if valid() and result is not None:
                        report.update(area=result.get('area'),captured_at=session.wall0.isoformat(),
                                      output_format='fractional seconds; exact times preserved in text and schedule')
                        # No screenshots on disk. Save only the most recent numeric
                        # report for this server profile, never a global schedule.
                        save_error = ''
                        if profile:
                            try:
                                path = Path(profile)/'precision_capture_latest.json'
                                temporary = path.with_suffix('.json.tmp')
                                temporary.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                                os.replace(temporary,path)
                            except OSError as exc:
                                save_error = f' (정밀 결과 파일 저장 실패: {exc})'
                                app._append_debug_log(f'precision report_save_error={exc}')
                        app.schedule_input_precision_report = report
                        updated = apply_measurements(result,report)
                        previous = app.schedule_input_ocr_mode_results.get('ocr',[])
                        combined = [r for r in previous if r.get('area')!=updated.get('area')]+[updated]
                        app.schedule_input_ocr_mode_results['ocr'] = combined
                        app.schedule_input_ocr_results = combined
                        app.schedule_input_ocr_last_mode = 'ocr'
                        app._set_schedule_input_ocr_loading_lock(False)
                        app._render_schedule_input_ocr_text('ocr')
                        app.schedule_input_status_var.set(
                            f"정밀 스캔 완료: {len(report['results'])}개 확정 · 미확정은 OCR1 값 유지 · 최대 캡처 간격 {report['max_capture_interval']:.3f}s"+save_error)
                    else:
                        app.schedule_input_status_var.set('정밀 결과를 반영하지 않았습니다: 입력창/서버 변경 또는 OCR 결과 없음.')
                    # Rendering changed the text intentionally; the next poll
                    # must not mistake our own output for a user edit.
                    finish()
                    return
                elif kind=='stopped':
                    finish()
                    return
        except queue.Empty:
            pass
        except Exception as exc:
            session.cancel.set()
            app._append_debug_log(f'precision ui_error={exc}')
            finish()
            return
        app.root.after(100,poll)

    # Allow the old windows to disappear before recording T0. No OCR is run
    # during this hide delay; tracker and OCR both use the later real capture.
    app.root.after(200,session.start)
    app.root.after(100,poll)
