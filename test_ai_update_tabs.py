"""Theme-independent update tabs: readable selected, idle and hover states."""
import unittest
from unittest.mock import Mock, patch

import ai_update_center as module


class UpdateTabTests(unittest.TestCase):
    def test_manual_full_download_opens_progress_before_worker_and_closes_on_completion(self):
        import queue
        center = object.__new__(module.AiUpdateCenter)
        service = Mock()
        center.app = Mock()
        center.app._get_program_update_service.return_value = service
        center.busy = False
        center.window = None
        center.root = Mock()
        center.events = queue.Queue()
        center._status = Mock()
        center._work = Mock(return_value=True)
        center._install_full()
        service._open_progress.assert_called_once()
        self.assertIs(center._full_preparation, service)
        done = center._work.call_args.args[2]
        result = {'state': 'prepared', 'message': 'ready'}
        center.events.put((done, result, None, False))
        center._poll()
        service._close_progress.assert_called_once()
        service.schedule.assert_called_once_with(result)
        self.assertIsNone(center._full_preparation)

    def test_download_bar_uses_received_bytes_and_verification_switches_to_busy(self):
        from program_update_service import ProgramUpdateService
        service = object.__new__(ProgramUpdateService)
        service.progress = Mock()
        service.progress.winfo_exists.return_value = True
        service.progress_message, service.progress_bar = Mock(), Mock()
        service.progress_mode = 'indeterminate'
        service.updater = Mock(progress=dict(stage='downloading', message='download', received=25, total=100))
        service._refresh_download_progress()
        service.progress_bar.configure.assert_any_call(value=25)
        service.updater.progress = dict(stage='verifying', message='verify', received=0, total=0)
        service._refresh_download_progress()
        service.progress_message.set.assert_called_with('verify')
        self.assertEqual(service.progress_mode, 'indeterminate')
        service.progress_bar.start.assert_called_once_with(50)

    def setUp(self):
        self.center = object.__new__(module.AiUpdateCenter)
        self.center.update_tab_titles = ("배포 목록 / 패치 설명", "설치 / 실패 내역")
        self.center.update_tab_buttons = [Mock(), Mock()]
        self.center.update_tab_pages = [Mock(), Mock()]

    def test_selection_colors_and_visible_page_switch_together(self):
        for selected in (0, 1, 0):
            self.center._select_update_tab(selected)
            self.assertEqual(self.center.selected_update_tab, selected)
            self.center.update_tab_pages[selected].tkraise.assert_called()
            for index, button in enumerate(self.center.update_tab_buttons):
                options = button.configure.call_args.kwargs
                self.assertEqual(options["bg"], "#1e3a8a" if index == selected else "#dbeafe")
                self.assertEqual(options["fg"], "#ffffff" if index == selected else "#1e293b")
                self.assertEqual(options["activeforeground"], options["fg"])
                self.assertNotEqual(options["activebackground"], options["activeforeground"])
                self.assertEqual(options["text"].startswith("● "), index == selected)

    def test_keyboard_navigation_wraps_and_moves_focus(self):
        self.assertEqual(self.center._focus_update_tab(-1), "break")
        self.assertEqual(self.center.selected_update_tab, 1)
        self.center.update_tab_buttons[1].focus_set.assert_called_once()
        self.center._focus_update_tab(2)
        self.assertEqual(self.center.selected_update_tab, 0)

    def test_build_uses_local_widgets_not_native_theme_or_notebook(self):
        with patch.object(module.tk, "Frame", side_effect=lambda *a, **k: Mock()) as frame, \
             patch.object(module.tk, "Button", side_effect=lambda *a, **k: Mock()) as button, \
             patch.object(module.ttk, "Style") as style, \
             patch.object(module.ttk, "Notebook") as notebook:
            pages = self.center._build_update_tabs(Mock())
        self.assertEqual(len(pages), 2)
        self.assertEqual(button.call_count, 2)
        style.assert_not_called()
        notebook.assert_not_called()
        self.assertTrue(any(call.kwargs.get("bg") == "#1e3a8a" for call in frame.call_args_list))
        # Clicking the second tab must raise its page and apply selected colors.
        button.call_args_list[1].kwargs["command"]()
        self.assertEqual(self.center.selected_update_tab, 1)
        self.assertEqual(self.center.update_tab_buttons[1].configure.call_args.kwargs["bg"], "#1e3a8a")

    def test_scope_tabs_switch_without_starting_a_check_or_install(self):
        self.center.scope_titles = ("전체 업데이트", "웹서버 업데이트", "알림 모듈")
        self.center.scope_buttons = [Mock(), Mock(), Mock()]
        self.center.scope_pages = [Mock(), Mock(), Mock()]
        self.center.updater = Mock()
        for index in (2, 0):
            self.center._select_scope(index)
            self.center.scope_pages[index].tkraise.assert_called_once()
            options = self.center.scope_buttons[index].configure.call_args.kwargs
            self.assertEqual(options["bg"], "#1e3a8a")
            self.assertEqual(options["fg"], "#ffffff")
        self.center.updater.assert_not_called()
        self.assertFalse(self.center.updater.method_calls)

    def test_full_panel_enables_check_but_requires_a_new_release_before_install(self):
        self.center.app_version = "5.0.0"
        with patch.object(module.tk, "Frame", side_effect=lambda *a, **k: Mock()), \
             patch.object(module.tk, "Label", side_effect=lambda *a, **k: Mock()), \
             patch.object(module.ttk, "Frame", side_effect=lambda *a, **k: Mock()), \
             patch.object(module.ttk, "Button", side_effect=lambda *a, **k: Mock()) as buttons, \
             patch.object(module.tk, "StringVar", side_effect=lambda *a, **k: Mock()):
            self.center._build_full_panel(Mock())
        self.assertEqual(buttons.call_count, 2)
        self.assertEqual(buttons.call_args_list[0].kwargs['state'], 'normal')
        self.assertEqual(buttons.call_args_list[1].kwargs['state'], 'disabled')
        for call in buttons.call_args_list:
            self.assertTrue(callable(call.kwargs["command"]))

    def test_web_action_uses_existing_nonblocking_request(self):
        updater = Mock()
        self.center.app = Mock(web_service_manager=Mock(page_updater=updater))
        self.center._refresh_web_panel = Mock()
        self.center._request_web_update()
        updater.request.assert_called_once_with()
        updater.request_and_wait.assert_not_called()
        self.center._refresh_web_panel.assert_called_once_with()
        self.center.app._show_centered_messagebox.assert_not_called()

    def test_unavailable_web_module_is_disabled(self):
        self.center.app = Mock(web_service_manager=None)
        self.center.web_summary = Mock(get=Mock(return_value=""))
        self.center.web_update_button = Mock()
        self.center._refresh_web_panel()
        self.assertIn("준비되지", self.center.web_summary.set.call_args.args[0])
        self.center.web_update_button.configure.assert_called_once_with(state="disabled")


if __name__ == "__main__":
    unittest.main()
