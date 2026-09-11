"""Small Tk adapter; precision workers never read or write Tk widgets."""
from __future__ import annotations

import copy
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import tkinter as tk

from precision_capture_session import PrecisionCaptureSession, CANDIDATES, timer_bounds
from precision_time_tracker import PrecisionConfig
from schedule_precision import clock_text, DEFAULT_CAPTURE_RATE
from precision_region_overlay import RegionOverlay


def clear_preview(app):
    preview = getattr(app, '_precision_preview', None)
    if preview is not None:
        preview.clear()
    app._precision_preview = None


def show_preview(app, owner, slots, area, enabled):
    clear_preview(app)
    if not enabled:
        return
    hwnd = app._get_preferred_odin_window_handle()
    rect = app._get_odin_client_screen_rect(hwnd) if hwnd else None
    if not rect or (rect.get('width'),rect.get('height')) != (1600,900):
        app.schedule_input_status_var.set('영역 미리보기: 오딘 창을 1600×900으로 맞춰주세요.')
        return
    try:
        overlay = RegionOverlay(owner,(int(rect['left']),int(rect['top'])),set())
        app._precision_preview = overlay
        overlay.show([CANDIDATES[0]]+[timer_bounds(slot) for slot in slots.get(area,[])])
    except Exception as exc:
        clear_preview(app)
        app.schedule_input_status_var.set(f'감지영역 미리보기 실패: {exc}')


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
            warning=f"정밀 계측 {exact:%H:%M:%S.%f} (추정 ±{measured['uncertainty_seconds']:.3f}s; 원본 유지)"
            if warning not in slot.setdefault('warnings', []):
                slot['warnings'].append(warning)
        elif slot.get('state') == 'TIMED' and 0 < (slot.get('remaining_seconds') or 0) < 86400:
            if slot.get('severity') != 'error':
                slot['severity'] = 'warn'
            warning='정밀 미확정: 최초 OCR1 값 유지'
            if warning not in slot.setdefault('warnings', []):
                slot['warnings'].append(warning)
    return result


def retry_targets_for(result, report):
    measured = {p['boss_name'] for p in report.get('results', [])}
    targets = {}
    for slot in result.get('slot_results', []):
        name, remaining = slot.get('boss_name'), slot.get('remaining_seconds')
        if not name or name in measured or not isinstance(slot.get('slot_index'), int):
            continue
        if isinstance(remaining, (int,float)) and remaining >= 86400:
            continue
        if slot.get('severity') == 'error' or (slot.get('state') == 'TIMED' and
                                              (not isinstance(remaining,(int,float)) or remaining > 0)):
            targets[name] = slot['slot_index']
    return targets


def merge_retry(previous_result, previous_report, result, report, targets):
    if result.get('area') != previous_result.get('area'):
        raise ValueError('재시도 챕터가 다릅니다.')
    merged = copy.deepcopy(previous_result)
    incoming = {s.get('boss_name'): s for s in result.get('slot_results', [])}
    new_measured = {p['boss_name']: p for p in report.get('results', []) if p['boss_name'] in targets}
    for index, slot in enumerate(merged.get('slot_results', [])):
        name = slot.get('boss_name')
        if name in targets and name in incoming:
            candidate = incoming[name]
            if name in new_measured or (candidate.get('state')=='TIMED' and candidate.get('severity')!='error'):
                merged['slot_results'][index] = copy.deepcopy(candidate)
    combined_report = copy.deepcopy(report)
    combined_report['results'] = copy.deepcopy([p for p in previous_report.get('results', [])
                                               if p['boss_name'] not in new_measured] + list(new_measured.values()))
    total = previous_report.get('total', len(merged.get('slot_results', [])))
    combined_report.update(total=total, completed=total, excluded=total-len(combined_report['results']),
                           retry_targets=list(targets), retry_count=previous_report.get('retry_count',0)+1)
    return merged, combined_report


