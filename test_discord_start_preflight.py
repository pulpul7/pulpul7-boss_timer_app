"""Exercise the real GUI launch method without opening Tk or contacting Discord."""
import ast
from pathlib import Path
from types import SimpleNamespace, MethodType
import unittest
from unittest.mock import Mock, patch


class StartPreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = ast.parse(Path(__file__).with_name('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        selected = []
        for name in ('_start_discord_bot_runtime', '_get_discord_bot_settings_validation_error',
                     '_apply_discord_settings_button_style', '_stop_discord_settings_warning',
                     '_start_discord_settings_warning', '_tick_discord_settings_warning'):
            node = next(n for n in ast.walk(source) if isinstance(n, ast.FunctionDef) and n.name == name)
            node.decorator_list = []
            selected.append(node)
        namespace = {'tk': SimpleNamespace(TclError=RuntimeError)}
        exec(compile(ast.Module(body=selected, type_ignores=[]), 'gui-launch-test', 'exec'), namespace)
        cls.launch = staticmethod(namespace['_start_discord_bot_runtime'])
        cls.validate = staticmethod(namespace['_get_discord_bot_settings_validation_error'])
        cls.warning_helpers = {key: value for key, value in namespace.items()
                               if key.startswith('_') and key not in ('_start_discord_bot_runtime', '_get_discord_bot_settings_validation_error', '__builtins__')}

    def app(self, **values):
        settings = dict(token='test-token', application_id='123', server_id='456', voice_channel_id='789', text_channel_id='')
        settings.update(values)
        app = SimpleNamespace(root=Mock(), schedule_window=None, current_season_no='18',
            discord_bot_server_id='stale', schedule_status_var=Mock(),
            _show_centered_messagebox=Mock(), _get_discord_bot_launch_command=Mock(),
            _ensure_administrator_name=Mock(return_value=True),
            _start_discord_settings_warning=Mock(), _stop_discord_settings_warning=Mock(),
            _get_discord_bot_config_storage_path=Mock(return_value='test.ini'),
            _get_current_github_upload_server_entry=Mock(return_value={'id': 'odin9'}),
            _load_discord_bot_settings=Mock(return_value=settings),
            _get_discord_bot_settings_validation_error=self.validate)
        def apply(payload):
            for key, value in payload.items():
                setattr(app, 'discord_bot_' + key, value)
        app._apply_discord_bot_settings_to_runtime = apply
        return app

    def test_missing_settings_never_create_handover_or_launch_process(self):
        for key in ('token', 'application_id', 'server_id', 'voice_channel_id'):
            with self.subTest(key=key), patch('discord_handover.DiscordHandover') as coordinator:
                app = self.app(**{key: ''})
                self.assertFalse(self.launch(app))
                coordinator.assert_not_called()
                app._get_discord_bot_launch_command.assert_not_called()
                app._show_centered_messagebox.assert_not_called()
                app._ensure_administrator_name.assert_not_called()
                app._start_discord_settings_warning.assert_called_once()
                self.assertFalse(app.discord_bot_expected_running)
                self.assertIn('미입력', app.schedule_status_var.set.call_args.args[0])

    def test_saved_settings_are_loaded_before_coordinator_construction(self):
        app = self.app()
        with patch('discord_handover.DiscordHandover') as coordinator:
            coordinator.side_effect = lambda loaded: SimpleNamespace(start=Mock(), guild=loaded.discord_bot_server_id)
            self.assertTrue(self.launch(app))
            self.assertEqual(app.discord_handover.guild, '456')
            app.discord_handover.start.assert_called_once()
            app._stop_discord_settings_warning.assert_called_once()

    def test_automatic_invalid_settings_do_not_open_popup(self):
        app = self.app(token='')
        self.assertFalse(self.launch(app, automatic=True))
        app._show_centered_messagebox.assert_not_called()

    def test_name_registration_cancel_does_not_start_handover(self):
        app = self.app()
        app._ensure_administrator_name.return_value = False
        with patch('discord_handover.DiscordHandover') as coordinator:
            self.assertFalse(self.launch(app))
            coordinator.assert_not_called()
        app._get_discord_bot_launch_command.assert_not_called()

    def test_optional_chat_empty_is_allowed_and_all_missing_fields_are_named(self):
        good = dict(token='test', application_id='123', server_id='456', voice_channel_id='789', text_channel_id='')
        self.assertEqual(self.validate(**good), '')
        error = self.validate(**{key: '' for key in good})
        for label in ('봇 토큰', 'Application ID', '서버 ID', '음성채널 ID'):
            self.assertIn(label, error)
        self.assertNotIn('안내채팅', error)

    def test_whitespace_and_bad_ids_block_without_popups(self):
        for values in (dict(token=' \t '), dict(application_id='abc'), dict(server_id='abc'),
                       dict(voice_channel_id='abc'), dict(text_channel_id='abc')):
            with self.subTest(values=values), patch('discord_handover.DiscordHandover') as coordinator:
                app = self.app(**values)
                self.assertFalse(self.launch(app))
                coordinator.assert_not_called()
                app._show_centered_messagebox.assert_not_called()
                app._get_discord_bot_launch_command.assert_not_called()
                app._start_discord_settings_warning.assert_called_once()

    def warning_app(self):
        pending = {}
        sequence = iter(range(100))
        def after(delay, callback):
            key = next(sequence)
            pending[key] = callback
            return key
        app = SimpleNamespace(root=SimpleNamespace(after=after, after_cancel=lambda key: pending.pop(key, None)),
                              schedule_window_open=True, discord_bot_settings_button=Mock(),
                              _widget_available=lambda widget: widget is not None)
        for name, method in self.warning_helpers.items():
            setattr(app, name, MethodType(method, app))
        return app, pending

    def test_warning_repeated_clicks_keep_one_timer_and_hover_keeps_warning(self):
        app, pending = self.warning_app()
        app._start_discord_settings_warning()
        self.assertEqual(app.discord_bot_settings_button.configure.call_args.kwargs['bg'], '#dc2626')
        app._apply_discord_settings_button_style(hover=True)
        self.assertEqual(app.discord_bot_settings_button.configure.call_args.kwargs['bg'], '#dc2626')
        app._start_discord_settings_warning()
        self.assertEqual(len(pending), 1)
        pending.pop(app.discord_bot_settings_warning_after_id)()
        self.assertEqual(app.discord_bot_settings_button.configure.call_args.kwargs['bg'], '#fbbf24')
        app._stop_discord_settings_warning()
        self.assertFalse(pending)
        self.assertEqual(app.discord_bot_settings_button.configure.call_args.kwargs['bg'], '#475569')

    def test_closed_or_destroyed_window_stops_warning(self):
        for change in ('closed', 'destroyed'):
            app, pending = self.warning_app()
            app._start_discord_settings_warning()
            if change == 'closed':
                app.schedule_window_open = False
            else:
                app.discord_bot_settings_button = None
            pending.pop(app.discord_bot_settings_warning_after_id)()
            self.assertFalse(pending)
            self.assertFalse(app.discord_bot_settings_warning_active)

    def test_game_server_change_replaces_stale_coordinator(self):
        app = self.app()
        previous = SimpleNamespace(alive=True, profile='test.ini',
            scope=dict(guild='456', season='18', server='odin8'), start=Mock())
        app.discord_handover = previous
        with patch('discord_handover.DiscordHandover') as coordinator:
            self.assertTrue(self.launch(app))
            self.assertFalse(previous.alive)
            previous.start.assert_not_called()
            coordinator.return_value.start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
