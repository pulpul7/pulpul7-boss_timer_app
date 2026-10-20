"""Tray lifecycle/explicit-stop tests; no live service, account or visible test UI."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from web_manual_stop import (manual_stop, request_manual_stop, complete_manual_stop,
                             clear_manual_stop)
from web_service_common import read_json, write_json
from web_service_manager import WebServiceManager
from web_persistent_runtime import WorkerRuntime
from web_worker_tray import WebWorkerTray, CALLBACK, _api

RUN, CLIENT, SESSION, WEB = ('a'*32, 'b'*32, 'c'*32, 'd'*32)


class TrayStopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='boss-web-tray-unit-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'web_service'
        self.root.mkdir()
        write_json(self.root/'persistent_worker.json', dict(run_id=RUN, pid=23456))

    def manager(self):
        manager = WebServiceManager(self.root.parent)
        manager._log = Mock()
        manager._ensure_coordinator = Mock()
        self.addCleanup(manager.close)
        return manager

    def test_stale_worker_cannot_stop_new_worker(self):
        request_manual_stop(self.root, RUN, 23456)
        self.assertIsNotNone(manual_stop(self.root))
        write_json(self.root/'persistent_worker.json', dict(run_id='e'*32, pid=23456))
        self.assertIsNone(manual_stop(self.root))
        with self.assertRaises(ValueError):
            request_manual_stop(self.root, RUN, 23456)
        self.assertTrue(self.manager().settings['enabled'])

    def test_restart_respects_stop_and_explicit_start_clears_it(self):
        write_json(self.root/'settings.json', dict(enabled=True, port=8765, external_port=443))
        protected = {name: b'unchanged' for name in ('schedule.json', 'access_settings.json', 'push_subscriptions.json')}
        for name, content in protected.items():
            (self.root/name).write_bytes(content)
        request_manual_stop(self.root, RUN, 23456)
        complete_manual_stop(self.root, RUN, shared_confirmed=True)
        manager = self.manager()
        manager._request = Mock()
        manager.page_updater.start = Mock()
        manager.start_saved()
        manager._request.assert_not_called()
        self.assertFalse(manager.status()['enabled'])
        self.assertFalse(manager.has_web_authority())
        with self.assertRaises(ValueError):
            manager.set_enabled(True)
        self.assertEqual(read_json(manager.settings_path)['external_port'], 443)
        from web_standalone_hosting import StandaloneHosting
        manager.standalone = StandaloneHosting(NS(manager=manager, app=NS()))
        manager.ddns_status = Mock(return_value=dict(configured=True))
        with patch('web_standalone_hosting.threading.Thread') as thread:
            manager.start_web()
            thread.return_value.start.assert_called_once()
            self.assertEqual(thread.call_args.kwargs['kwargs'], dict(force=False, approved=False))
        self.assertIsNone(manual_stop(self.root))
        self.assertFalse(manager.tray_shutdown)
        for name, content in protected.items():
            self.assertEqual((self.root/name).read_bytes(), content)

    def test_live_gui_does_not_treat_tray_exit_as_worker_failure(self):
        manager = self.manager()
        manager.run_id = RUN
        manager.process = Mock(pid=23456)
        manager.process.poll.return_value = None
        manager.background_protected = True
        manager._fail = Mock()
        request_manual_stop(self.root, RUN, 23456)
        manager._monitor()
        self.assertEqual(manager.status()['state'], 'stopping')
        self.assertFalse(manager.desired)
        self.assertFalse(manager.background_protected)
        self.assertFalse(manager.has_web_authority())
        manager.process.poll.return_value = 0
        manager._monitor()
        self.assertEqual(manager.status()['state'], 'stopped')
        manager._fail.assert_not_called()
        manager.process.terminate.assert_not_called()
        manager.process = None

    def test_lease_renewal_cannot_reopen_access_before_monitor_sees_stop(self):
        manager = self.manager()
        manager.run_id = RUN
        manager.discovery_pending = False
        manager.desired = True
        manager.publication_allowed = Mock(return_value=True)
        request_manual_stop(self.root, RUN, 23456)
        manager._flush_authority(force=True)
        self.assertFalse(read_json(self.root/'authority_lease.json')['enabled'])

    def test_start_waits_for_cleanup_and_missing_pid_allows_recovery(self):
        request_manual_stop(self.root, RUN, 23456)
        process = Mock(); process.poll.return_value = None
        with patch('web_persistent_runtime.AttachedProcess', return_value=process):
            with self.assertRaises(ValueError):
                clear_manual_stop(self.root)
        process.close.assert_called_once()
        process.terminate.assert_not_called()
        self.assertIsNotNone(manual_stop(self.root))
        error = OSError('not found'); error.winerror = 87
        with patch('web_persistent_runtime.AttachedProcess', side_effect=error):
            clear_manual_stop(self.root)
        self.assertIsNone(manual_stop(self.root))

    def test_rejected_start_keeps_manual_stop_fence(self):
        from web_standalone_hosting import StandaloneHosting
        request_manual_stop(self.root, RUN, 23456)
        complete_manual_stop(self.root, RUN, shared_confirmed=True)
        manager = self.manager()
        manager.standalone = StandaloneHosting(NS(manager=manager, app=NS()))
        manager.ddns_status = Mock(return_value=dict(configured=False))
        with self.assertRaises(ValueError):
            manager.start_web()
        self.assertIsNotNone(manual_stop(self.root))
        manager.ddns_status.return_value = dict(configured=True)
        with patch('program_update_guard.launch_gate', side_effect=ValueError('maintenance')):
            with self.assertRaises(ValueError):
                manager.start_web()
        self.assertTrue(manager.tray_shutdown)
        self.assertIsNotNone(manual_stop(self.root))

    def test_coordinator_leaves_worker_cleanup_in_charge_without_force_kill(self):
        manager = self.manager()
        manager.run_id = RUN
        manager.process = Mock(pid=23456); manager.process.poll.return_value = None
        manager.discovery_pending = False
        manager.preserve_worker = True
        manager._stop_owned = Mock()
        manager._monitor = Mock()
        manager.wake = Mock()
        manager.wake.wait.side_effect = lambda _: manager.closed.set()
        request_manual_stop(self.root, RUN, 23456)
        manager._coordinate()
        manager._stop_owned.assert_not_called()
        manager._monitor.assert_not_called()
        self.assertFalse(manager.desired)
        self.assertEqual(manager.status()['state'], 'stopping')
        manager.process = None

    def test_delayed_previous_host_restore_is_cancelled_by_tray_stop(self):
        import test_web_standalone_hosting as fixtures
        fixture = fixtures.StandaloneTests()
        standalone, manager, coordinator, _ = fixture.node()
        manager.tray_shutdown = True
        before = deepcopy(fixture.repo.data)
        standalone._restore_previous_locked(coordinator, WEB, dict(owner=CLIENT, runtime=SESSION))
        self.assertEqual(fixture.repo.data, before)
        manager.emergency_stop.assert_not_called()

    def runtime(self):
        runtime = WorkerRuntime(self.root, RUN, None, lambda: {})
        for name in ('public', 'ddns', 'certificate'):
            setattr(runtime, name, NS(thread=None))
        runtime._load_config = Mock(return_value=dict(client=CLIENT, runtime=SESSION, web_id=WEB))
        runtime.record = Mock()
        data = dict(scope=dict(guild='1', server='9', season='18'), owner='voice-owner',
                    owner_runtime='voice-session', schedule={'revision': 50},
                    web_background={}, web_return_pending={},
                    web_standalone=dict(protocol=1, id=WEB, owner=CLIENT, runtime=SESSION,
                                        guild='1', server='9', season='18', state='active'))
        runtime.record.read.return_value = (data, 'sha')
        return runtime, data

    def test_shared_ack_keeps_schedule_and_discord_owner(self):
        runtime, data = self.runtime()
        runtime.closed.set()
        original = deepcopy(data)
        self.assertTrue(runtime.acknowledge_manual_stop())
        saved, sha = runtime.record.write.call_args.args
        self.assertEqual(saved['web_standalone']['state'], 'stopped')
        self.assertEqual(saved['members'][CLIENT]['web_closed_runtime'], SESSION)
        for key in ('schedule', 'owner', 'owner_runtime'):
            self.assertEqual(saved[key], original[key])
        self.assertNotIn('web_background', saved)
        self.assertNotIn('web_return_pending', saved)

    def test_ack_never_overwrites_new_host_and_does_not_claim_unclean_stop(self):
        runtime, data = self.runtime()
        data['web_standalone']['id'] = 'e'*32
        self.assertTrue(runtime.acknowledge_manual_stop())
        runtime.record.write.assert_not_called()
        runtime.public.thread = Mock(); runtime.public.thread.is_alive.return_value = True
        self.assertFalse(runtime.acknowledge_manual_stop())
        runtime.record.write.assert_not_called()

    def test_offline_ack_fails_without_changing_local_stop_intent(self):
        runtime, _ = self.runtime()
        runtime.record.read.side_effect = OSError('offline')
        request_manual_stop(self.root, RUN, 23456)
        result = runtime.acknowledge_manual_stop()
        complete_manual_stop(self.root, RUN, shared_confirmed=result)
        self.assertFalse(result)
        self.assertFalse(manual_stop(self.root)['shared_confirmed'])
        self.assertTrue(manual_stop(self.root)['completed'])

    def test_worker_tray_shutdown_cleans_up_before_shared_ack(self):
        from web_service_worker import main
        objects = {}
        for name in ('LocalWebServer', 'ScheduleCache', 'AccessRuntime', 'HTTPSListener', 'LiveWebAssets'):
            objects[name] = Mock()
        objects['LiveWebAssets'].status.return_value = {}
        objects['AccessRuntime'].page_refresh.status.return_value = {}
        objects['LiveWebAssets'].snapshot.files = {}
        tray = WebWorkerTray(None)
        tray.request_stop()
        tray.start = Mock(); tray.close = Mock()
        runtime = Mock()
        runtime.status.return_value = dict(runtime_protocol=1)
        def ack():
            for name, method in (('LocalWebServer', 'server_close'), ('HTTPSListener', 'close')):
                getattr(objects[name], method).assert_called_once()
            runtime.close.assert_called_once()
            return True
        runtime.acknowledge_manual_stop.side_effect = ack
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name, obj in objects.items():
                stack.enter_context(patch('web_service_worker.'+name, return_value=obj))
            stack.enter_context(patch('web_service_worker.WebWorkerTray', return_value=tray))
            stack.enter_context(patch('web_persistent_runtime.WorkerRuntime', return_value=runtime))
            stack.enter_context(patch('web_push.PushRuntime'))
            result = main(['--root', str(self.root), '--run-id', RUN, '--parent-pid', '1',
                           '--port', '8765', '--persistent'])
        self.assertEqual(result, 0)
        self.assertTrue(manual_stop(self.root)['completed'])
        state = read_json(self.root/('runtime_'+RUN+'.json'))
        self.assertEqual(state['stop_reason'], 'system_tray')
        self.assertEqual(state['state'], 'stopped')
        tray.close.assert_called_once()


class NativeTrayTests(unittest.TestCase):
    def test_duplicate_menu_request_is_one_shot_and_close_never_stops_worker(self):
        tray = WebWorkerTray(None)
        tray._post = Mock()
        tray.request_stop(); tray.request_stop()
        self.assertTrue(tray.stop_requested.is_set())
        tray._post.assert_called_once()
        tray.retry_stop()
        self.assertFalse(tray.stop_requested.is_set())
        tray.close()
        tray.request_stop()
        self.assertFalse(tray.stop_requested.is_set())

    @unittest.skipUnless(os.name == 'nt', 'Windows native message loop')
    def test_native_window_messages_menu_and_explorer_recreation_without_visible_icon(self):
        import ctypes
        from ctypes import wintypes as w
        calls = []
        restored = threading.Event()
        def notify(operation, pointer):
            calls.append(operation)
            if calls.count(0) == 2:
                restored.set()
            return True
        def api(dll, name, result, *args):
            if name == 'Shell_NotifyIconW':
                return notify
            if name == 'TrackPopupMenu':
                return Mock(return_value=1)
            if name == 'SetForegroundWindow':
                return Mock(return_value=True)
            return _api(dll, name, result, *args)
        tray = WebWorkerTray(None)
        with patch('web_worker_tray._api', side_effect=api):
            try:
                tray.start()
                self.assertTrue(tray.ready.wait(3))
                self.assertIsNone(tray.error)
                self.assertIsNotNone(tray.hwnd)
                user = ctypes.WinDLL('user32', use_last_error=True)
                taskbar = _api(user, 'RegisterWindowMessageW', w.UINT, w.LPCWSTR)('TaskbarCreated')
                tray._post(taskbar)
                self.assertTrue(restored.wait(3))
                tray.post(tray.hwnd, CALLBACK, 0, 0x7b)
                self.assertTrue(tray.stop_requested.wait(3))
            finally:
                tray.close()
        self.assertFalse(tray.thread.is_alive())
        self.assertIsNone(tray.hwnd)
        self.assertGreaterEqual(calls.count(0), 2)
        self.assertIn(4, calls)  # Version-4 callback protocol.
        self.assertIn(2, calls)  # Notification removed on close.


if __name__ == '__main__':
    unittest.main()
