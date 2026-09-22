"""Bounded, invisible Tk integration checks; no application, network or user data."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import subprocess
import sys
import unittest


def exercise_window():
    import faulthandler
    from types import SimpleNamespace
    from unittest.mock import Mock, patch
    import tkinter as tk
    from tkinter import ttk
    from notice_module.payload.notice_management import CATEGORIES, DEFAULT_RULES
    from notice_module.payload.notice_management_ui import NoticeManagementWindow

    faulthandler.dump_traceback_later(8, exit=True)
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"TK_UNAVAILABLE: {exc}")
        return 77
    root.attributes("-alpha", 0)
    root.geometry("300x200+0+0")
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    state = {"settings": {"collection_enabled": True, "output_enabled": False,
                          "categories": dict.fromkeys(CATEGORIES, True), "rules": deepcopy(DEFAULT_RULES)},
             "events": {}, "articles": {}, "collection": {}}
    for i in range(4):
        state["articles"][f"CT9G/{i}"] = {
            "title": f"Test notice {i}", "url": "https://m.cafe.daum.net/odin/CT9G/1",
            "pinned": True, "rank": i, "category": "update", "body": "Notice text\n" * 500,
        }
    app = SimpleNamespace(root=root, schedule_window=root, schedule_server_profile_id="test",
                          schedule_server_profile_name="test", _show_centered_messagebox=Mock())
    original = tk.Toplevel

    def invisible(*args, **kwargs):
        window = original(*args, **kwargs)
        window.attributes("-alpha", 0)
        return window

    try:
        with patch("notice_module.payload.notice_management_ui.NoticeStore") as store, \
                patch("tkinter.Toplevel", side_effect=invisible):
            store.return_value.snapshot.side_effect = lambda: deepcopy(state)
            print("creating management window", flush=True)
            ui = NoticeManagementWindow(app, Path("unused-mocked-path"))
            print("created management window", flush=True)
            notebook = ui.tabs
            for tab in notebook.tabs():
                notebook.select(tab)
                root.update()
                assert notebook.buttons[tab].cget("bg") == "#1e40af"
                assert notebook.body.cget("bg") == "#1e40af"
                ui._refresh()
                root.update()
            state["events"]["preview-test"] = {
                "id": "preview-test", "title": "TTS editor", "category": "general", "source_key": "",
                "source_url": "", "body": "", "tts_text": "로컬 미리듣기 문장", "enabled": True,
                "valid_from": "2026-01-01T00:00:00+09:00", "valid_until": "2999-01-01T00:00:00+09:00",
                "event_from": None, "event_until": None, "created_at": "2026-01-01T00:00:00+09:00",
                "revision": 1, "delivered_revision": None, "last_delivery": None, "manual_period": False,
            }
            ui._refresh()
            ui.tree.selection_set("preview-test")
            assert ui.tree['columns'][:3] == ('enabled', 'playback', 'title')
            assert ui.tree.heading('playback', 'text') == '재생 예정 / 조건 (KST)'
            assert ui.tree.set('preview-test', 'playback') == '01/01 00:00 이후 · 대기열 1회'
            assert ui.tree.heading("listen", "text") == "음성 듣기"
            assert ui.tree.set("preview-test", "listen") == "▶ 듣기"
            # All audio is mocked even when clicking the actual UI.
            ui._preview = Mock(worker=None, process=None)
            ui._preview.messages = __import__("queue").Queue()
            ui._listen_selected()
            ui._preview.start.assert_called_once_with("로컬 미리듣기 문장")
            ui._edit_tts()
            root.update()
            ui._tts_editor.destroy()  # Opening/closing must not synthesize or play anything.
            root.update()
            ui._open_tts_templates()
            root.update()
            from notice_module.payload.notice_templates import TEMPLATES
            assert len(ui._tts_templates.tree.get_children()) == len(TEMPLATES)
            for key in TEMPLATES:
                ui._tts_templates.tree.selection_set(key)
                ui._tts_templates.select()
                root.update()
            ui._tts_templates.window.destroy()
            root.update()
            ui.window.destroy()
            root.update()
            assert not errors, errors
    finally:
        root.destroy()
        faulthandler.cancel_dump_traceback_later()
    return 0


class NoticeUiTests(unittest.TestCase):
    def test_management_window_remains_responsive(self):
        result = subprocess.run([sys.executable, "-B", __file__, "--exercise"],
                                capture_output=True, text=True, timeout=15)
        if result.returncode == 77:
            self.skipTest(result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class NoticeUiActionTests(unittest.TestCase):
    """Action routing also runs without a working Tcl installation."""
    def setUp(self):
        from notice_module.payload.notice_management_ui import NoticeManagementWindow
        self.ui = ui = NoticeManagementWindow.__new__(NoticeManagementWindow)
        ui.tree = Mock()
        ui.tree.identify_region.return_value = "cell"
        ui.tree.identify_row.return_value = "one"
        ui.tree.identify_column.return_value = "#2"
        ui.tree.selection.return_value = ("one",)
        ui.events = {"one": {"id": "one", "title": "알림", "tts_text": "이전 문장"}}
        ui._ignore_release = False
        ui._preview_key = None
        ui._preview = Mock(worker=None)
        ui.preview_status = Mock()
        ui._guard = Mock(return_value=True)
        ui._edit_tts = Mock()
        ui._toggle = Mock()
        ui.window = Mock()
        ui.app = Mock()
        ui.store = Mock()
        ui.store.snapshot.return_value = {"events": {"one": {"id": "one", "title": "알림", "tts_text": "최신 문장"}}}
        self.event = SimpleNamespace(x=10, y=20)

    def test_double_click_row_opens_editor_not_playback(self):
        self.ui._event_double_click(self.event)
        self.ui._edit_tts.assert_called_once_with()
        self.ui._preview.start.assert_not_called()
        self.ui._toggle.assert_not_called()

    def test_listen_cell_uses_latest_text_and_same_cell_stops(self):
        ui = self.ui
        ui.tree.identify_column.return_value = "#7"
        ui._event_click(self.event)
        ui._preview.start.assert_called_once_with("최신 문장")
        ui._edit_tts.assert_not_called()
        ui._event_click(self.event)
        ui._preview.stop.assert_called_once_with()
        ui.store.complete_delivery.assert_not_called()

    def test_double_click_on_control_runs_only_first_click(self):
        ui = self.ui
        for column in ("#1", "#7"):
            ui.tree.identify_column.return_value = column
            ui._event_double_click(self.event)
            self.assertEqual(ui._event_click(self.event), "break")
        ui._preview.start.assert_not_called()
        ui._toggle.assert_not_called()
        ui._edit_tts.assert_not_called()

    def test_playback_and_state_columns_never_toggle_or_play(self):
        for column in ('#2', '#6'):
            self.ui.tree.identify_column.return_value = column
            self.ui._event_click(self.event)
        self.ui._preview.start.assert_not_called()
        self.ui._toggle.assert_not_called()

    def test_heading_or_empty_area_does_not_open_editor(self):
        ui = self.ui
        ui.tree.identify_region.return_value = "heading"
        ui._event_double_click(self.event)
        ui.tree.identify_region.return_value = "cell"
        ui.tree.identify_row.return_value = ""
        ui._event_double_click(self.event)
        ui._edit_tts.assert_not_called()

    def test_changed_server_blocks_list_preview(self):
        self.ui._guard.return_value = False
        self.ui._listen_selected()
        self.ui._preview.start.assert_not_called()

    def test_retired_notice_is_not_played(self):
        self.ui.store.snapshot.return_value["events"]["one"]["retired_at"] = "today"
        self.ui._listen_selected()
        self.ui._preview.start.assert_not_called()
        self.ui.app._show_centered_messagebox.assert_called_once()

    def test_preview_errors_use_centered_parent_dialog(self):
        self.ui._preview.start.side_effect = ValueError("처리 중")
        self.ui._listen_selected()
        self.assertEqual(self.ui.app._show_centered_messagebox.call_args.kwargs["parent"], self.ui.window)


if __name__ == "__main__":
    if "--exercise" in sys.argv:
        sys.exit(exercise_window())
    unittest.main()
