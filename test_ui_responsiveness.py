"""Offline modal-owner and watchdog checks. No GUI, processes, or user files."""
import ast
from pathlib import Path
import threading
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from ui_responsiveness import UIResponsivenessMonitor


class ModalParentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == '_get_centered_messagebox_parent')
        scope = dict(tk=NS(Widget=object, TclError=RuntimeError))
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'modal-parent-source', 'exec'), scope)
        cls.choose = staticmethod(scope[method.name])

    def test_hidden_schedule_is_not_owner_of_delayed_retry_dialog(self):
        root = NS(winfo_viewable=lambda: True)
        schedule = NS(winfo_viewable=lambda: False)
        app = NS(root=root, schedule_window=schedule, schedule_window_open=False,
                 _widget_available=lambda window: window is not None)
        self.assertIs(self.choose(app, schedule), root)

    def test_visible_schedule_remains_parent(self):
        root = NS(winfo_viewable=lambda: True)
        schedule = NS(winfo_viewable=lambda: True)
        app = NS(root=root, _widget_available=lambda window: window is not None)
        self.assertIs(self.choose(app, schedule), schedule)


class WatchdogTests(unittest.TestCase):
    def node(self):
        watch = object.__new__(UIResponsivenessMonitor)
        watch.threshold = 15
        watch.ui_thread = threading.get_ident()
        watch.last_pulse = None
        watch.reported = False
        watch._record = Mock()
        return watch

    def test_long_startup_is_not_reported_before_first_ui_callback(self):
        watch = self.node()
        watch._check(100)
        watch._record.assert_not_called()

    def test_hang_reports_once_and_recovery_allows_next_report(self):
        watch = self.node()
        watch.last_pulse = 10
        watch._check(26)
        watch._check(100)
        self.assertEqual(watch._record.call_count, 1)
        record = watch._record.call_args.args[0]
        self.assertEqual(record['event'], 'ui_unresponsive')
        self.assertIn(threading.current_thread().name, record['stacks'])
        watch.last_pulse = 100
        watch._check(101)
        self.assertEqual(watch._record.call_args.args[0]['event'], 'ui_responsive_again')
        watch._check(116)
        self.assertEqual(watch._record.call_count, 3)


if __name__ == '__main__':
    unittest.main()
