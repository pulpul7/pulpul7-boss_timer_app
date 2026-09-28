"""No game capture, real Tk windows, audio, or user settings writes."""
from datetime import datetime
import os
import queue
import threading
import unittest
from unittest.mock import Mock, patch

from precision_capture_ui import start
from precision_capture_widgets import CaptureProgress, prevent_mouse_activation
from precision_time_tracker import PrecisionConfig


class AutoApplyTests(unittest.TestCase):
    def run_capture(self, *, enabled=True, event='done', failed=False, measured=True,
                    cancel=False, change=None, toggle=None):
        app = Mock()
        app._precision_session = None
        app.schedule_input_ocr_worker_active = app.schedule_input_ocr_addon_busy = False
        app.precision_auto_apply = enabled
        app.precision_capture_rate = 5
        app._get_preferred_odin_window_handle.return_value = 123
        app._get_odin_client_screen_rect.return_value = dict(left=0, top=0, width=1600, height=900)
        app._get_schedule_server_profile_dir.return_value = 'profile'
        app.schedule_input_ocr_mode_results = {}
        app._show_centered_messagebox.return_value = False
        target = Mock()
        text = ['original']
        target.get.side_effect = lambda *args: text[0]
        app._get_schedule_input_ocr_text_widget_for_mode.return_value = target
        callbacks, order = [], []
        app.root.after.side_effect = lambda delay, callback: callbacks.append((delay, callback))
        def render(*args):
            order.append('render')
            text[0] = '23:30:41.123456 헤르모드'
        app._render_schedule_input_ocr_text.side_effect = render
        def apply(*, auto_confirm_update=False):
            self.assertTrue(auto_confirm_update)
            self.assertIsNone(app._precision_session)
            self.assertFalse(app.schedule_input_ocr_worker_active)
            self.assertFalse(app.schedule_input_ocr_addon_busy)
            self.assertEqual(text[0], '23:30:41.123456 헤르모드')
            order.append('apply')
        app._apply_schedule_input_batch.side_effect = apply
        result = dict(area='test', slot_results=[dict(boss_name='헤르모드', slot_index=1,
                      state='TIMED', remaining_seconds=5000)])
        measurements = [dict(boss_name='헤르모드', target_datetime='2026-09-28T23:30:41.123456', uncertainty_seconds=.1)] if measured else []
        if failed:
            result['slot_results'].append(dict(boss_name='미확정', slot_index=2, state='TIMED', remaining_seconds=6000))
        report = dict(t0=100., ended_at=140., max_capture_interval=.2, max_jitter=.01,
                      total=len(result['slot_results']), excluded=int(failed), results=measurements)
        session = Mock(config=PrecisionConfig(), wall0=datetime(2026, 9, 28), debug_logging=False)
        session.events, session.cancel = queue.Queue(), threading.Event()
        def popup(*args, **kwargs):
            if toggle is not None:
                kwargs['on_auto_apply'](toggle)
            if cancel:
                args[2]()
            return Mock()
        def capture():
            session.events.put(('regions', []))
            if event == 'done':
                session.events.put(('done', (result, report)))
                # Real worker sets this in finally even for successful scans.
                session.cancel.set()
            else:
                session.events.put((event, 'capture error' if event == 'error' else None))
                session.events.put(('stopped', None))
        session.start.side_effect = capture
        with patch('precision_capture_ui.PrecisionCaptureSession', return_value=session), \
             patch('precision_capture_ui.hide_app_windows', return_value=[]), \
             patch('precision_capture_ui.restore_app_windows') as restore, \
             patch('precision_capture_widgets.CaptureProgress', side_effect=popup):
            start(app, {})
            changed = False
            for _ in range(20):
                if not callbacks:
                    break
                delay, callback = callbacks.pop(0)
                if delay == 300 and change and not changed:
                    changed = True
                    if change == 'text': text[0] = 'edited'
                    if change == 'profile': app._get_schedule_server_profile_dir.return_value = 'different'
                    if change == 'window': app.schedule_input_window = Mock()
                    if change == 'new_scan': app._precision_session = Mock()
                callback()
            restore.assert_called_once()
        return app, order

    def test_success_renders_exact_text_then_applies_once_after_unlock(self):
        app, order = self.run_capture()
        self.assertEqual(order, ['render', 'apply'])
        app._apply_schedule_input_batch.assert_called_once_with(auto_confirm_update=True)

    def test_default_off_and_live_toggle_are_respected(self):
        for enabled, toggle, expected in ((False, None, 0), (False, True, 1), (True, False, 0)):
            with self.subTest(enabled=enabled, toggle=toggle):
                app, _ = self.run_capture(enabled=enabled, toggle=toggle)
                self.assertEqual(app._apply_schedule_input_batch.call_count, expected)
                self.assertEqual(app._save_settings.call_count, int(toggle is not None))

    def test_no_auto_apply_on_cancel_error_missing_measurement_or_failed_boss(self):
        for args in (dict(event='error'), dict(event='cancelled'), dict(cancel=True),
                     dict(measured=False), dict(failed=True)):
            with self.subTest(args=args):
                app, _ = self.run_capture(**args)
                app._apply_schedule_input_batch.assert_not_called()

    def test_deferred_apply_aborts_if_inputs_or_capture_context_change(self):
        for change in ('text', 'profile', 'window', 'new_scan'):
            with self.subTest(change=change):
                app, _ = self.run_capture(change=change)
                app._apply_schedule_input_batch.assert_not_called()


