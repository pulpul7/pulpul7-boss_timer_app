"""GUI notice transport identity; fake processes only, no GUI/audio/network."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch


class NoticeOutputContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        method = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == '_get_notice_output_context')
        namespace = {'DISCORD_BOT_STATUS_PORT': 12345}
        exec(compile(ast.Module(body=[method], type_ignores=[]), '<notice-context>', 'exec'), namespace)
        cls.method = staticmethod(namespace[method.name])

    def app(self, launcher_pid=100, bot_pid=100):
        process = NS(pid=launcher_pid, poll=Mock(return_value=None)) if launcher_pid else None
        return NS(discord_bot_process=process, notice_output_secret='test-secret',
                  schedule_server_profile_id='8', current_season_no=18,
                  discord_bot_process_profile='profile.ini', discord_bot_running=True,
                  discord_bot_last_status_payload={'pid': bot_pid, 'ok': True, 'online': True,
                      'voice_connected': True, 'notice_opportunities': []},
                  _get_discord_bot_config_storage_path=Mock(return_value='profile.ini'),
                  _append_debug_log=Mock(),
                  _handover_audio_blocked=Mock(return_value=False),
                  _discord_bot_status_matches=Mock(return_value=True))

    def test_source_bot_context_uses_status_process_for_authenticated_request(self):
        app = self.app()
        context = self.method(app)
        self.assertEqual(context['identity'], ('8', '18', 100, 'test-secret'))
        with patch('notice_output_bridge.request_notice', return_value={'accepted': True}) as request:
            context['request']({'action': 'prepare', 'id': 'n'})
        self.assertEqual(request.call_args.args[2], 100)

    def test_onefile_bootloader_pid_does_not_block_actual_bot(self):
        app = self.app(launcher_pid=100, bot_pid=101)
        context = self.method(app)
        self.assertIsNotNone(context)
        self.assertEqual(context['identity'][2], 101)
        with patch('notice_output_bridge.request_notice', return_value={'accepted': True}) as request:
            context['request']({'action': 'prepare', 'id': 'n'})
        self.assertEqual(request.call_args.args[2], 101)

    def test_matching_adopted_bot_does_not_require_a_popen_object(self):
        app = self.app(launcher_pid=None, bot_pid=101)
        self.assertIsNotNone(self.method(app))
        app.discord_bot_running = False
        self.assertIsNone(self.method(app))

    def test_foreign_profile_or_missing_credentials_remain_blocked(self):
        for field, value in [('notice_output_secret', ''), ('discord_bot_process_profile', 'other.ini'),
                             ('discord_handover_busy', True)]:
            with self.subTest(field=field):
                app = self.app();setattr(app, field, value)
                self.assertIsNone(self.method(app))
        app = self.app();app._discord_bot_status_matches.return_value = False
        self.assertIsNone(self.method(app))
        app = self.app();app._handover_audio_blocked.return_value = True
        self.assertIsNone(self.method(app))

    def test_disconnected_or_invalid_status_stays_blocked(self):
        for values in ({'pid': 0}, {'pid': 'bad'}, {'voice_connected': False}, {'online': False}):
            with self.subTest(values=values):
                app = self.app();app.discord_bot_last_status_payload.update(values)
                self.assertIsNone(self.method(app))

    def test_managed_bot_exit_is_not_an_adoption(self):
        app = self.app();app.discord_bot_process.poll.return_value = 0
        self.assertIsNone(self.method(app))

    def test_changed_bot_or_handover_invalidates_existing_request(self):
        for change in ('pid', 'process', 'server', 'season', 'profile', 'handover', 'authorization', 'secret', 'voice'):
            with self.subTest(change=change):
                app = self.app(launcher_pid=100, bot_pid=101);context = self.method(app)
                self.assertIsNotNone(context)
                if change == 'pid':app.discord_bot_last_status_payload['pid'] = 202
                elif change == 'process':app.discord_bot_process = NS(pid=200, poll=lambda: None)
                elif change == 'server':app.schedule_server_profile_id = '9'
                elif change == 'season':app.current_season_no = 19
                elif change == 'profile':app._get_discord_bot_config_storage_path.return_value = 'other.ini'
                elif change == 'handover':app.discord_handover_busy = True
                elif change == 'authorization':app._discord_bot_status_matches.return_value = False
                elif change == 'secret':app.notice_output_secret = 'replacement-secret'
                else:app.discord_bot_last_status_payload['voice_connected'] = False
                with patch('notice_output_bridge.request_notice') as request:
                    with self.assertRaises(RuntimeError):context['request']({'action': 'play', 'id': 'n'})
                    request.assert_not_called()

    def test_old_scope_can_still_stop_after_handover(self):
        app = self.app(launcher_pid=100, bot_pid=101);context = self.method(app)
        app.discord_handover_busy = True
        with patch('notice_output_bridge.request_notice', return_value={'accepted': True}) as request:
            context['request']({'action': 'stop', 'id': 'n'})
        self.assertEqual(request.call_args.args[2], 101)

    def test_dead_owned_bot_stop_needs_no_http_request(self):
        app = self.app();context = self.method(app)
        app.discord_bot_process.poll.return_value = 0
        with patch('notice_output_bridge.request_notice') as request:
            result = context['request']({'action': 'stop', 'id': 'n'})
        self.assertTrue(result['accepted']);request.assert_not_called()

    def test_specific_wait_reason_logs_only_on_change_without_credentials(self):
        app = self.app();app.discord_bot_last_status_payload['voice_connected'] = False
        self.assertIsNone(self.method(app))
        self.assertEqual(app.notice_output_wait_reason, '음성 채널 연결 대기')
        self.method(app)
        app._append_debug_log.assert_called_once()
        self.assertNotIn(app.notice_output_secret, str(app._append_debug_log.call_args))
        app.discord_bot_last_status_payload['voice_connected'] = True
        self.assertIsNotNone(self.method(app))
        self.assertEqual(app.notice_output_wait_reason, '')


if __name__ == '__main__':
    unittest.main()
