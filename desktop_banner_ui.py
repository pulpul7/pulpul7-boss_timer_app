"""Reusable, non-activating Tk banner windows. No Discord/audio dependencies."""
from __future__ import annotations

from collections import deque
import ctypes
from ctypes import wintypes
from datetime import datetime
import os
import re
import time
import tkinter as tk
from tkinter import font as tkfont

WIDTH, HEIGHT = 404, 172
HOLD_SECONDS = 10.0
MAX_CARDS = 3
TRANSPARENT = "#010203"


def ease_out(value: float) -> float:
    value = min(1.0, max(0.0, value))
    return 1.0 - (1.0 - value) ** 3


def banner_kind(body: str) -> tuple[str, str]:
    if re.search(r"(?:1|일)분\s*(?:전|남)", body):
        return "1분", "#fb7185"
    if re.search(r"5분\s*(?:전|남)", body):
        return "5분", "#fbbf24"
    if re.search(r"젠|타임|오픈\s*예정\s*시간", body):
        return "젠", "#4ade80"
    return "안내", "#38bdf8"


def wrap_spans(spans, measure, width: int, max_lines=3):
    """Wrap Korean/color runs by measured pixels, not arbitrary character counts."""
    rows, row, used = [], [], 0
    for span in spans:
        color = span.get("color", "#cbd5e1")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(color)):
            color = "#cbd5e1"
        bold = bool(span.get("bold"))
        for character in str(span.get("text") or ""):
            advance = measure(character, bold)
            if character == "\n" or (used + advance > width and row):
                rows.append(row)
                if len(rows) >= max_lines:
                    last = rows[-1]
                    while last and sum(measure(c, b) for c, _, b in last) + measure("…", False) > width:
                        last.pop()
                    last.append(("…", "#94a3b8", False))
                    return rows
                row, used = [], 0
                if character == "\n":
                    continue
            row.append((character, color, bold))
            used += advance
    if row:
        rows.append(row)
    return rows


def _rounded(canvas, x, y, right, bottom, radius=12, **options):
    points = (x+radius, y, right-radius, y, right, y, right, y+radius,
              right, bottom-radius, right, bottom, right-radius, bottom,
              x+radius, bottom, x, bottom, x, bottom-radius, x, y+radius, x, y)
    return canvas.create_polygon(points, smooth=True, splinesteps=24, **options)


def _user32():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetAncestor.argtypes, user.GetAncestor.restype = (wintypes.HWND, wintypes.UINT), wintypes.HWND
    user.GetWindowLongW.argtypes, user.GetWindowLongW.restype = (wintypes.HWND, ctypes.c_int), wintypes.LONG
    user.SetWindowLongW.argtypes, user.SetWindowLongW.restype = (wintypes.HWND, ctypes.c_int, wintypes.LONG), wintypes.LONG
    user.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT)
    user.SetWindowPos.restype = wintypes.BOOL
    user.SystemParametersInfoW.argtypes = (wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT)
    user.SystemParametersInfoW.restype = wintypes.BOOL
    return user