class UpdateConfirmationTests(unittest.TestCase):
    """Exercise the actual input validation path without windows or writes."""

    def setUp(self):
        from boss_timer_gui import BossTimerApp
        self.apply = BossTimerApp._apply_schedule_input_batch
        self.app = Mock()
        self.app.schedule_input_edit_mode = False
        self.app.schedule_input_past_enabled = False
        self.app.schedule_input_add_mode = False
        self.app._get_schedule_reference_datetime.return_value = datetime(2026, 9, 28, 12)
        self.app._get_schedule_input_raw_text.return_value = '23:30:41.123456 헤르모드'
        self.app._warn_schedule_input_duplicate_bosses.return_value = False
        self.app._parse_schedule_input_lines.return_value = ([dict(
            boss_name='헤르모드', raw_key='hermod', state='scheduled', mode='clock')], 0)
        self.app._collect_schedule_uncertain_overwrite_items.return_value = []
        # Stop at the persistence boundary; real schedule/settings stay untouched.
        self.app._apply_schedule_parsed_batch.return_value = False

    def test_auto_apply_confirms_update_but_manual_apply_still_asks(self):
        self.apply(self.app, auto_confirm_update=True)
        self.assertTrue(self.app._apply_schedule_parsed_batch.call_args.kwargs['skip_overwrite_confirm'])
        self.apply(self.app)
        self.assertFalse(self.app._apply_schedule_parsed_batch.call_args.kwargs['skip_overwrite_confirm'])

    def test_duplicate_boss_warning_still_blocks_auto_apply(self):
        self.app._warn_schedule_input_duplicate_bosses.return_value = True
        self.apply(self.app, auto_confirm_update=True)
        self.app._warn_schedule_input_duplicate_bosses.assert_called_once()
        self.app._apply_schedule_parsed_batch.assert_not_called()

    def test_uncertain_data_cancel_still_blocks_auto_apply(self):
        self.app._collect_schedule_uncertain_overwrite_items.return_value = [dict(raw_key='hermod')]
        self.app._show_schedule_uncertain_overwrite_dialog.return_value = None
        self.apply(self.app, auto_confirm_update=True)
        self.app._show_schedule_uncertain_overwrite_dialog.assert_called_once()
        self.app._apply_schedule_parsed_batch.assert_not_called()


class ProgressWidgetTests(unittest.TestCase):
    def test_checkbox_is_below_rate_cancel_is_explicit_and_no_focus_is_requested(self):
        cancel, changed = Mock(), Mock()
        with patch('precision_capture_widgets.tk.Toplevel') as top, \
             patch('precision_capture_widgets.tk.Frame'), patch('precision_capture_widgets.tk.Label'), \
             patch('precision_capture_widgets.tk.Canvas'), patch('precision_capture_widgets.tk.Button') as button, \
             patch('precision_capture_widgets.tk.Checkbutton') as check, \
             patch('precision_capture_widgets.tk.BooleanVar') as variable, \
             patch('precision_capture_widgets.prevent_mouse_activation') as no_activate:
            widget = CaptureProgress(Mock(), dict(left=0, top=0), cancel, auto_apply=False, on_auto_apply=changed)
            no_activate.assert_called_once_with(top.return_value)
            top.return_value.state.assert_called_once_with('normal')
            top.return_value.deiconify.assert_not_called()
            top.return_value.lift.assert_not_called()
            top.return_value.focus_force.assert_not_called()
            cancel.assert_not_called()
            self.assertEqual(check.call_args.kwargs['text'], '자동적용')
            self.assertEqual(check.return_value.place.call_args.kwargs['y'], 34)
            variable.return_value.get.return_value = True
            check.call_args.kwargs['command']()
            changed.assert_called_once_with(True)
            cancel.assert_not_called()
            button.call_args.kwargs['command']()
            cancel.assert_called_once()
            top.return_value.protocol.assert_called_once_with('WM_DELETE_WINDOW', cancel)

    @unittest.skipUnless(os.name == 'nt', 'Windows window style')
    def test_native_style_preserves_existing_bits_without_click_through(self):
        api, window = Mock(), Mock()
        api.GetAncestor.return_value = 123
        api.GetWindowLongPtrW.return_value = 0x100
        api.SetWindowLongPtrW.return_value = 0x100
        with patch('ctypes.WinDLL', return_value=api):
            prevent_mouse_activation(window)
        api.SetWindowLongPtrW.assert_called_once_with(123, -20, 0x08000100)
        # WS_EX_TRANSPARENT (0x20) is deliberately absent: clicks reach controls.
        self.assertFalse(api.SetWindowLongPtrW.call_args.args[2] & 0x20)


if __name__ == '__main__':
    unittest.main()
