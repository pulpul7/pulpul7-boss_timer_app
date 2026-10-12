"""Offline recovery checks; never launch or terminate actual processes."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import Mock, patch

import discord_bot_process_recovery as recovery


class ProcessSelectionTests(unittest.TestCase):
    def setUp(self):
        self.outer = recovery.BotProcess(10, 1, r"G:\old\boss_timer_discord_bot.exe", 100)
        self.inner = recovery.BotProcess(11, 10, self.outer.path, 110)
        self.bots = {10: self.outer, 11: self.inner}

    def select(self, live=None, parent=None, **kwargs):
        return recovery.select_stale_bots(self.bots, live or {10, 11},
            lambda _: parent, current_pid=99, **kwargs)

    def test_orphan_onefile_child_and_bootloader_are_reclaimed_in_order(self):
        self.assertEqual(self.select(), [self.inner, self.outer])

    def test_listener_in_old_folder_reclaims_entire_orphan_family(self):
        self.assertEqual(self.select(payload=dict(ok=True, pid=11)), [self.inner, self.outer])

    def test_other_live_gui_is_untouched_even_with_same_settings(self):
        parent = recovery.BotProcess(1, 0, 'gui.exe', 90)
        self.assertEqual(self.select({1, 10, 11}, parent,
            payload=dict(ok=True, pid=11, client_id='a'), client_id='a'), [])

    def test_access_denied_to_parent_is_not_treated_as_orphan(self):
        self.assertEqual(self.select({1, 10, 11}), [])

    def test_recycled_parent_pid_does_not_protect_an_orphan(self):
        self.assertEqual(self.select({1, 10, 11}, recovery.BotProcess(1, 0, 'other.exe', 200)),
                         [self.inner, self.outer])

    def test_unrelated_listener_is_not_a_reason_to_kill_orphan(self):
        self.assertEqual(self.select(payload=dict(ok=True, pid=20)), [])
        self.assertEqual(self.select(payload=dict(ok=True, pid='invalid')), [])

    def test_current_launch_without_listener_is_left_to_start_normally(self):
        self.assertEqual(self.select(owned_pid=10), [])

    def test_current_parent_requires_explicit_replacement_and_identity_match(self):
        self.outer = recovery.BotProcess(10, 99, self.outer.path, 100)
        self.bots[10] = self.outer
        parent = recovery.BotProcess(99, 0, 'gui.exe', 90)
        args = dict(payload=dict(ok=True, pid=11, client_id='a'), client_id='a', owned_pid=10)
        self.assertEqual(self.select({99, 10, 11}, parent, **args), [])
        self.assertEqual(self.select({99, 10, 11}, parent,
            allow_current_parent=True, **args), [self.inner, self.outer])
        args['client_id'] = 'b'
        self.assertEqual(self.select({99, 10, 11}, parent,
            allow_current_parent=True, **args), [])

    def test_snapshot_does_not_accept_generic_bot_in_unrelated_folder(self):
        kernel = Mock()
        with patch.object(recovery.os, 'name', 'nt'), patch.object(recovery, '_kernel', return_value=kernel), \
             patch.object(recovery, '_snapshot', return_value={10: (1, 'discord_bot.exe')}), \
             patch.object(recovery, '_inspect', return_value=recovery.BotProcess(10, 1, r'G:\other\discord_bot.exe', 10)):
            self.assertEqual(recovery.find_stale_bots(expected_executable=r'C:\current\discord_bot.exe'), [])


class TerminationTests(unittest.TestCase):
    def setUp(self):
        self.process = recovery.BotProcess(10, 1, r'G:\old\boss_timer_discord_bot.exe', 100)
        self.kernel = Mock()
        self.kernel.OpenProcess.return_value = 123
        self.kernel.TerminateProcess.return_value = True
        self.kernel.WaitForSingleObject.side_effect = [258, 0]

    def terminate(self, identity):
        with patch.object(recovery, '_kernel', return_value=self.kernel), \
             patch.object(recovery, '_identity', return_value=identity):
            return recovery.terminate_stale_bots([self.process])

    def test_pid_reuse_and_wrong_executable_cannot_be_terminated(self):
        self.assertFalse(self.terminate((self.process.path, 200)))
        self.assertFalse(self.terminate(('other.exe', 100)))
        self.kernel.TerminateProcess.assert_not_called()
        self.assertEqual(self.kernel.CloseHandle.call_count, 2)

    def test_process_is_waited_and_handle_is_closed(self):
        self.assertTrue(self.terminate((self.process.path, 100)))
        self.kernel.TerminateProcess.assert_called_once_with(123, 0)
        self.kernel.WaitForSingleObject.assert_any_call(123, 3000)
        self.kernel.CloseHandle.assert_called_once_with(123)

    def test_exit_timeout_is_not_reported_as_success(self):
        self.kernel.WaitForSingleObject.side_effect = [258, 258]
        self.assertFalse(self.terminate((self.process.path, 100)))

    def test_access_denied_never_reports_old_bot_as_stopped(self):
        self.kernel.OpenProcess.return_value = 0
        with patch.object(recovery.ctypes, 'get_last_error', return_value=5, create=True):
            self.assertFalse(self.terminate((self.process.path, 100)))
        self.kernel.TerminateProcess.assert_not_called()


class GuiRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Extract two methods only. Importing the GUI would start Windows UI/audio.
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        names = {'_queue_discord_bot_stale_recovery', '_start_discord_bot_runtime'}
        methods = [node for owner in tree.body if isinstance(owner, ast.ClassDef)
                   for node in owner.body if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = dict(threading=threading, tk=NS(TclError=RuntimeError),
                         CONTROL_PROTOCOL='current', CONTROL_TRANSPORT='events')
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<gui-recovery>', 'exec'), namespace)
        cls.namespace = namespace

    def app(self):
        callbacks, workers = [], []
        app = NS(discord_bot_process=None, schedule_status_var=Mock(),
            _get_discord_bot_config_storage_path=Mock(return_value='profile.ini'),
            _get_administrator_identity=Mock(return_value={'client_id': 'a'}),
            _get_discord_bot_executable_path=Mock(return_value='bot.exe'),
            _owns_discord_bot_child=Mock(return_value=False),
            _append_debug_log=Mock(), _start_discord_bot_runtime=Mock(return_value=True),
            _wait_discord_bot_status_port_down=Mock(return_value=True),
            _stop_discord_bot_runtime_core=Mock(return_value=True),
            root=NS(after=lambda _, callback: callbacks.append(callback)))
        app._queue_discord_bot_stale_recovery = self.namespace['_queue_discord_bot_stale_recovery'].__get__(app)
        def thread(**kw):
            return NS(start=lambda: workers.append(kw['target']))
        return app, callbacks, workers, thread

    def queued(self, termination=True):
        app, callbacks, workers, thread = self.app()
        with patch.object(recovery, 'find_stale_bots', return_value=[NS(pid=10)]), \
             patch.object(recovery, 'terminate_stale_bots', return_value=termination), \
             patch.object(threading, 'Thread', side_effect=thread):
            self.assertTrue(app._queue_discord_bot_stale_recovery({}, automatic=True))
            self.assertTrue(app._queue_discord_bot_stale_recovery({}, automatic=True))
        self.assertEqual(len(workers), 1)
        self.assertEqual(callbacks, [])
        return app, callbacks, workers

    def test_cleanup_is_background_and_retries_gateway_only_once(self):
        app, callbacks, workers = self.queued()
        workers[0]()
        app._start_discord_bot_runtime.assert_not_called()
        callbacks[0]()
        app._start_discord_bot_runtime.assert_called_once_with(
            gateway_only=True, automatic=True, _stale_recovery_attempted=True)
        self.assertFalse(app.discord_bot_stale_recovery_in_progress)

    def test_failed_cleanup_never_launches_duplicate_bot(self):
        app, callbacks, workers = self.queued(termination=False)
        workers[0]()
        callbacks[0]()
        app._start_discord_bot_runtime.assert_not_called()
        app._wait_discord_bot_status_port_down.assert_not_called()

    def test_exit_and_profile_change_cancel_delayed_restart(self):
        for exiting in (True, False):
            app, callbacks, workers = self.queued()
            workers[0]()
            app.discord_bot_exit_requested = exiting
            if not exiting:
                app._get_discord_bot_config_storage_path.return_value = 'new-profile.ini'
            callbacks[0]()
            app._start_discord_bot_runtime.assert_not_called()

    def test_offline_current_child_is_not_killed_during_normal_startup(self):
        app, callbacks, workers, thread = self.app()
        app._owns_discord_bot_child.return_value = True
        with patch.object(recovery, 'find_stale_bots') as scan:
            self.assertFalse(app._queue_discord_bot_stale_recovery(dict(ok=True, pid=10), orphan_only=True))
        scan.assert_not_called()
        self.assertEqual(workers, [])

    def test_unidentifiable_or_uninspectable_process_does_not_start_cleanup(self):
        app, callbacks, workers, _ = self.app()
        with patch.object(recovery, 'find_stale_bots', return_value=[]):
            self.assertFalse(app._queue_discord_bot_stale_recovery({'ok': True}))
        with patch.object(recovery, 'find_stale_bots', side_effect=OSError('access denied')):
            self.assertFalse(app._queue_discord_bot_stale_recovery({'ok': True}))
        self.assertEqual(workers, [])
        app._stop_discord_bot_runtime_core.assert_not_called()

    def test_healthy_bot_reuse_does_not_scan_or_stop_any_process(self):
        app = NS(_apply_discord_bot_settings_to_runtime=Mock(), _load_discord_bot_settings=Mock(),
            _get_discord_bot_settings_validation_error=Mock(return_value=''),
            _stop_discord_settings_warning=Mock(),
            _query_discord_bot_status_port=Mock(return_value=dict(ok=True, online=True)),
            _discord_bot_status_matches=Mock(return_value=True),
            _queue_discord_bot_stale_recovery=Mock(),
            _get_discord_bot_config_storage_path=Mock(return_value='profile.ini'),
            _set_discord_bot_status_payload=Mock(), _ensure_discord_authority=Mock(),
            _schedule_discord_bot_status_poll=Mock(),
            discord_bot_token='token', discord_bot_application_id='1', discord_bot_server_id='2',
            discord_bot_voice_channel_id='3', discord_bot_text_channel_id='4')
        self.namespace['Path'] = Mock(return_value=NS(read_text=Mock(side_effect=OSError())))
        self.assertTrue(self.namespace['_start_discord_bot_runtime'](app, automatic=True, gateway_only=True))
        app._queue_discord_bot_stale_recovery.assert_not_called()
        self.assertTrue(app.discord_bot_expected_running)

    def test_automatic_mismatch_queues_recovery_but_second_attempt_cannot_loop(self):
        app = NS(_apply_discord_bot_settings_to_runtime=Mock(), _load_discord_bot_settings=Mock(),
            _get_discord_bot_settings_validation_error=Mock(return_value=''),
            _stop_discord_settings_warning=Mock(), _query_discord_bot_status_port=Mock(return_value=dict(ok=True)),
            _discord_bot_status_matches=Mock(return_value=False),
            _queue_discord_bot_stale_recovery=Mock(return_value=True), schedule_status_var=Mock(),
            discord_bot_token='token', discord_bot_application_id='1', discord_bot_server_id='2',
            discord_bot_voice_channel_id='3', discord_bot_text_channel_id='4')
        start = self.namespace['_start_discord_bot_runtime']
        self.assertTrue(start(app, automatic=True, gateway_only=True))
        app._queue_discord_bot_stale_recovery.assert_called_once_with({'ok': True}, automatic=True)
        app._queue_discord_bot_stale_recovery.reset_mock()
        self.assertFalse(start(app, automatic=True, gateway_only=True, _stale_recovery_attempted=True))
        app._queue_discord_bot_stale_recovery.assert_not_called()


if __name__ == '__main__':
    unittest.main()
