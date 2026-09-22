"""Theme-independent update tabs: readable selected, idle and hover states."""
import unittest
from unittest.mock import Mock, patch

import ai_update_center as module


class UpdateTabTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
