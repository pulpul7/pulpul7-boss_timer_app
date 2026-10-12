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
        wanted = {'_get_centered_messagebox_parent', '_get_centered_messagebox_stack_parent'}
        methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in wanted]
        scope = dict(tk=NS(Widget=object, TclError=RuntimeError))
        exec(compile(ast.Module(body=methods, type_ignores=[]), 'modal-parent-source', 'exec'), scope)
        cls.choose = staticmethod(scope['_get_centered_messagebox_parent'])
        cls.stack_parent = staticmethod(scope['_get_centered_messagebox_stack_parent'])

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

    def test_confirmation_stacks_above_running_connection_progress(self):
        owner = NS(winfo_viewable=lambda: True)
        window = NS(winfo_viewable=lambda: True)
        app = NS(_widget_available=lambda value: value is not None,
                 discord_connection_progress=NS(window=window, finished=False))
        self.assertIs(self.stack_parent(app, owner), window)
        app.discord_connection_progress.finished = True
        self.assertIs(self.stack_parent(app, owner), owner)

    def test_hidden_progress_is_never_used_as_transient_parent(self):
        owner = NS(winfo_viewable=lambda: True)
        window = NS(winfo_viewable=lambda: False)
        app = NS(_widget_available=lambda value: value is not None,
                 discord_connection_progress=NS(window=window, finished=False))
        self.assertIs(self.stack_parent(app, owner), owner)

    def test_existing_visible_modal_has_priority_over_progress(self):
        owner, modal = object(), object()
        grab = NS(winfo_viewable=lambda: True, winfo_toplevel=lambda: modal)
        app = NS(_widget_available=lambda value: value is not None)
        self.assertIs(self.stack_parent(app, owner, grab), modal)


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
