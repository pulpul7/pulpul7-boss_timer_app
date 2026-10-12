"""Check live-setting confirmation without importing the GUI or opening Tk."""
import ast
from pathlib import Path
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class ConfirmationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(Path(__file__).with_name('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        names = {'_set_schedule_alarm_countdown_enabled', '_on_schedule_alarm_countdown_toggled'}
        functions = [node for klass in cls.tree.body if isinstance(klass, ast.ClassDef)
                     for node in klass.body if isinstance(node, ast.FunctionDef) and node.name in names]
        scope = {}
        exec(compile(ast.Module(body=functions, type_ignores=[]), 'countdown-source', 'exec'), scope)
        cls.methods = {name: scope[name] for name in names}

    def setUp(self):
        self.app = NS(schedule_alarm_countdown_enabled_var=Var(True),
            schedule_alarm_countdown_change_serial=0, schedule_alarm_countdown_confirmation_pending=False,
            _append_debug_log=Mock(), _show_centered_messagebox=Mock(return_value=True),
            _apply_schedule_alarm_global_options=Mock())
        for name, function in self.methods.items():
            setattr(self.app, name, MethodType(function, self.app))

    def test_scheduler_stays_off_until_explicit_yes(self):
        def confirm(*args, **kwargs):
            self.assertFalse(self.app.schedule_alarm_countdown_enabled_var.get())
            self.app._apply_schedule_alarm_global_options.assert_not_called()
            self.assertEqual(kwargs['default'], 'no')
            self.assertEqual(kwargs['parent'], 'schedule-window')
            return True
        self.app._show_centered_messagebox.side_effect = confirm
        self.assertTrue(self.app._on_schedule_alarm_countdown_toggled(parent='schedule-window', show_status=False))
        self.assertTrue(self.app.schedule_alarm_countdown_enabled_var.get())
        self.app._apply_schedule_alarm_global_options.assert_called_once_with(show_status=False)

    def test_no_or_close_keeps_off_and_does_not_save(self):
        for answer in (False, None):
            with self.subTest(answer=answer):
                self.app.schedule_alarm_countdown_enabled_var.set(True)
                self.app._show_centered_messagebox.return_value = answer
                self.assertFalse(self.app._on_schedule_alarm_countdown_toggled())
                self.assertFalse(self.app.schedule_alarm_countdown_enabled_var.get())
                self.app._apply_schedule_alarm_global_options.assert_not_called()

    def test_disable_needs_no_confirmation(self):
        self.app.schedule_alarm_countdown_enabled_var.set(False)
        self.assertTrue(self.app._on_schedule_alarm_countdown_toggled())
        self.app._show_centered_messagebox.assert_not_called()
        self.app._apply_schedule_alarm_global_options.assert_called_once_with(show_status=True)

    def test_profile_reload_during_popup_invalidates_old_yes(self):
        def confirm(*args, **kwargs):
            self.app._set_schedule_alarm_countdown_enabled(False, source='profile_settings_load')
            return True
        self.app._show_centered_messagebox.side_effect = confirm
        self.assertFalse(self.app._on_schedule_alarm_countdown_toggled())
        self.assertFalse(self.app.schedule_alarm_countdown_enabled_var.get())
        self.app._apply_schedule_alarm_global_options.assert_not_called()

    def test_remote_change_during_popup_takes_precedence(self):
        def confirm(*args, **kwargs):
            self.app._set_schedule_alarm_countdown_enabled(True, source='discord_command')
            return False
        self.app._show_centered_messagebox.side_effect = confirm
        self.assertFalse(self.app._on_schedule_alarm_countdown_toggled())
        self.assertTrue(self.app.schedule_alarm_countdown_enabled_var.get())
        self.app._apply_schedule_alarm_global_options.assert_not_called()

    def test_nested_checkbox_invocation_cannot_bypass_confirmation(self):
        self.app.schedule_alarm_countdown_confirmation_pending = True
        self.assertFalse(self.app._on_schedule_alarm_countdown_toggled())
        self.assertFalse(self.app.schedule_alarm_countdown_enabled_var.get())
        self.app._show_centered_messagebox.assert_not_called()
        self.app._apply_schedule_alarm_global_options.assert_not_called()

    def test_popup_error_resets_guard_and_leaves_countdown_off(self):
        self.app._show_centered_messagebox.side_effect = RuntimeError('popup closed')
        with self.assertRaises(RuntimeError):
            self.app._on_schedule_alarm_countdown_toggled()
        self.assertFalse(self.app.schedule_alarm_countdown_enabled_var.get())
        self.assertFalse(self.app.schedule_alarm_countdown_confirmation_pending)

    def test_both_actual_checkboxes_use_confirmation_handler(self):
        controls = []
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != 'Checkbutton':
                continue
            keywords = {item.arg: item.value for item in node.keywords}
            text = keywords.get('text')
            if isinstance(text, ast.Constant) and text.value == '초읽기 사용':
                controls.append(keywords['command'])
        self.assertEqual(len(controls), 2)
        for command in controls:
            calls = [n.func.attr for n in ast.walk(command)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
            self.assertIn('_on_schedule_alarm_countdown_toggled', calls)


if __name__ == '__main__':
    unittest.main()
