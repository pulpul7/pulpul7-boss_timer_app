"""Offline fixtures only: no builds, downloads, real process termination or user data."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch, ANY
import zipfile
from datetime import datetime, timedelta
from types import SimpleNamespace as NS
import queue

import program_update_guard as guard
import program_update_helper as helper
import program_update_process as processes
from program_updater import GUI, BOT, POINTER, KST, ProgramUpdater, load_job, read, write, target_from_pointer
from program_update_package import MANIFEST, manifest_for, verify_archive, version_key, release_rows
from distribution_defaults import revision_token, save_if_current, STATE


class UpdateSequenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='update-sequence-unit-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.data, self.install = self.base / 'data', self.base / 'install'
        self.data.mkdir()
        self.install.mkdir()
        self.ticket = 'a' * 32
        self.folder = self.data / 'update_program/jobs' / self.ticket
        self.folder.mkdir(parents=True)
        self.dest = self.install / 'program_versions' / self.ticket
        self.dest.mkdir(parents=True)
        self.files = {GUI: b'MZ-fake-gui-unit-fixture', BOT: b'MZ-fake-bot-unit-fixture'}
        for name, raw in self.files.items():
            (self.dest / name).write_bytes(raw)
        self.manifest = manifest_for('5.6.0', self.files)
        self.path = self.folder / 'transaction.json'
        self.job = dict(schema=1, ticket=self.ticket, version='5.6.0', phase='stopping',
                        data=str(self.data), install=str(self.install), destination=str(self.dest),
                        previous=str(self.install / GUI), manifest=self.manifest,
                        parent=dict(pid=10, parent_pid=0, path=str(self.install / GUI), created=1),
                        bot_path=str(self.install / BOT), bots=[], resume=dict(bot=False), previous_pointer=None)
        write(self.path, self.job)
        self.job['attempt'] = guard.begin(self.data, self.ticket, self.path)
        write(self.path, self.job)

    def test_fence_blocks_queued_launch_and_only_allows_verified_restart_ticket(self):
        with patch.dict(os.environ, {guard.ENV_ROOT: str(self.data), guard.ENV_TICKET: self.ticket,
                                     guard.ENV_ATTEMPT: self.job['attempt']}):
            self.assertTrue(guard.blocked())
            with self.assertRaises(ValueError):
                with guard.launch_gate():
                    self.fail('spawn should not run')
            guard.phase(self.data, self.ticket, 'verifying')
            self.assertFalse(guard.blocked())
            with patch.dict(os.environ, {guard.ENV_TICKET: 'wrong'}):
                self.assertTrue(guard.blocked())
            self.assertTrue(guard.active())  # Web adoption/updates still wait for commit.

    def test_different_ticket_cannot_clear_fence_and_corrupt_fence_fails_closed(self):
        with self.assertRaises(ValueError):
            guard.finish(self.data, 'b' * 32)
        guard.fence_path(self.data).write_text('broken', encoding='utf-8')
        self.assertTrue(guard.blocked(self.data))

    def test_success_keeps_original_exe_and_live_web_data(self):
        (self.install / GUI).write_bytes(b'original exe must remain for live web')
        web = self.data / 'web_service'
        web.mkdir()
        write(web / 'persistent_worker.json', dict(pid=55, run_id='keep'))
        write(web / 'access_settings.json', dict(secret='keep'))
        old_gui = (self.install / GUI).read_bytes()
        ready_gui = dict(pid=20, parent_pid=0, path=str(self.dest / GUI), created=2)
        def spawn(*args, **kwargs):
            write(self.folder / 'ready.json', dict(ticket=self.ticket, attempt=self.job['attempt'], version='5.6.0', gui=ready_gui, bot=None))
            return Mock(pid=20, poll=Mock(return_value=None))
        with patch.object(helper, 'identity', return_value=ready_gui), \
             patch.object(helper, 'wait_exited') as wait, patch.object(helper, 'reap_bots') as reap, \
             patch.object(helper, 'alive', return_value=True), patch.object(helper, 'spawn_gui', side_effect=spawn):
            helper.apply(self.path)
        wait.assert_called_once_with(self.job['parent'], 120, cancelled=ANY)
        reap.assert_called_once_with([], str(self.install / BOT))
        self.assertEqual(read(self.path)['phase'], 'complete')
        self.assertFalse(guard.active(self.data))
        self.assertEqual(target_from_pointer(self.install), self.dest / GUI)
        self.assertEqual((self.install / GUI).read_bytes(), old_gui)
        self.assertEqual(read(web / 'persistent_worker.json')['pid'], 55)
        self.assertEqual(read(web / 'access_settings.json')['secret'], 'keep')

    def test_failed_trial_restores_settings_and_receipts_without_touching_web_sessions(self):
        profile = self.data / 'server_profiles/season_18/9/init/fixed.txt'
        profile.parent.mkdir(parents=True)
        profile.write_text('old fixed bosses', encoding='utf-8')
        write(self.data / STATE, dict(schema=1, scopes={'old': {}}))
        web = self.data / 'web_service'
        web.mkdir()
        write(web / 'boss_spectrum.json', dict(old=7))
        write(web / 'access_settings.json', dict(session='keep'))
        calls = []
        def spawn(executable, *_args, **_kwargs):
            calls.append(str(executable))
            if len(calls) == 1:
                profile.write_text('new trial bosses', encoding='utf-8')
                write(self.data / STATE, dict(schema=1, scopes={'new': {}}))
                write(web / 'boss_spectrum.json', dict(new=30))
                raise OSError('fake startup failure')
            return Mock()
        with patch.object(helper, 'identity', return_value=self.job['parent']), \
             patch.object(helper, 'wait_exited'), patch.object(helper, 'reap_bots'), \
             patch.object(helper, 'stop_trial'), patch.object(helper, 'trial_processes', return_value=[]), \
             patch.object(helper, 'spawn_gui', side_effect=spawn):
            helper.apply(self.path)
        self.assertEqual(read(self.path)['phase'], 'rolled_back')
        self.assertEqual(profile.read_text(encoding='utf-8'), 'old fixed bosses')
        self.assertIn('old', read(self.data / STATE)['scopes'])
        self.assertEqual(read(web / 'boss_spectrum.json'), dict(old=7))
        self.assertEqual(read(web / 'access_settings.json'), dict(session='keep'))
        self.assertEqual(calls[-1], self.job['previous'])
        self.assertFalse((self.install / POINTER).exists())

    def test_corrupt_backup_is_rejected_before_any_live_settings_change(self):
        settings = self.data / 'settings.json'
        write(settings, dict(value='old'))
        helper.backup_settings(self.job, self.folder)
        write(settings, dict(value='current'))
        added = self.data / 'new_settings.json'
        write(added, dict(keep=True))
        (self.folder / 'settings/settings.json').write_bytes(b'corrupt backup')
        with self.assertRaises(ValueError):
            helper.restore_settings(self.job, self.folder)
        self.assertEqual(read(settings), dict(value='current'))
        self.assertEqual(read(added), dict(keep=True))

    def test_old_settings_snapshot_cannot_overwrite_new_distribution(self):
        loaded = revision_token(self.data)
        write(self.data / STATE, dict(schema=1, scopes={'new_deployment': {}}))
        callback = Mock()
        self.assertFalse(save_if_current(self.data, loaded, callback))
        callback.assert_not_called()
        self.assertTrue(save_if_current(self.data, revision_token(self.data), callback))
        callback.assert_called_once()

    def test_reused_pid_is_not_terminated_and_unrelated_image_is_rejected(self):
        value = dict(pid=10, parent_pid=0, created=1, path=str(self.install / BOT))
        with patch.object(processes, 'alive', return_value=False), \
             patch.object(processes, 'terminate_stale_bots') as terminate:
            processes.reap_bots([value], str(self.install / BOT))
        terminate.assert_not_called()
        with self.assertRaises(ValueError):
            processes.reap_bots([dict(value, path=str(self.install / 'other.exe'))], str(self.install / BOT))

    def test_manifest_and_outer_zip_digest_both_required(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(MANIFEST, json.dumps(self.manifest))
            for name, raw in self.files.items():
                archive.writestr(name, raw)
        raw = stream.getvalue()
        package = self.folder / 'fixture.zip'
        package.write_bytes(raw)
        row = dict(version='5.6.0', size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        self.assertEqual(verify_archive(package, row), self.manifest)
        with self.assertRaises(ValueError):
            verify_archive(package, dict(row, sha256='0' * 64))
        self.assertGreater(version_key('5.6.0.fix2'), version_key('5.6.0.fix'))

    def test_escape_path_in_transaction_is_rejected(self):
        write(self.path, dict(self.job, destination=str(self.base / 'outside')))
        with self.assertRaises(ValueError):
            load_job(self.path)

    def test_disable_switch_never_downloads_or_starts_shutdown(self):
        from program_update_service import ProgramUpdateService
        service = object.__new__(ProgramUpdateService)
        service.updater = Mock()
        with patch('program_updater.UPDATE_ENABLED', False):
            with self.assertRaisesRegex(ValueError, '승인'):
                service.prepare()
            with self.assertRaisesRegex(ValueError, '승인'):
                service.begin(self.path)
        self.assertFalse(service.updater.method_calls)

    def test_source_execution_never_runs_the_automatic_full_updater(self):
        from ai_update_center import AiUpdateCenter
        center = object.__new__(AiUpdateCenter)
        center.app, center.root = Mock(), Mock()
        center._daily_full()
        center.app._get_program_update_service.assert_not_called()
        center.root.after.assert_called_once_with(60_000, center._daily_full)

    def package_fixture(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr(MANIFEST, json.dumps(self.manifest))
            for name, raw in self.files.items():
                archive.writestr(name, raw)
        raw = stream.getvalue()
        return raw, dict(version='5.6.0', size=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                         url='https://github.com/unit-fixture/never-accessed', name='bosstimer_update_5.6.0.zip')

    def test_prepare_validates_before_fencing_and_reuses_verified_download(self):
        guard.finish(self.data, self.ticket)
        raw, row = self.package_fixture()
        updater = ProgramUpdater(self.data, self.install, '5.5.0')
        with patch('program_updater.fetch_catalog', return_value=[row]), \
             patch('program_updater.open_response', return_value=io.BytesIO(raw)) as network:
            path = updater.prepare()
            again = updater.prepare()
        self.assertEqual(path, again)
        network.assert_called_once()
        self.assertEqual(read(path)['phase'], 'prepared')
        self.assertFalse(guard.active(self.data))
        self.assertFalse((self.install / POINTER).exists())

    def test_bad_download_keeps_current_version_and_does_not_start_maintenance(self):
        guard.finish(self.data, self.ticket)
        raw, row = self.package_fixture()
        updater = ProgramUpdater(self.data, self.install, '5.5.0')
        with patch('program_updater.fetch_catalog', return_value=[dict(row, sha256='0' * 64)]), \
             patch('program_updater.open_response', return_value=io.BytesIO(raw)), self.assertRaises(ValueError):
            updater.prepare()
        self.assertFalse(guard.active(self.data))
        self.assertFalse((self.install / POINTER).exists())

    def test_failed_version_is_skipped_automatically_but_manual_retry_is_available(self):
        guard.finish(self.data, self.ticket)
        raw, row = self.package_fixture()
        updater = ProgramUpdater(self.data, self.install, '5.5.0')
        write(updater.root / 'failed_versions.json', {'5.6.0': 'unit startup failure'})
        with patch('program_updater.fetch_catalog', return_value=[row]), \
             patch('program_updater.open_response', return_value=io.BytesIO(raw)) as network:
            self.assertIsNone(updater.prepare(automatic=True))
            network.assert_not_called()
            self.assertIsNotNone(updater.prepare())

    def test_daily_check_catches_up_once_after_three_am(self):
        updater = ProgramUpdater(self.data, self.install, '5.5.0')
        day = datetime(2026, 10, 17, 2, 59, tzinfo=KST)
        self.assertFalse(updater.claim_daily(day))
        self.assertTrue(updater.claim_daily(day + timedelta(minutes=1)))
        self.assertFalse(updater.claim_daily(day + timedelta(hours=6)))
        self.assertTrue(updater.claim_daily(day + timedelta(days=1, hours=6)))

    def test_web_stop_and_page_jobs_are_blocked_even_for_the_new_trial_gui(self):
        from web_service_manager import WebServiceManager
        manager = object.__new__(WebServiceManager)
        manager.root = self.data / 'web_service'
        manager.root.mkdir()
        manager._stop_owned_idle = Mock()
        guard.phase(self.data, self.ticket, 'verifying')
        with patch.dict(os.environ, {guard.ENV_TICKET: self.ticket}):
            self.assertFalse(manager._stop_owned())
        manager._stop_owned_idle.assert_not_called()
        import threading
        manager.lock = threading.RLock()
        job = dict(error=None, done=threading.Event())
        manager.page_update_job = job
        manager._page_update_jobs_locked = Mock()
        manager._page_update_jobs()
        manager._page_update_jobs_locked.assert_not_called()
        self.assertTrue(job['done'].is_set())
        self.assertTrue(job['error'])

    def test_begin_drains_bot_before_requesting_gui_exit_without_stopping_web(self):
        from program_update_service import ProgramUpdateService
        import program_update_service as service_module
        guard.finish(self.data, self.ticket)
        events = []
        root = NS(grab_current=lambda: None)
        manager = NS(page_updater=NS(status=lambda: dict(busy=False)),
                     standalone=NS(busy=False, stopping=False), status=lambda: dict(state='running'),
                     stop_web_verified=Mock(), close=Mock())
        app = NS(root=root, web_service_manager=manager, discord_bot_process=None, discord_bot_voice_channel_id='10',
                 _query_discord_bot_status_port=lambda **_kw: {}, _get_discord_bot_executable_path=lambda: str(self.install / BOT),
                 schedule_status_var=Mock(), _save_settings=lambda: events.append('save-settings'),
                 _save_schedule_alarm_settings=lambda: True, discord_handover=None,
                 on_close=lambda: events.append('gui-exit'), _show_centered_messagebox=Mock())
        def stop(**_kw):
            self.assertTrue(guard.active(self.data))
            events.append('stop-bot')
            return True
        app._stop_discord_bot_runtime_core = stop
        service = object.__new__(ProgramUpdateService)
        service.app, service.root, service.data = app, root, self.data
        service.version = '5.5.0'
        service.updater, service.active, service.events = NS(install=self.install), False, queue.Queue()
        service._open_progress = Mock()
        class Immediate:
            def __init__(self, target, **_kw): self.target = target
            def start(self): self.target()
        with patch.object(service_module.sys, 'frozen', True, create=True), \
             patch.object(service_module.sys, 'executable', str(self.install / GUI)), \
             patch.object(service_module.threading, 'Thread', Immediate), \
             patch.object(service_module, 'identity', return_value=self.job['parent']), \
             patch.object(service_module, 'capture_bots', return_value=[]), \
             patch.object(service_module, 'reap_bots', side_effect=lambda *_: events.append('verify-no-old-bot')), \
             patch.object(helper, 'spawn_helper', side_effect=lambda *_: events.append('helper-ready')):
            service.begin(self.path)
            service.events.get_nowait()()
        self.assertEqual(events, ['save-settings', 'stop-bot', 'verify-no-old-bot', 'helper-ready', 'gui-exit'])
        manager.stop_web_verified.assert_not_called()
        manager.close.assert_not_called()

    def test_source_execution_cannot_prepare_an_installation(self):
        from program_update_service import ProgramUpdateService
        service = object.__new__(ProgramUpdateService)
        service.updater = Mock()
        with self.assertRaisesRegex(ValueError, '배포 실행파일'):
            service.prepare()
        self.assertFalse(service.updater.method_calls)

    def test_restart_reaps_old_bot_then_starts_new_bot_and_adopts_web_after_commit(self):
        from program_update_service import ProgramUpdateService
        import program_update_service as module
        guard.phase(self.data, self.ticket, 'verifying')
        write(self.path, dict(self.job, resume=dict(bot=True, voice=False)))
        callbacks, events = [], []
        root = NS(after=lambda _ms, callback: callbacks.append(callback))
        app = NS(root=root, schedule_status_var=Mock(), discord_bot_process=NS(pid=30),
                 _start_discord_bot_runtime=Mock(side_effect=lambda **_kw: events.append('start-new-bot') or True),
                 _query_discord_bot_status_port=lambda **_kw: dict(ok=True, pid=30),
                 _discord_bot_status_matches=lambda _payload: True,
                 web_service_manager=NS(start_saved=lambda: events.append('adopt-web')))
        service = object.__new__(ProgramUpdateService)
        service.app, service.root, service.data, service.version = app, root, self.data, '5.6.0'
        service._open_progress, service._close_progress = Mock(), Mock()
        def process_identity(pid):
            return dict(pid=pid, parent_pid=0, created=2,
                        path=str(self.dest / (GUI if pid == os.getpid() else BOT)))
        with patch.dict(os.environ, {'BOSS_TIMER_UPDATE_JOB': str(self.path), guard.ENV_TICKET: self.ticket,
                                     guard.ENV_ATTEMPT: self.job['attempt']}), \
             patch.object(module.sys, 'executable', str(self.dest / GUI)), \
             patch.object(module, 'identity', side_effect=process_identity), \
             patch.object(module, 'capture_bots', return_value=[]), \
             patch.object(module, 'reap_bots', side_effect=lambda *_: events.append('reap-old-bot')):
            service.resume()
            callbacks.pop(0)()
            self.assertEqual(events, ['reap-old-bot', 'start-new-bot'])
            self.assertTrue((self.folder / 'ready.json').exists())
            self.assertTrue(app._program_update_startup_owned)
            guard.finish(self.data, self.ticket)
            callbacks.pop(0)()
        self.assertEqual(events[-1], 'adopt-web')
        self.assertFalse(app._program_update_restoring)

    def test_terminal_journal_can_clear_an_interrupted_final_fence(self):
        write(self.path, dict(self.job, phase='complete'))
        with patch.object(helper, 'identity', return_value=self.job['parent']), \
             patch.object(helper, 'wait_exited'), patch.object(helper, 'reap_bots'), \
             patch.object(helper, 'spawn_gui') as spawn:
            helper.apply(self.path)
        self.assertFalse(guard.active(self.data))
        spawn.assert_called_once_with(self.dest / GUI, str(self.install))

    def test_process_exit_signal_is_used_even_if_a_handle_still_retains_the_pid(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 100
        kernel.WaitForSingleObject.return_value = 0
        with patch.object(processes, '_kernel', return_value=kernel), \
             patch.object(processes, '_identity') as query:
            self.assertFalse(processes.alive(self.job['parent']))
        query.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(100)

    def test_access_denied_is_not_treated_as_process_exit(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 0
        with patch.object(processes, '_kernel', return_value=kernel), \
             patch.object(processes.ctypes, 'get_last_error', return_value=5), self.assertRaises(OSError):
            processes.alive(self.job['parent'])

    def test_package_selection_ignores_notice_web_and_preview_releases(self):
        from ai_module_updater import REPOSITORY
        def release(name):
            return dict(tag_name='v5.6.0', assets=[dict(name=name, size=10, digest='sha256:' + '0' * 64,
                browser_download_url=f'https://github.com/{REPOSITORY}/releases/download/v5.6.0/{name}')])
        full = release('bosstimer_update_5.6.0.zip')
        rows = release_rows([release('Update_AI_v1.0.0.zip'), release('bosstimer_web_update_1.0.0.zip'),
                             dict(full, prerelease=True), dict(full, draft=True), full])
        self.assertEqual([row['version'] for row in rows], ['5.6.0'])
        with self.assertRaises(ValueError):
            release_rows([full, full])

    def test_release_builder_uses_full_manifest_and_refuses_archive_overwrite_without_building(self):
        import build_release
        from program_update_package import filename
        with patch.object(build_release, 'DIST_DIR', self.install), \
             patch.object(build_release, 'GUI_EXE', self.install / GUI), \
             patch.object(build_release, 'BOT_EXE', self.install / BOT), \
             patch('program_update_package.digest_file', return_value='0' * 64), \
             patch.object(build_release.zipfile, 'ZipFile') as factory:
            path = build_release.create_release_zip('v5.6.0')
        self.assertEqual(path.name, filename('5.6.0'))
        self.assertEqual(factory.call_args.args[1], 'x')
        archive = factory.return_value.__enter__.return_value
        name, text = archive.writestr.call_args.args
        self.assertEqual(name, MANIFEST)
        self.assertEqual(set(json.loads(text)['files']), {GUI, BOT})
        self.assertFalse(path.exists())  # The archive writer was a mock, not a distributable build.

    def test_long_web_work_defers_full_update_without_blocking_bot_launches(self):
        guard.finish(self.data, self.ticket)
        manager = NS(root=self.data / 'web_service')
        with guard.web_change(manager):
            with guard.launch_gate(self.data):
                pass  # A web update does not hold the global spawn mutex.
            def check():
                if manager._program_update_work:
                    raise ValueError('web busy')
            with self.assertRaisesRegex(ValueError, 'web busy'):
                guard.begin(self.data, self.ticket, self.path, check=check)
        self.assertFalse(guard.active(self.data))
        self.assertEqual(manager._program_update_work, 0)

    def test_notice_module_is_not_loaded_until_full_update_commit(self):
        from notice_runtime import NoticeRuntime
        runtime = object.__new__(NoticeRuntime)
        runtime.session = None
        runtime.updater = NS(root=self.data / 'update_ai')
        runtime.start()  # Would access a missing bundle if it bypassed the fence.
        self.assertIsNone(runtime.session)

    def test_failed_helper_unblocks_original_gui_without_stopping_web(self):
        from program_update_service import ProgramUpdateService
        service = object.__new__(ProgramUpdateService)
        service.data, service._job_path = self.data, self.path
        service._helper, service.active = Mock(poll=Mock(return_value=1)), True
        service.app, service.root = Mock(), Mock()
        service._close_progress, service._resume_notice = Mock(), Mock()
        service._watch_helper()
        self.assertFalse(service.active)
        self.assertFalse(guard.active(self.data))
        self.assertEqual(read(self.path)['phase'], 'prepared')
        self.assertFalse(service.app.discord_bot_exit_requested)
        service.app._show_centered_messagebox.assert_called_once()
        service.app.web_service_manager.assert_not_called()

    def test_live_web_is_detached_before_bot_shutdown_and_missing_context_defers_update(self):
        from program_update_service import ProgramUpdateService
        service = object.__new__(ProgramUpdateService)
        root = self.data / 'web_service'
        root.mkdir()
        manager = NS(root=root, run_id=self.ticket, process=Mock(poll=Mock(return_value=None)),
                     public=NS(status=lambda: dict(state='prepared'), send=Mock()),
                     _flush_snapshot=Mock(), _flush_authority=Mock(), stop_web_verified=Mock())
        service.app = NS(web_service_manager=manager, web_handover_bridge=NS())
        with patch('web_persistent_runtime.save_background_context') as save:
            with self.assertRaises(FileNotFoundError):
                service._detach_web()
            manager.public.send.assert_not_called()
            write(root / ('background.' + self.ticket + '.json'), dict(schema=1, run_id=self.ticket))
            service._detach_web()
        self.assertEqual(save.call_count, 2)
        manager.public.send.assert_called_once_with(force=True, detach=True)
        manager.stop_web_verified.assert_not_called()

    def test_cancelled_helper_cannot_clear_or_apply_a_new_attempt_of_the_same_download(self):
        old_attempt = self.job['attempt']
        guard.finish(self.data, self.ticket, attempt=old_attempt)
        new_attempt = guard.begin(self.data, self.ticket, self.path)
        with self.assertRaises(ValueError):
            guard.finish(self.data, self.ticket, attempt=old_attempt)
        with self.assertRaises(ValueError), patch.object(helper, 'identity') as native:
            helper.apply(self.path)
        native.assert_not_called()
        self.assertTrue(guard.matches(self.data, self.ticket, new_attempt))
        self.assertFalse((self.install / POINTER).exists())

    def test_power_loss_before_transaction_arming_keeps_previous_program_selected(self):
        guard.finish(self.data, self.ticket)
        guard.begin(self.data, self.ticket, self.path, owner=self.job['parent'])
        write(self.path, dict(self.job, phase='prepared'))
        with patch.object(helper, 'alive', return_value=False), patch.object(helper, 'spawn_helper') as spawn:
            self.assertTrue(helper.recover_entry(guard.read(self.data)))
        spawn.assert_not_called()
        self.assertFalse(guard.active(self.data))
        self.assertFalse((self.install / POINTER).exists())

    def test_manual_replacement_of_entry_exe_is_not_routed_to_an_older_active_folder(self):
        import program_updater as module
        entry = self.install / GUI
        entry.write_bytes(b'old manually installed entry')
        write(self.install / POINTER, module.pointer_for(self.job))
        entry.write_bytes(b'new manually copied entry')
        with patch.object(module.sys, 'frozen', True, create=True), \
             patch.object(module.sys, 'executable', str(entry)), \
             patch.dict(os.environ, {module.INSTALL_ENV: str(self.install)}), \
             patch.object(module.guard, 'read', return_value=None), \
             patch.object(module, 'spawn_gui') as spawn:
            self.assertTrue(module.route_startup())
        spawn.assert_not_called()
        self.assertFalse((self.install / POINTER).exists())
        self.assertEqual(len(list(self.install.glob('program_active.manual_*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