def start(app, slots, *, retry_context=None):
    if getattr(app, '_precision_session', None) or app.schedule_input_ocr_worker_active or app.schedule_input_ocr_addon_busy:
        return
    clear_preview(app)
    hwnd = app._get_preferred_odin_window_handle()
    if not hwnd:
        app.schedule_input_status_var.set('오딘 창을 찾지 못했습니다.')
        return
    rect = app._get_odin_client_screen_rect(hwnd)
    if not rect or (rect.get('width'),rect.get('height')) != (1600,900):
        app.schedule_input_status_var.set('초단위 찍기는 오딘 클라이언트 1600×900에서만 지원합니다.')
        return
    retry_targets = retry_targets_for(*retry_context) if retry_context is not None else None
    if retry_targets is not None and not retry_targets:
        return
    session = PrecisionCaptureSession(app,hwnd,slots,
        config=PrecisionConfig.from_rate(getattr(app,'precision_capture_rate',DEFAULT_CAPTURE_RATE)),
        retry_targets=retry_targets, expected_area=retry_context[0].get('area') if retry_context else None)
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
    from precision_capture_widgets import CaptureProgress
    progress = CaptureProgress(app.root,rect,session.cancel.set,
                               retry_names=list(retry_targets or ()),rate=1/session.config.interval)
    dialog = progress.window
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
        if app.schedule_input_window is input_window and input_window is not None and input_window.winfo_exists():
            input_window.deiconify()
            was_topmost = input_window.attributes('-topmost')
            input_window.attributes('-topmost', True)
            input_window.lift()
            input_window.focus_force()
            def restore_topmost():
                if input_window.winfo_exists():
                    input_window.attributes('-topmost', was_topmost)
            app.root.after(250,restore_topmost)

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
                    elapsed,total,tracking,completed = data
                    progress.update(elapsed,total,tracking,completed,session.config.duration)
                elif kind=='error':
                    app.schedule_input_status_var.set(f'정밀 스캔 중단: {data}')
                elif kind=='cancelled':
                    app.schedule_input_status_var.set('정밀 스캔을 취소했습니다. 기존 입력은 유지됩니다.')
                elif kind=='done':
                    result,report = data
                    app._append_debug_log(f"precision tracker_end at={report['ended_at']:.6f} duration={report['ended_at']-report['t0']:.6f} max_interval={report['max_capture_interval']:.6f} max_jitter={report['max_jitter']:.6f}")
                    if valid() and result is not None:
                        if retry_context is not None:
                            result,report = merge_retry(*retry_context,result,report,retry_targets)
                        report.update(area=result.get('area'),captured_at=session.wall0.isoformat(),
                                      output_format='fractional seconds; exact times preserved in text and schedule')
                        # No screenshots on disk. Save only the most recent numeric
                        # report for this server profile, never a global schedule.
                        save_error = ''
                        if profile and session.debug_logging:
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
                            f"정밀 스캔 완료: 총 {report['total']}개 · 정밀 확정 {len(report['results'])}개 · 배제/미확정 {report['excluded']}개 · 미확정은 OCR1 값 유지"+save_error)
                        failed = retry_targets_for(updated,report)
                        retry_text = target.get('1.0','end-1c')
                        retry_inputs = [(widget,widget.get('1.0','end-1c'))
                                        for widget in (getattr(app,'schedule_input_text',None),
                                                       getattr(app,'schedule_input_ocr1_text',None))
                                        if widget is not None and widget.winfo_exists()]
                        def offer_retry():
                            def unchanged():
                                return (app.schedule_input_window is input_window and input_window.winfo_exists()
                                        and app._get_schedule_server_profile_dir()==profile
                                        and not getattr(app,'_precision_session',None)
                                        and target.winfo_exists() and target.get('1.0','end-1c')==retry_text
                                        and all(widget.winfo_exists() and widget.get('1.0','end-1c')==text
                                                for widget,text in retry_inputs))
                            if not unchanged():
                                return
                            names = '\n'.join(f'• {name}' for name in failed)
                            answer = app._show_centered_messagebox(
                                'askyesno','미확정 보스 재시도',
                                f'다음 보스의 초정밀 시간을 얻지 못했습니다.\n\n{names}\n\n제외된 보스들만 불러와서 재시도 하겠습니까?\n정상 결과는 유지됩니다. 같은 챕터 화면을 열어두세요.',
                                parent=input_window,default='no')
                            if answer and unchanged():
                                start(app,slots,retry_context=(updated,report))
                        if failed:
                            app.root.after(300,offer_retry)
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
