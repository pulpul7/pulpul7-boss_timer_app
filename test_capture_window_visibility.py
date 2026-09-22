"""No real windows, screenshots, settings writes or game interaction."""
from datetime import datetime
import queue
import threading
import unittest
from unittest.mock import Mock, patch

from capture_window_visibility import app_windows, hide_app_windows, restore_app_windows, lower_app_windows, minimize_app_windows
from precision_capture_ui import start
from precision_time_tracker import PrecisionConfig


class Frame:
    def __init__(self, *children):
        self.children=list(children)
    def winfo_exists(self): return True
    def winfo_children(self): return self.children


class Window(Frame):
    def __init__(self, *children, state='normal', topmost=True):
        super().__init__(*children)
        self.mode=state
        self.topmost=topmost
        self.lower=Mock()
        self.lift=Mock()
        self.update_idletasks=Mock()
    def state(self, value=None):
        if value is not None: self.mode=value
        return self.mode
    def attributes(self, key, value=None):
        if value is not None: self.topmost=value
        return self.topmost
    def withdraw(self): self.mode='withdrawn'
    def iconify(self): self.mode='iconic'


class VisibilityTests(unittest.TestCase):
    def make_capture_app(self):
        from boss_timer_gui import BossTimerApp
        app=object.__new__(BossTimerApp)
        app.root=Mock()
        app.schedule_input_ocr_worker_active=False
        app.schedule_input_ocr_addon_busy=False
        app.schedule_input_ocr_addon_open=True
        app.schedule_input_ocr_addon_session_id=1
        app.schedule_input_ocr_addon_window=Mock()
        app.schedule_input_ocr_addon_window.state.return_value='normal'
        app.schedule_input_ocr_addon_status_var=Mock()
        app._get_preferred_odin_window_handle=Mock(return_value=123)
        app._update_schedule_input_ocr_addon_controls=Mock()
        app._get_schedule_input_ocr_addon_restore_delay_ms=Mock(return_value=0)
        app._position_schedule_input_ocr_addon_window=Mock()
        app.schedule_input_ocr_items=[]
        app._is_valid_window_handle=Mock(return_value=True)
        app._get_schedule_input_ocr_item_content_hash=Mock(return_value='hash')
        app._capture_active_odin_window_image=Mock(return_value={'image_data':'fake'})
        app._append_schedule_input_ocr_item_to_queue=Mock(return_value=True)
        return app

    def test_f2_minimizes_all_windows_then_restores_at_original_feedback_timing(self):
        app=self.make_capture_app()
        app.root.after.side_effect=lambda delay,callback:callback()
        saved=[(app.root,'normal',False),(app.schedule_input_ocr_addon_window,'normal',True)]
        with patch('capture_window_visibility.minimize_app_windows',return_value=saved) as minimize, \
             patch('boss_timer_gui.threading.Thread') as worker:
            app._capture_schedule_input_ocr_from_odin()
            minimize.assert_called_once_with(app,123)
            worker.return_value.start.assert_called_once()
            self.assertTrue(app.schedule_input_ocr_addon_busy)
            app.root.state.assert_not_called()
            app._finish_schedule_input_ocr_addon_capture('done')
            app.root.state.assert_called_once_with('normal')
            app.schedule_input_ocr_addon_window.state.assert_called_once_with('normal')
            app.schedule_input_ocr_addon_window.lift.assert_called_once()
            self.assertFalse(app.schedule_input_ocr_addon_busy)

    def test_restore_delay_blocks_second_capture_until_all_windows_return(self):
        app=self.make_capture_app()
        callbacks=[]
        app.root.after.side_effect=lambda delay,callback:callbacks.append((delay,callback)) or 'restore'
        app._get_schedule_input_ocr_addon_restore_delay_ms.return_value=80
        with patch('capture_window_visibility.minimize_app_windows',return_value=[(app.root,'normal',False)]) as minimize, \
             patch('boss_timer_gui.threading.Thread'):
            app._capture_schedule_input_ocr_from_odin()
            app._finish_schedule_input_ocr_addon_capture('done')
            self.assertTrue(app.schedule_input_ocr_addon_busy)
            app._capture_schedule_input_ocr_from_odin()
            self.assertEqual(minimize.call_count,1)
            self.assertEqual(callbacks[0][0],80)
            callbacks[0][1]()
            self.assertFalse(app.schedule_input_ocr_addon_busy)
            app.root.state.assert_called_once_with('normal')

    def test_only_first_shot_minimizes_program_in_same_input_session(self):
        app=self.make_capture_app()
        app.root.after.side_effect=lambda delay,callback:callback()
        with patch('capture_window_visibility.minimize_app_windows',return_value=[(app.root,'normal',False)]) as minimize, \
             patch('boss_timer_gui.threading.Thread'):
            app._capture_schedule_input_ocr_from_odin()
            app._finish_schedule_input_ocr_addon_capture('first')
            app._capture_schedule_input_ocr_from_odin()
            app.schedule_input_ocr_addon_window.withdraw.assert_called_once()
            app._finish_schedule_input_ocr_addon_capture('second')
            minimize.assert_called_once_with(app,123)
            app.root.state.assert_called_once_with('normal')
            app.schedule_input_capture_minimized_once=False  # Next input opening after close.
            app._capture_schedule_input_ocr_from_odin()
            self.assertEqual(minimize.call_count,2)
            app._finish_schedule_input_ocr_addon_capture('next session')

    def test_input_close_resets_first_capture_flag(self):
        from boss_timer_gui import BossTimerApp
        app=Mock(schedule_input_capture_minimized_once=True, schedule_input_capture_hidden_windows=[],
                 _precision_session=None, schedule_input_ocr_worker_active=False,
                 schedule_input_ocr_preview_window=None, schedule_input_ocr_worker_after_id=None,
                 schedule_input_window=None)
        BossTimerApp.close_schedule_input_window(app)
        self.assertFalse(app.schedule_input_capture_minimized_once)
        app._close_schedule_input_ocr_addon_window.assert_called_once()

    def test_worker_failure_duplicate_and_success_all_restore_without_real_capture(self):
        for outcome in ('success','duplicate','missing','exception','append_error','hash_missing'):
            with self.subTest(outcome=outcome):
                app=self.make_capture_app()
                app.root.after.side_effect=lambda delay,callback:callback()
                if outcome=='duplicate': app._append_schedule_input_ocr_item_to_queue.return_value=False
                if outcome=='missing': app._capture_active_odin_window_image.return_value=None
                if outcome=='exception': app._capture_active_odin_window_image.side_effect=RuntimeError('fake failure')
                if outcome=='append_error': app._append_schedule_input_ocr_item_to_queue.side_effect=RuntimeError('fake queue failure')
                if outcome=='hash_missing': app._get_schedule_input_ocr_item_content_hash.return_value=''
                def thread_factory(*,target,args,**kwargs):
                    return Mock(start=lambda:target(*args))
                with patch('capture_window_visibility.minimize_app_windows',return_value=[(app.root,'normal',False)]), \
                     patch('boss_timer_gui.threading.Thread',side_effect=thread_factory), \
                     patch('boss_timer_gui.time.sleep'):
                    app._capture_schedule_input_ocr_from_odin()
                app.root.state.assert_called_once_with('normal')
                self.assertFalse(app.schedule_input_ocr_addon_busy)

    def test_worker_start_failure_restores_windows(self):
        app=self.make_capture_app()
        app.root.after.side_effect=lambda delay,callback:callback()
        with patch('capture_window_visibility.minimize_app_windows',return_value=[(app.root,'normal',False)]), \
             patch('boss_timer_gui.threading.Thread') as worker:
            worker.return_value.start.side_effect=RuntimeError('cannot start')
            app._capture_schedule_input_ocr_from_odin()
        app.root.state.assert_called_once_with('normal')
        self.assertFalse(app.schedule_input_ocr_addon_busy)

    def test_close_addon_restores_owner_and_late_worker_cannot_touch_next_capture(self):
        app=self.make_capture_app()
        app.root.after.side_effect=lambda delay,callback:callback()
        app.schedule_input_ocr_addon_poll_after_id=None
        app.schedule_input_ocr_addon_settings_window=None
        app.schedule_input_ocr_addon_stack_items=[]
        app._cancel_schedule_input_ocr_addon_resize_verify=Mock()
        app._unregister_schedule_input_ocr_addon_hotkeys=Mock()
        with patch('capture_window_visibility.minimize_app_windows',return_value=[(app.root,'normal',False)]), \
             patch('boss_timer_gui.threading.Thread') as worker, patch('boss_timer_gui.time.sleep'):
            app._capture_schedule_input_ocr_from_odin()
            old_target=worker.call_args.kwargs['target']
            app._close_schedule_input_ocr_addon_window()
            app.root.state.assert_called_once_with('normal')
            self.assertFalse(app.schedule_input_ocr_addon_busy)
            app._capture_schedule_input_ocr_from_odin()
            old_target(123)
            self.assertTrue(app.schedule_input_ocr_addon_busy)
            app._append_schedule_input_ocr_item_to_queue.assert_not_called()
            app.root.state.assert_called_once_with('normal')
            app._finish_schedule_input_ocr_addon_capture('cleanup')

    def test_partial_minimize_failure_restores_original_window_states(self):
        popup=Window()
        root=Window(popup,state='zoomed')
        app=Mock(root=root)
        app._force_window_foreground.side_effect=RuntimeError('game vanished')
        with patch('capture_window_visibility.tk.Toplevel',Window), self.assertRaises(RuntimeError):
            minimize_app_windows(app,123)
        self.assertEqual(root.state(),'zoomed')
        self.assertEqual(popup.state(),'normal')
        self.assertTrue(root.topmost)
        self.assertTrue(popup.topmost)

    def test_minimize_root_hide_transients_and_preserve_preexisting_states(self):
        addon=Window()
        hidden=Window(state='withdrawn',topmost=False)
        minimized=Window(state='iconic')
        root=Window(Frame(addon,hidden,minimized),state='zoomed',topmost=False)
        app=Mock(root=root)
        with patch('capture_window_visibility.tk.Toplevel',Window):
            saved=minimize_app_windows(app,123)
            self.assertEqual(root.state(),'iconic')
            self.assertEqual(addon.state(),'withdrawn')
            self.assertFalse(addon.topmost)
            restore_app_windows(saved)
        self.assertEqual(root.state(),'zoomed')
        self.assertEqual(addon.state(),'normal')
        self.assertEqual(hidden.state(),'withdrawn')
        self.assertEqual(minimized.state(),'iconic')
        self.assertTrue(addon.topmost)
        app._force_window_foreground.assert_called_once_with(123)

    def test_all_nested_windows_are_hidden_and_original_states_restored(self):
        settings=Window()
        hidden=Window(state='withdrawn',topmost=False)
        root=Window(Frame(Window(settings),hidden))
        app=Mock(root=root)
        with patch('capture_window_visibility.tk.Toplevel',Window):
            windows=app_windows(root)
            self.assertEqual(len(windows),4)
            saved=hide_app_windows(app,123)
            self.assertTrue(all(w.state()=='withdrawn' and not w.topmost for w in windows))
            restore_app_windows(saved)
        self.assertEqual(settings.state(),'normal')
        self.assertTrue(settings.topmost)
        self.assertEqual(hidden.state(),'withdrawn')
        app._force_window_foreground.assert_called_once_with(123)

    def test_lower_clears_topmost_for_every_window_and_focuses_game(self):
        popup=Window()
        app=Mock(root=Window(Frame(popup)))
        with patch('capture_window_visibility.tk.Toplevel',Window):
            lower_app_windows(app,123)
        self.assertFalse(popup.topmost)
        popup.lower.assert_called_once()
        app._force_window_foreground.assert_called_once_with(123)

    def test_precision_checks_day_filter_and_waits_for_first_capture_before_popup(self):
        app=Mock()
        app._precision_session=None
        app.schedule_input_ocr_worker_active=False
        app.schedule_input_ocr_addon_busy=False
        app.precision_capture_rate=5
        app._get_preferred_odin_window_handle.return_value=123
        app._get_odin_client_screen_rect.return_value=dict(left=0,top=0,width=1600,height=900)
        app._get_schedule_server_profile_dir.return_value='profile'
        app._get_schedule_input_ocr_text_widget_for_mode.return_value.get.return_value='draft'
        callbacks=[]
        app.root.after.side_effect=lambda delay,callback:callbacks.append(callback)
        session=Mock(config=PrecisionConfig(),wall0=datetime(2026,9,20))
        session.events=queue.Queue()
        session.cancel=threading.Event()
        with patch('precision_capture_ui.PrecisionCaptureSession',return_value=session), \
             patch('precision_capture_ui.hide_app_windows',return_value=[]) as hide, \
             patch('precision_capture_ui.restore_app_windows') as restore, \
             patch('capture_window_visibility.lower_app_windows') as lower, \
             patch('precision_capture_widgets.CaptureProgress') as popup:
            start(app,{})
            app.schedule_input_ocr_within_day_only_var.set.assert_called_once_with(True)
            hide.assert_called_once_with(app,123)
            popup.assert_not_called()
            callbacks.pop(0)()  # delayed capture starts, but no frame yet
            popup.assert_not_called()
            session.events.put(('regions',[]))  # first full frame is now safe
            callbacks.pop(0)()
            popup.assert_called_once()
            self.assertTrue(popup.call_args.kwargs['topmost'])
            popup.return_value.window.deiconify.assert_called_once()
            popup.return_value.window.lift.assert_called_once()
            popup.return_value.window.grab_set.assert_called_once()
            lower.assert_not_called()
            session.events.put(('stopped',None))
            callbacks.pop(0)()
            restore.assert_called_once_with([])
            self.assertFalse(app.schedule_input_ocr_worker_active)
            app.schedule_input_window.lift.assert_called_once()


if __name__=='__main__': unittest.main()
