"""GUI orchestration: maintenance fence -> bot drain -> GUI restart -> web adoption."""
import os
from pathlib import Path
import queue
import sys
import threading
import time

import program_update_guard as guard
from program_updater import (ProgramUpdater, INSTALL_ENV, RESUME_ENV, GUI, BOT, load_job,
                             read, write, version_key, POINTER)
from program_update_process import identity, capture_bots, reap_bots


class UpdateDeferred(ValueError):
    pass


class ProgramUpdateService:
    def __init__(self, app, data_root, version):
        self.app, self.root = app, app.root
        self.data = Path(data_root).resolve()
        install = os.environ.get(INSTALL_ENV) or Path(sys.executable).parent
        self.updater = ProgramUpdater(self.data, install, version)
        self.version = str(version)
        self.active = False
        self.events = queue.Queue()
        self.root.after(100, self._poll)

    def _poll(self):
        try:
            while True:
                callback = self.events.get_nowait()
                try:
                    callback()
                except Exception as exc:
                    self.app._append_debug_log('program_update_ui_callback_failed type=' + type(exc).__name__)
        except queue.Empty:
            pass
        self._watch_helper()
        self.root.after(100, self._poll)

    def _watch_helper(self):
        helper = getattr(self, '_helper', None)
        if not self.active or helper is None or helper.poll() is None:
            return
        job = load_job(self._job_path)
        self._helper = None
        if job['phase'] == 'stopping':
            # The original GUI is still here, so the helper cannot have switched
            # versions. Unblock the UI instead of leaving a hidden modal forever.
            guard.finish(self.data, job['ticket'], attempt=job['attempt'])
            self.active = False
            self.app.discord_bot_exit_requested = False
            self._close_progress()
            self._resume_notice()
            job['phase'] = 'prepared'
            write(self._job_path, job)
            self.app._show_centered_messagebox('showerror', '전체 업데이트 중단',
                '업데이트 도우미가 종료되어 적용을 중단했습니다.\n'
                '기존 프로그램과 웹서버는 유지합니다. 봇 연결 상태를 확인해주세요.\n\n' + job.get('message', ''),
                parent=self.root)

    def prepare(self, *, automatic=False):
        from program_updater import UPDATE_ENABLED
        if not UPDATE_ENABLED:
            raise ValueError('전체 자동 업데이트 연결 승인을 기다리고 있습니다.')
        if not getattr(sys, 'frozen', False):
            raise ValueError('전체 업데이트 적용은 배포 실행파일에서만 가능합니다. 소스 실행은 보존합니다.')
        path = self.updater.prepare(automatic=automatic)
        return dict(state='prepared' if path else 'current', path=str(path) if path else '',
                    message=self.updater.message)

    def schedule(self, result, *, automatic=False):
        if result['state'] == 'prepared':
            deadline = time.monotonic() + 20 * 60
            def apply_ready():
                try:
                    self.begin(Path(result['path']), automatic=automatic)
                except UpdateDeferred as exc:
                    if automatic and time.monotonic() < deadline:
                        self.root.after(30_000, apply_ready)
                    else:
                        self.app._append_debug_log('program_update_apply_deferred_busy')
                        if not automatic:
                            self.app._show_centered_messagebox('showinfo', '전체 업데이트 적용 대기', str(exc),
                                                              parent=self.app.schedule_window or self.root)
                except Exception as exc:
                    self.app._show_centered_messagebox('showerror', '전체 업데이트 적용 보류',
                        str(exc) + '\n다운로드한 파일은 유지됩니다. 업데이트 관리에서 다시 적용할 수 있습니다.',
                        parent=self.app.schedule_window or self.root)
            self.root.after(250, apply_ready)

    def begin(self, path, *, automatic=False):
        from program_updater import UPDATE_ENABLED
        if not UPDATE_ENABLED:
            raise ValueError('전체 자동 업데이트 연결 승인을 기다리고 있습니다.')
        if not getattr(sys, 'frozen', False):
            raise ValueError('전체 업데이트 적용은 배포 실행파일에서만 가능합니다.')
        app = self.app
        if self.active or getattr(app, 'discord_handover_busy', False):
            raise UpdateDeferred('이미 업데이트 또는 관리자 승계가 진행 중입니다.')
        if self.root.grab_current():
            raise UpdateDeferred('열려 있는 확인창을 닫은 후 업데이트를 적용해주세요.')
        if automatic:
            for name in ('schedule_input_window', 'settings_window', 'schedule_boss_config_window',
                         'schedule_alarm_window', 'schedule_break_window'):
                window = getattr(app, name, None)
                if window is not None and window.winfo_exists() and window.winfo_viewable():
                    raise UpdateDeferred('입력·설정 창을 닫으면 자동 업데이트를 적용합니다.')
        manager = getattr(app, 'web_service_manager', None)
        def ensure_web_idle():
            if manager and (manager.page_updater.status().get('busy')
                            or getattr(manager, '_program_update_work', 0)
                            or getattr(manager, 'page_update_job', None) is not None
                            or getattr(manager.standalone, 'busy', False)
                            or getattr(manager.standalone, 'stopping', False)
                            or manager.status().get('state') in {'starting', 'stopping', 'updating'}):
                raise UpdateDeferred('웹 업데이트·승계·종료 작업이 끝난 후 전체 업데이트를 적용해주세요.')
            process = getattr(manager, 'process', None)
            if process is not None and process.poll() is None and manager.public.status().get('state') == 'prepared':
                try:
                    context = read(manager.root / ('background.' + manager.run_id + '.json'))
                    if context.get('schema') != 1 or context.get('run_id') != manager.run_id:
                        raise ValueError('background context mismatch')
                except (OSError, ValueError, AttributeError) as exc:
                    raise UpdateDeferred('웹 백그라운드 유지 준비를 확인한 뒤 전체 업데이트를 적용합니다.') from exc
        ensure_web_idle()
        job = load_job(path)
        if job['data'] != str(self.data) or job['install'] != str(self.updater.install):
            raise ValueError('다른 설치의 업데이트는 적용하지 않습니다.')
        if version_key(job['version']) <= version_key(self.version):
            raise ValueError('현재 버전보다 새 버전만 적용할 수 있습니다.')
        if not Path(sys.executable).resolve().is_relative_to(self.updater.install):
            raise ValueError('현재 실행파일이 업데이트할 설치 폴더에 속하지 않습니다.')
        payload = app._query_discord_bot_status_port(timeout=.3)
        if payload.get('ok') and not (app._discord_bot_status_matches(payload) or app._owns_discord_bot_child(payload)):
            raise ValueError('다른 설정의 봇이 실행 중이라 업데이트를 시작하지 않았습니다.')
        if getattr(app, 'discord_bot_reconnect_in_progress', False) or getattr(app, 'discord_bot_stale_recovery_in_progress', False):
            raise UpdateDeferred('봇 복구가 진행 중입니다. 완료 후 다시 시도해주세요.')
        origin = identity(os.getpid())
        try:
            attempt = guard.begin(self.data, job['ticket'], path, check=ensure_web_idle, owner=origin)
        except TimeoutError as exc:
            raise UpdateDeferred('진행 중인 봇·웹 작업이 끝나면 전체 업데이트를 적용합니다.') from exc
        self.active = True
        app.discord_bot_exit_requested = True
        app.discord_bot_expected_running = False
        try:
            process = getattr(app, 'discord_bot_process', None)
            process_alive = process is not None and process.poll() is None
            bot_path = app._get_discord_bot_executable_path()
            job.update(attempt=attempt, parent=origin, previous=str(Path(sys.executable).resolve()),
                       previous_pointer=read(self.updater.install / POINTER) if (self.updater.install / POINTER).exists() else None,
                       bot_path=bot_path,
                       bots=capture_bots(bot_path, [payload.get('pid'), getattr(process, 'pid', 0)]),
                       resume=dict(bot=bool(payload.get('ok') or process_alive),
                                   voice=bool(payload.get('send_authorized')),
                                   channel=str(payload.get('voice_channel_id') or app.discord_bot_voice_channel_id),
                                   runtime=str(payload.get('runtime_id') or '')),
                       phase='stopping', message='봇 종료 중 · 웹서버는 유지합니다.')
            write(path, job)
            app.schedule_status_var.set(job['message'])
            self._open_progress('이전 봇 종료 준비 · 웹서버는 유지합니다.')
            app._save_settings()
            if app._save_schedule_alarm_settings() is False:
                raise ValueError('알람 설정 저장을 확인하지 못해 업데이트를 보류했습니다.')
            runtime = getattr(app, 'notice_runtime', None)
            if runtime is not None:
                runtime.close()
        except Exception:
            guard.finish(self.data, job['ticket'], attempt=attempt)
            self.active = False
            app.discord_bot_exit_requested = False
            self._close_progress()
            self._resume_notice()
            raise

        def shutdown():
            try:
                self._detach_web()
                stopped = app._stop_discord_bot_runtime_core(graceful_timeout=4, force_timeout=2)
                # A half-closed bot is rechecked by image path and creation time.
                reap_bots(job['bots'], job['bot_path'])
                if not stopped and app._query_discord_bot_status_port(timeout=.2):
                    raise ValueError('이전 봇의 종료를 확인하지 못했습니다.')
                app.discord_bot_running = False
                coordinator = getattr(app, 'discord_handover', None)
                if coordinator is not None and coordinator.alive:
                    coordinator._release(stopped=True)
                    coordinator.alive = False
                # Arm a detached helper before requesting the normal save-and-exit path.
                from program_update_helper import spawn_helper
                self._helper = spawn_helper(path)
                self._job_path = path
                def close():
                    app._program_update_close_ready = True
                    app.on_close()  # Existing path detaches/preserves the live web server.
                self.events.put(close)
            except Exception as exc:
                def failed(message=str(exc)):
                    guard.finish(self.data, job['ticket'], attempt=attempt)
                    self.active = False
                    app.discord_bot_exit_requested = False
                    self._close_progress()
                    self._resume_notice()
                    app._show_centered_messagebox('showerror', '전체 업데이트 중단',
                        message + '\n기존 프로그램과 웹서버는 유지합니다.', parent=app.schedule_window or app.root)
                self.events.put(failed)
        threading.Thread(target=shutdown, name='program-update-drain', daemon=True).start()

    def _detach_web(self):
        manager = getattr(self.app, 'web_service_manager', None)
        process = getattr(manager, 'process', None)
        if process is None or process.poll() is not None:
            return
        bridge = getattr(self.app, 'web_handover_bridge', None)
        if bridge is not None:
            from web_persistent_runtime import save_background_context
            save_background_context(manager, bridge)  # This method runs on the drain worker, not Tk.
        if manager.public.status().get('state') == 'prepared':
            path = manager.root / ('background.' + manager.run_id + '.json')
            config = read(path)
            if config.get('schema') != 1 or config.get('run_id') != manager.run_id:
                raise ValueError('웹 백그라운드 유지 정보를 확인하지 못해 전체 업데이트를 보류했습니다.')
        manager._flush_snapshot()
        manager._flush_authority(force=True)
        manager.public.send(force=True, detach=True)

    def resume(self):
        path_text = os.environ.get(RESUME_ENV)
        if not path_text:
            return
        path = Path(path_text)
        job = load_job(path)
        state = guard.read(self.data)
        if (state is None or state['ticket'] != job['ticket'] or state['phase'] != 'verifying'
                or state.get('attempt') != job.get('attempt')
                or os.environ.get(guard.ENV_ATTEMPT) != job.get('attempt')
                or version_key(self.version) != version_key(job['version'])
                or Path(sys.executable).resolve() != Path(job['destination']) / GUI):
            raise ValueError('업데이트 재시작 버전·경로·차단 상태가 일치하지 않습니다.')
        app = self.app
        app._program_update_restoring = True
        app._program_update_startup_owned = True
        self._open_progress('새 프로그램 설정 준비 완료 · Discord 봇을 확인합니다.')
        # Cleanup is repeated at restart, before any bot spawn. Web is excluded.
        reap_bots(job['bots'], job['bot_path'])
        deadline = time.monotonic() + 80
        launched = False

        def ready_check():
            nonlocal launched
            try:
                bot = None
                if job['resume']['bot']:
                    if not launched:
                        if not app._start_discord_bot_runtime(gateway_only=True, update_resume=True):
                            raise ValueError('새 봇을 시작하지 못했습니다.')
                        launched = True
                    payload = app._query_discord_bot_status_port(timeout=.15)
                    if not payload.get('ok') or not app._discord_bot_status_matches(payload):
                        if time.monotonic() >= deadline:
                            raise ValueError('새 봇의 로컬 제어 응답을 확인하지 못했습니다.')
                        self.root.after(250, ready_check)
                        return
                    bot = identity(payload['pid'])
                    if Path(bot['path']).resolve() != Path(job['destination']) / BOT:
                        raise ValueError('이전 버전 봇이 응답하여 새 버전 시작을 중단했습니다.')
                    write(path.parent / 'trial_bots.json', capture_bots(bot['path'], [bot['pid'], getattr(app.discord_bot_process, 'pid', 0)]))
                write(path.parent / 'ready.json', dict(ticket=job['ticket'], attempt=job['attempt'], version=job['version'],
                                                       gui=identity(os.getpid()), bot=bot))
                self.root.after(200, committed_check)
            except Exception as exc:
                app._append_debug_log('program_update_start_failed type=' + type(exc).__name__)
                app._program_update_close_ready = True
                self._close_progress()
                app.on_close()  # Helper observes exit and restores the previous version.

        def committed_check():
            if guard.active(self.data):
                if time.monotonic() > deadline + 30:
                    app._program_update_close_ready = True
                    self._close_progress()
                    app.on_close()
                    return
                self.root.after(200, committed_check)
                return
            app._program_update_restoring = False
            self._close_progress()
            os.environ.pop(RESUME_ENV, None)
            os.environ.pop(guard.ENV_TICKET, None)
            os.environ.pop(guard.ENV_ATTEMPT, None)
            self._resume_notice()
            manager = getattr(app, 'web_service_manager', None)
            if manager is not None:
                manager.start_saved()  # Discover/adopt existing worker; never force a new publication.
            app.schedule_status_var.set('전체 업데이트 완료 · 연결 상태를 복원합니다.')
            if job['resume'].get('voice'):
                self._resume_voice(job['resume']['channel'])
        self.root.after(0, ready_check)

    def _resume_notice(self):
        callback = getattr(self.app, '_ensure_notice_runtime', None)
        if callable(callback):
            try:
                callback()
            except Exception as exc:
                self.app._append_debug_log('program_update_notice_resume_failed type=' + type(exc).__name__)

    def _open_progress(self, message):
        import tkinter as tk
        from tkinter import ttk
        from ai_update_center import center_window
        parent = self.root
        for candidate in (getattr(getattr(self.app, 'ai_update_center', None), 'window', None),
                          getattr(self.app, 'schedule_window', None), self.root):
            if candidate is not None and candidate.winfo_exists() and candidate.winfo_viewable():
                parent = candidate
                break
        window = self.progress = tk.Toplevel(parent)
        window.title('전체 업데이트')
        window.configure(bg='#eff6ff')
        window.resizable(False, False)
        window.transient(parent)
        window.protocol('WM_DELETE_WINDOW', lambda: None)
        tk.Label(window, text='전체 업데이트', bg='#1e3a8a', fg='white', anchor='w',
                 font=('맑은 고딕', 13, 'bold'), padx=18, pady=12).pack(fill='x')
        self.progress_message = tk.StringVar(window, value=message)
        tk.Label(window, textvariable=self.progress_message, bg='#eff6ff', fg='#1e293b', wraplength=460,
                 font=('맑은 고딕', 12, 'bold'), padx=18, pady=18).pack(fill='x')
        bar = ttk.Progressbar(window, mode='indeterminate', length=400)
        self.progress_bar = bar
        self.progress_mode = 'indeterminate'
        bar.pack(padx=18, pady=(0, 18))
        bar.start(50)
        center_window(window, parent)
        window.grab_set()
        window.lift()
        window.focus_force()

    def _refresh_download_progress(self):
        if getattr(self, 'progress', None) is None or not self.progress.winfo_exists():
            return
        state = self.updater.progress
        self.progress_message.set(state['message'])
        mode = 'determinate' if state['stage'] == 'downloading' and state['total'] else 'indeterminate'
        if mode != self.progress_mode:
            self.progress_bar.stop()
            self.progress_bar.configure(mode=mode, maximum=100)
            self.progress_mode = mode
            if mode == 'indeterminate':
                self.progress_bar.start(50)
        if mode == 'determinate':
            self.progress_bar.configure(value=min(100, state['received'] * 100 / state['total']))

    def _close_progress(self):
        window = getattr(self, 'progress', None)
        if window is not None:
            if window.winfo_exists():
                self.progress_bar.stop()
                window.grab_release()
                window.destroy()
            self.progress = None

    def _resume_voice(self, channel):
        app = self.app
        deadline = time.monotonic() + 90
        def check():
            payload = app._query_discord_bot_status_port(timeout=.15)
            if payload.get('online') and app._discord_bot_status_matches(payload):
                app._ensure_discord_authority().start(channel=channel, resume_only=True)
            elif time.monotonic() < deadline:
                self.root.after(500, check)
            else:
                app.schedule_status_var.set('업데이트 완료 · 봇 연결 대기 (송출은 연결 후 다시 확인해주세요.)')
        self.root.after(0, check)