class _Card:
    def __init__(self, owner):
        self.owner = owner
        self.height = owner.height
        self.window = tk.Toplevel(owner.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg=TRANSPARENT)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.0)
        if os.name == "nt":
            self.window.attributes("-transparentcolor", TRANSPARENT)
        self.canvas = canvas = tk.Canvas(self.window, width=WIDTH, height=self.height,
            bg=TRANSPARENT, highlightthickness=0, bd=0, takefocus=False)
        canvas.pack()
        _rounded(canvas, 4, 6, WIDTH-1, self.height-1, fill="#080d18", outline="")
        _rounded(canvas, 1, 1, WIDTH-5, self.height-7, fill="#111b2c", outline="#2b3c55", width=1)
        self.accent = _rounded(canvas, 10, 17, 14, self.height-24, radius=2, fill="#38bdf8", outline="")
        canvas.create_text(25, 23, anchor="w", text="보스타이머  ·  안내",
                           font=owner.small_font, fill="#94a3b8")
        self.clock = canvas.create_text(WIDTH-59, 23, anchor="e", font=owner.small_font, fill="#64748b")
        canvas.create_text(WIDTH-31, 23, text="×", font=owner.title_font, fill="#94a3b8", tags="close")
        canvas.tag_bind("close", "<Button-1>", lambda _: self.dismiss())
        canvas.tag_bind("close", "<Enter>", lambda _: canvas.itemconfigure("close", fill="#ffffff"))
        canvas.tag_bind("close", "<Leave>", lambda _: canvas.itemconfigure("close", fill="#94a3b8"))
        _rounded(canvas, 24, 46, 63, 84, radius=10, fill="#1e3048", outline="#314763")
        self.badge = canvas.create_text(43, 65, font=owner.title_font, fill="#38bdf8")
        self.title = canvas.create_text(75, 49, anchor="nw", width=WIDTH-104,
            font=owner.title_font, fill="#f1f5f9")
        # Reuse text items and fonts rather than rebuilding widgets per notice.
        self.body_items = [canvas.create_text(0, 0, anchor="nw", state="hidden") for _ in range(48)]
        canvas.create_line(25, self.height-20, WIDTH-27, self.height-20, fill="#243349", width=2)
        self.progress = canvas.create_line(25, self.height-20, WIDTH-27, self.height-20, fill="#38bdf8", width=2)
        self.window.update_idletasks()
        self.hwnd = None
        if owner.user is not None:
            self.hwnd = owner.user.GetAncestor(self.window.winfo_id(), 2)
            style = owner.user.GetWindowLongW(self.hwnd, -20)
            owner.user.SetWindowLongW(self.hwnd, -20, style | 0x08000000 | 0x00000080)
        self.after_id = None
        self.active = False
        self.exiting = False
        self.y = 0.0
        self.target_y = 0.0
        self.from_y = 0.0
        self.motion_at = 0.0

    def show(self, notice):
        self.notice = notice
        title, body = notice["title"], notice["body"]
        badge, accent = banner_kind(body)
        self.canvas.itemconfigure(self.accent, fill=accent)
        self.canvas.itemconfigure(self.badge, text=badge, fill=accent)
        self.canvas.itemconfigure(self.progress, fill=accent)
        self.canvas.itemconfigure(self.clock, text=datetime.now().strftime("%H:%M"))
        title = re.sub(r"\s*(?:오전|오후)\s*\d+:\d+\s*$", "", title).strip("✦ \u2003")
        if self.owner.title_font.measure(title) > WIDTH-104:
            while title and self.owner.title_font.measure(title + "…") > WIDTH-104:
                title = title[:-1]
            title += "…"
        self.canvas.itemconfigure(self.title, text=title)
        spans = notice.get("style", {}).get("spans") or [{"text": body, "color": "#cbd5e1"}]
        rows = wrap_spans(spans, self.owner.measure, WIDTH-54)
        for item in self.body_items:
            self.canvas.itemconfigure(item, state="hidden")
        item_index = 0
        for row_index, row in enumerate(rows):
            x, run = 26, []
            for char, color, bold in row:
                if run and (run[-1][1], run[-1][2]) != (color, bold):
                    x = self._draw_run(item_index, run, x, 91 + row_index * self.owner.line_height)
                    item_index += 1
                    run = []
                run.append((char, color, bold))
            if run:
                self._draw_run(item_index, run, x, 91 + row_index * self.owner.line_height)
                item_index += 1
        self.active, self.exiting = True, False
        self.window.attributes("-alpha", 0.0)

    def start(self):
        # Enter near this card's own slot without crossing the cards below it.
        self.born = self.motion_at = time.perf_counter()
        self.y = self.from_y = self.target_y + 40
        self.window.deiconify()
        self._step()

    def _draw_run(self, index, run, x, y):
        text = "".join(char for char, _, _ in run)
        color, bold = run[0][1], run[0][2]
        if index < len(self.body_items):
            item = self.body_items[index]
            self.canvas.coords(item, x, y)
            self.canvas.itemconfigure(item, text=text, fill=color,
                font=self.owner.body_fonts[bold], state="normal")
        return x + self.owner.body_fonts[bold].measure(text)

    def retarget(self, y):
        if self.target_y == float(y):
            return
        self.from_y, self.target_y, self.motion_at = self.y, float(y), time.perf_counter()

    def dismiss(self):
        if self.active and not self.exiting:
            self.exiting = True
            self.exit_at, self.exit_y = time.perf_counter(), self.y

    def _step(self):
        if not self.active:
            return
        try:
            now = time.perf_counter()
            if self.exiting:
                fraction = min(1.0, (now-self.exit_at) / 0.22)
                self.y = self.exit_y + 18 * ease_out(fraction)
                opacity = 0.97 * (1.0-fraction)
                if fraction >= 1:
                    self.hide()
                    self.owner.recycle(self)
                    return
            else:
                move = ease_out((now-self.motion_at) / 0.45)
                self.y = self.from_y + (self.target_y-self.from_y)*move
                opacity = 0.97 * ease_out((now-self.born) / 0.45)
                remaining = max(0.0, 1.0-(now-self.born-0.45) / HOLD_SECONDS)
                self.canvas.coords(self.progress, 25, self.height-20,
                                   25+(WIDTH-52)*remaining, self.height-20)
                if remaining == 0:
                    self.dismiss()
            left, _, right, _ = self.owner.area()
            x = max(left+8, right-WIDTH-14)
            if self.hwnd is not None:
                self.owner.user.SetWindowPos(self.hwnd, wintypes.HWND(-1), x, round(self.y),
                    WIDTH, self.height, 0x0010 | 0x0040)  # NOACTIVATE | SHOWWINDOW
            else:
                self.window.geometry(f"{WIDTH}x{self.height}{x:+d}{round(self.y):+d}")
            self.window.attributes("-alpha", opacity)
            self.after_id = self.owner.root.after(16 if now-self.motion_at < 0.45 or self.exiting else 80, self._step)
        except (tk.TclError, OSError) as exc:
            self.owner._failed(exc)

    def hide(self):
        self.active = False
        if self.after_id is not None:
            try:
                self.owner.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        try:
            self.window.withdraw()
        except tk.TclError:
            pass


