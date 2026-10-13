"""Display and local polling checks without Tk windows, processes or network."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from discord_connection_progress import DiscordConnectionProgress, show_connection_progress


class ProgressTests(unittest.TestCase):
    def popup(self):
        popup = object.__new__(DiscordConnectionProgress)
        popup.role, popup.token, popup.finished = 'incoming', 'token', False
        popup.position, popup.skipped = -1, set()
        popup.rows = {key: Mock() for key, _ in popup.STEPS['incoming']}
        popup.header, popup.detail, popup.peer, popup.bar = Mock(), Mock(), Mock(), Mock()
        popup.window = NS(after=Mock(return_value='close-job'))
        popup.is_open = lambda: True
        return popup

    def test_voice_entry_is_done_while_permission_commit_is_still_pending(self):
        popup = self.popup()
        popup.update('voice', 'joining')
        popup.update('confirm', 'voice connected, committing')
        self.assertTrue(popup.rows['voice'].configure.call_args.kwargs['text'].startswith('✓'))
        self.assertTrue(popup.rows['confirm'].configure.call_args.kwargs['text'].startswith('●'))
        self.assertIn('음성 입장 완료', popup.header.configure.call_args.kwargs['text'])

    def test_late_display_update_cannot_restore_an_earlier_loading_step(self):
        popup = self.popup()
        popup.update('confirm', 'voice entered')
        count = popup.detail.set.call_count
        popup.update('owner', 'late old detail')
        self.assertEqual(popup.detail.set.call_count, count)
        self.assertIn('음성 입장 완료', popup.header.configure.call_args.kwargs['text'])

    def test_no_previous_owner_is_shown_as_skipped_through_completion(self):
        popup = self.popup()
        popup.update('authority', 'solo', skipped=('handover',))
        popup.update('confirm', 'connected')
        popup.finish('active')
        self.assertIn('생략', popup.rows['handover'].configure.call_args.kwargs['text'])
        self.assertIn('연결 완료', popup.header.configure.call_args.kwargs['text'])
        popup.update('voice', 'late')
        self.assertEqual(popup.detail.set.call_args.args[0], 'active')

    def test_preliminary_popup_is_adopted_instead_of_duplicated(self):
        popup = self.popup()
        popup.token = ''
        app = NS(discord_connection_progress=popup)
        with patch('discord_connection_progress.DiscordConnectionProgress') as create:
            self.assertIs(show_connection_progress(app, role='incoming', token='new'), popup)
        create.assert_not_called()
        self.assertEqual(popup.token, 'new')


class GuiFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        methods = [node for owner in tree.body if isinstance(owner, ast.ClassDef)
                   for node in owner.body if isinstance(node, ast.FunctionDef)
                   and node.name in ('_toggle_discord_bot_runtime', '_schedule_discord_bot_status_poll')]
        namespace = dict(tk=NS(TclError=RuntimeError))
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<connection-ui>', 'exec'), namespace)
        cls.namespace = namespace

    def app(self, payload):
        return NS(root=NS(after=Mock(return_value='job')), schedule_window=None,
            discord_bot_expected_running=True, discord_bot_last_status_payload=payload,
            _widget_available=lambda _: False,
            _callbacks_available=lambda: True, _schedule_discord_bot_status_poll=Mock(),
            _refresh_discord_bot_status_ui_async=Mock(), _refresh_discord_bot_status_ui=Mock(),
            _is_discord_bot_toggle_locked=lambda: False, _discord_bot_status_matches=lambda _: True,
            _start_discord_bot_runtime=Mock(return_value=True), _ensure_discord_authority=Mock(),
            _append_debug_log=Mock())

    def test_connected_standby_does_not_use_continuous_fast_polling(self):
        app = self.app(dict(ok=True, online=True, authority_joining=False))
        self.namespace['_schedule_discord_bot_status_poll'](app)
        self.assertEqual(app.root.after.call_args.args[0], 3000)

    def test_joining_and_bot_login_continue_fast_local_polling(self):
        for payload in (dict(online=True, authority_joining=True), dict(online=False)):
            app = self.app(payload)
            self.namespace['_schedule_discord_bot_status_poll'](app)
            self.assertEqual(app.root.after.call_args.args[0], 500)

    def test_button_displays_preparation_before_starting(self):
        app = self.app(dict(ok=True, online=True))
        order = []
        popup = NS(update=lambda *_: order.append('display'), close=Mock())
        app._refresh_discord_bot_status_ui.side_effect = lambda: order.append('status')
        app._start_discord_bot_runtime.side_effect = lambda: (order.append('start'), True)[1]
        with patch('discord_connection_progress.show_connection_progress', return_value=popup):
            self.namespace['_toggle_discord_bot_runtime'](app)
        self.assertEqual(order, ['display', 'status', 'start'])
        popup.close.assert_not_called()

    def test_failed_preflight_closes_loading_but_keeps_existing_settings_warning(self):
        app = self.app(dict(ok=True, online=True))
        app._start_discord_bot_runtime.return_value = False
        popup = Mock()
        with patch('discord_connection_progress.show_connection_progress', return_value=popup):
            self.namespace['_toggle_discord_bot_runtime'](app)
        popup.close.assert_called_once()

    def test_voice_release_does_not_open_a_connection_popup(self):
        app = self.app(dict(ok=True, online=True, send_authorized=True))
        with patch('discord_connection_progress.show_connection_progress') as show:
            self.namespace['_toggle_discord_bot_runtime'](app)
        show.assert_not_called()
        app._ensure_discord_authority.return_value.release.assert_called_once()
        app._start_discord_bot_runtime.assert_not_called()


if __name__ == '__main__':
    unittest.main()
