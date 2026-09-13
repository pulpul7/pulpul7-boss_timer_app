"""Bounded, invisible Tk integration checks; no application, network or user data."""
from copy import deepcopy
from pathlib import Path
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
            notebook = next(child for child in ui.window.winfo_children() if isinstance(child, ttk.Notebook))
            for tab in notebook.tabs():
                notebook.select(tab)
                root.update()
                ui._refresh()
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


if __name__ == "__main__":
    if "--exercise" in sys.argv:
        sys.exit(exercise_window())
    unittest.main()
