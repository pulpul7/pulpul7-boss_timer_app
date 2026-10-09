"""Banner lifecycle/IPC orchestration, independent of the main GUI and bot.

Only the host Tk loop is shared. Discord, schedule and audio logic are never
called here; native Windows notification history uses its own worker.
"""
from __future__ import annotations

import time
import tkinter as tk

from desktop_banner import BannerInbox, WindowsBanner, compact_text, notice_style


class DesktopBannerService:
    def __init__(self, root, app_root, channel_id, log, on_error):
        self.root, self.channel_id = root, channel_id
        self.log, self.on_error = log, on_error
        self.inbox = BannerInbox(app_root)
        self.native = WindowsBanner()
        self.visual = None
        self.enabled = False
        self.closed = False
        self.error_shown = False
        self.prepare_id = None
        self.poll_id = root.after(500, self._poll)

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        self.inbox.set_enabled(enabled)  # Do not change state if saving fails.
        self.enabled = enabled
        self.error_shown = False
        self.native.set_enabled(enabled)
        if self.visual is not None:
            self.visual.set_enabled(enabled)
        if enabled:
            self.native.prepare()
            if self.visual is None and self.prepare_id is None:
                self.prepare_id = self.root.after_idle(self._prepare)

    def _prepare(self):
        self.prepare_id = None
        if self.closed or not self.enabled or self.visual is not None:
            return
        try:
            # Bot imports desktop_banner only; it never loads Tk rendering.
            from desktop_banner_ui import SlidingBanner
            self.visual = SlidingBanner(self.root, on_error=self._visual_failed)
            self.visual.set_enabled(True)
        except (tk.TclError, OSError) as exc:
            self.log(f"desktop_banner_prepare_failed {type(exc).__name__}: {exc}")

    def show(self, title, body, *, style=None, created_at=None):
        if self.closed or not self.enabled:
            return
        notice = {"title": compact_text(title, 70), "body": compact_text(body, 180),
                  "style": style or notice_style({}, body),
                  "created_at": created_at if created_at is not None else time.time()}
        self._prepare()
        shown = self.visual is not None and self.visual.show(**notice)
        # Our card is the only popup. Windows still keeps notification history.
        self.native.submit(notice["title"], notice["body"], created_at=notice["created_at"], quiet=shown)

    def _visual_failed(self, exc, notices):
        self.log(f"desktop_banner_visual_failed {type(exc).__name__}: {exc}")
        for notice in notices:
            self.native.submit(notice["title"], notice["body"], created_at=notice["created_at"], quiet=False)

    def _poll(self):
        self.poll_id = None
        if self.closed:
            return
        try:
            if not self.root.winfo_exists():
                return
            for notice in self.inbox.drain(str(self.channel_id() or ""), self.enabled):
                self.show(notice["title"], notice["body"], style=notice.get("style"),
                          created_at=notice["created_at"])
            while not self.native.errors.empty():
                error = self.native.errors.get_nowait()
                self.log(f"desktop_banner_history_failed {error}")
                if (self.enabled and not self.error_shown
                        and (self.visual is None or self.visual.failed)):
                    self.error_shown = True
                    self.on_error()
        except tk.TclError:
            return
        except Exception as exc:
            self.log(f"desktop_banner_poll_failed {type(exc).__name__}: {exc}")
        if not self.closed:
            self.poll_id = self.root.after(500, self._poll)

    def close(self):
        if self.closed:
            return
        self.closed, self.enabled = True, False
        for after_id in (self.prepare_id, self.poll_id):
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
        if self.visual is not None:
            self.visual.close()
        self.native.close()
        try:
            self.inbox.set_enabled(False)
        except OSError:
            pass
