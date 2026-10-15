"""Input-window default boundaries; no GUI, network, build or live settings."""
import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock


class InputDayDefaultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        names = {'_get_schedule_input_within_day_open_default', 'open_schedule_input_window',
                 '_get_schedule_next_reset_datetime_for_custom_server_open'}
        methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
        cls.methods = dict(datetime=datetime, timedelta=timedelta, tk=NS(TclError=RuntimeError))
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<input-day-default>', 'exec'), cls.methods)

    def app(self, now, opened=datetime(2026, 10, 14, 14, 30), cutoff=datetime(2026, 10, 21, 8)):
        app = NS(_get_schedule_reference_datetime=Mock(return_value=now),
                 _get_schedule_input_server_open_datetime=Mock(return_value=opened),
                 _get_schedule_next_reset_datetime_for_custom_server_open=Mock(return_value=cutoff))
        app._get_schedule_input_within_day_open_default = MethodType(self.methods['_get_schedule_input_within_day_open_default'], app)
        return app

    def test_opening_day_off_next_calendar_day_on_until_exact_maintenance(self):
        for now, expected in ((datetime(2026, 10, 14, 15), False),
                              (datetime(2026, 10, 14, 23, 59, 59), False),
                              (datetime(2026, 10, 15, 0), True),
                              (datetime(2026, 10, 20, 23, 59), True),
                              (datetime(2026, 10, 21, 7, 59, 59), True),
                              (datetime(2026, 10, 21, 8), False),
                              (datetime(2026, 10, 21, 9), False)):
            with self.subTest(now=now):
                self.assertEqual(self.app(now)._get_schedule_input_within_day_open_default(), expected)

    def test_extension_across_midnight_uses_actual_opening_date(self):
        opened = datetime(2026, 10, 15, 1, 30)
        self.assertFalse(self.app(datetime(2026, 10, 15, 23), opened)._get_schedule_input_within_day_open_default())
        self.assertTrue(self.app(datetime(2026, 10, 16), opened)._get_schedule_input_within_day_open_default())

    def test_early_maintenance_uses_previous_cycle_cutoff_not_next_week(self):
        app = self.app(datetime(2026, 10, 21, 9))
        self.assertFalse(app._get_schedule_input_within_day_open_default())
        app._get_schedule_next_reset_datetime_for_custom_server_open.assert_called_once_with(datetime(2026, 10, 14, 14, 30))
        self.assertFalse(self.app(datetime(2026, 10, 15), cutoff=None)._get_schedule_input_within_day_open_default())

    def test_temporary_maintenance_ends_auto_default(self):
        app = self.app(datetime(2026, 10, 16))
        app._get_schedule_maintenance_cutoff_datetime = Mock(return_value=datetime(2026, 10, 21, 8))
        app._get_schedule_next_temporary_maintenance_datetime = Mock(return_value=datetime(2026, 10, 16, 10))
        app._get_schedule_next_reset_datetime_for_custom_server_open = MethodType(
            self.methods['_get_schedule_next_reset_datetime_for_custom_server_open'], app)
        self.assertTrue(app._get_schedule_input_within_day_open_default())
        app._get_schedule_reference_datetime.return_value = datetime(2026, 10, 16, 10)
        self.assertFalse(app._get_schedule_input_within_day_open_default())

    def test_reopening_applies_default_but_focusing_keeps_manual_choice(self):
        app = self.app(datetime(2026, 10, 15))
        app.schedule_input_window_busy = False
        app.schedule_input_window_open = False
        app.schedule_input_window = Mock()
        app.schedule_input_window.winfo_exists.return_value = True
        app.root = Mock()
        app.schedule_window = None
        app.schedule_input_edit_mode = False
        app.schedule_input_hide_fraction_var = Mock()
        app.schedule_input_ocr_within_day_only_var = Mock()
        app._widget_available = lambda widget: widget is not None
        for name in ('_update_schedule_base_label', '_ensure_schedule_input_window', '_configure_schedule_input_window_mode',
                     '_start_schedule_input_clipboard_poll', '_start_schedule_input_ocr_runtime_prewarm',
                     '_show_schedule_ocr1_region_preview', '_position_schedule_input_window'):
            setattr(app, name, Mock())
        open_window = MethodType(self.methods['open_schedule_input_window'], app)
        open_window()
        app.schedule_input_ocr_within_day_only_var.set.assert_called_once_with(True)
        app.schedule_input_ocr_within_day_only_var.reset_mock()
        open_window()
        app.schedule_input_ocr_within_day_only_var.set.assert_not_called()
        app.schedule_input_window_open = False
        app._get_schedule_reference_datetime.return_value = datetime(2026, 10, 21, 8)
        open_window()
        app.schedule_input_ocr_within_day_only_var.set.assert_called_once_with(False)


if __name__ == '__main__':
    unittest.main()