class SlidingBanner:
    """Owns the window pool; all calls/animations stay on the Tk thread."""
    def __init__(self, root, on_error=None):
        self.root, self.on_error = root, on_error
        self.enabled, self.failed = False, False
        self.pool, self.visible = [], []
        self.pending = deque(maxlen=64)
        self.prepare_id = None
        self.preparing = False
        self.cached_area = None
        self.user = _user32() if os.name == "nt" else None
        self.small_font = tkfont.Font(root=root, family="맑은 고딕", size=8)
        self.title_font = tkfont.Font(root=root, family="맑은 고딕", size=10, weight="bold")
        self.body_fonts = {bold: tkfont.Font(root=root, family="맑은 고딕", size=9,
                          weight="bold" if bold else "normal") for bold in (False, True)}
        self.line_height = max(16, *(font.metrics("linespace") for font in self.body_fonts.values()))
        self.height = max(HEIGHT, 91 + 3*self.line_height + 28)
        self.glyph_widths = {}

    def measure(self, char, bold):
        key = (char, bold)
        if key not in self.glyph_widths:
            if len(self.glyph_widths) >= 2048:
                self.glyph_widths.clear()
            self.glyph_widths[key] = self.body_fonts[bold].measure(char)
        return self.glyph_widths[key]

    def area(self):
        if self.cached_area is None:
            rectangle = wintypes.RECT()
            if self.user is not None and self.user.SystemParametersInfoW(0x0030, 0, ctypes.byref(rectangle), 0):
                self.cached_area = (rectangle.left, rectangle.top, rectangle.right, rectangle.bottom)
            else:
                self.cached_area = (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        return self.cached_area

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if self.enabled:
            self.prepare()
        else:
            self.pending.clear()
            for card in self.visible:
                card.hide()
                self.pool.append(card)
            self.visible.clear()

    def prepare(self):
        if self.enabled and not self.failed and not self.preparing and self.prepare_id is None:
            self.prepare_id = self.root.after_idle(self._prepare_one)

    def _prepare_one(self):
        self.prepare_id = None
        if not self.enabled or self.failed or len(self.pool)+len(self.visible) >= MAX_CARDS:
            return
        try:
            self.preparing = True
            self.pool.append(self._new_card())
            self.area()
            for char in "0123456789분전남았습니다젠타임안내보스 .…":
                for bold in (False, True):
                    self.measure(char, bold)
            self.prepare_id = self.root.after(25, self._prepare_one)
        except (tk.TclError, OSError) as exc:
            self._failed(exc)
        finally:
            self.preparing = False

    def _new_card(self):
        card = _Card.__new__(_Card)
        try:
            card.__init__(self)
            return card
        except (tk.TclError, OSError):
            if getattr(card, "window", None) is not None:
                try:
                    card.window.destroy()
                except tk.TclError:
                    pass
            raise

    def show(self, title, body, *, style=None, created_at=None):
        if not self.enabled or self.failed:
            return False
        notice = {"title": title, "body": body, "style": style or {},
                  "created_at": created_at if created_at is not None else time.time()}
        self.pending.append(notice)
        self.cached_area = None
        self._pump()
        return not self.failed

    def _pump(self):
        while self.enabled and self.pending and len(self.visible) < MAX_CARDS and not self.failed:
            # Keep surviving cards in their slots. If there is no room above
            # them, wait instead of filling a lower gap or moving them down.
            slot = max((card.slot for card in self.visible), default=-1) + 1
            if slot >= MAX_CARDS:
                break
            notice = self.pending.popleft()
            if time.time()-notice["created_at"] > 30:
                continue
            try:
                card = self.pool.pop() if self.pool else self._new_card()
                card.slot = slot
                self.visible.append(card)
                card.show(notice)
                self._layout()
                card.start()
            except (tk.TclError, OSError) as exc:
                self._failed(exc)

    def _layout(self):
        _, top, _, bottom = self.area()
        for card in self.visible:
            card.retarget(max(top+8, bottom-self.height-12-card.slot*(self.height+8)))

    def recycle(self, card):
        if card in self.visible:
            self.visible.remove(card)
            self.pool.append(card)
        self._pump()

    def _failed(self, exc):
        if self.failed:
            return
        self.failed = True
        notices = list(self.pending) + [card.notice for card in self.visible if hasattr(card, "notice")]
        self.pending.clear()
        for card in self.visible:
            card.hide()
            self.pool.append(card)
        self.visible.clear()
        if self.on_error:
            self.on_error(exc, notices)

    def close(self):
        self.set_enabled(False)
        if self.prepare_id is not None:
            try:
                self.root.after_cancel(self.prepare_id)
            except tk.TclError:
                pass
            self.prepare_id = None
        for card in self.pool:
            try:
                card.window.destroy()
            except tk.TclError:
                pass
        self.pool.clear()
