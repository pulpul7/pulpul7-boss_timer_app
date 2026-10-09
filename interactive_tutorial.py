"""Game-style spotlight tutorials attached to actual application controls.

Only explicitly allowed window navigation runs; target clicks act as Next. Data-changing
commands are intercepted; background alarms and bot operation remain intact.
"""
from __future__ import annotations

import time
import sys
from functools import lru_cache
import tkinter as tk
from tkinter import ttk

from tutorial_content import CHAPTERS, validate_content
from tutorial_live_ui import VIEWS, MAIN_OPENERS, route, find_window, find_target


BG = "#eff6ff"
INK = "#0f172a"
BLUE = "#1d4ed8"
HIGHLIGHT_RED = "#ff2438"
HIGHLIGHT_WIDTH = 4
HIGHLIGHT_BLINK_SECONDS = .6
TRANSPARENT_COLOR = "#010203"
COACH_WIDTH = 410
COACH_HEIGHT = 540


@lru_cache(maxsize=1)
def _native_api():
    """Own-window placement only; support monitors left/above primary screen."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    api = ctypes.WinDLL("user32", use_last_error=True)
    api.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    api.GetAncestor.restype = wintypes.HWND
    api.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
    api.SetWindowPos.restype = wintypes.BOOL
    api.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    api.MonitorFromWindow.restype = wintypes.HANDLE
    api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    api.GetMonitorInfoW.restype = wintypes.BOOL
    api.GetForegroundWindow.argtypes = []
    api.GetForegroundWindow.restype = wintypes.HWND
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetWindowThreadProcessId.restype = wintypes.DWORD
    api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    api.GetWindowRect.restype = wintypes.BOOL
    return api, ctypes, MonitorInfo


def _move_window(win, width, height, x, y):
    # Tk interprets a negative geometry offset relative to the right/bottom edge,
    # rather than as an absolute coordinate on a monitor left of primary.
    win.geometry(f"{int(width)}x{int(height)}+{max(0, int(x))}+{max(0, int(y))}")
    if x < 0 or y < 0:
        native = _native_api()
        if native is not None:
            api, ctypes, _ = native
            win.update_idletasks()
            hwnd = api.GetAncestor(win.winfo_id(), 2) or win.winfo_id()
            # Keep size, stacking and activation unchanged.
            if not api.SetWindowPos(hwnd, None, int(x), int(y), 0, 0, 0x0001 | 0x0004 | 0x0010):
                raise ctypes.WinError(ctypes.get_last_error())


def _work_area(win):
    native = _native_api()
    if native is not None:
        api, ctypes, Info = native
        hwnd = api.GetAncestor(win.winfo_id(), 2) or win.winfo_id()
        monitor = api.MonitorFromWindow(hwnd, 2)
        info = Info()
        info.cbSize = ctypes.sizeof(info)
        if api.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right - r.left, r.bottom - r.top
    return 0, 0, win.winfo_screenwidth(), win.winfo_screenheight()


def _clamp_position(x, y, size, area):
    ax, ay, aw, ah = area
    width, height = size
    return max(ax, min(x, ax + max(0, aw - width))), max(ay, min(y, ay + max(0, ah - height)))


def _application_foreground(win):
    native = _native_api()
    if native is None:
        return True
    api, ctypes, _ = native
    foreground = api.GetForegroundWindow()
    if not foreground:
        return False
    own_pid, active_pid = ctypes.c_ulong(), ctypes.c_ulong()
    api.GetWindowThreadProcessId(win.winfo_id(), ctypes.byref(own_pid))
    api.GetWindowThreadProcessId(foreground, ctypes.byref(active_pid))
    return own_pid.value == active_pid.value


def spotlight_rectangles(bounds, target):
    """Four disjoint dark rectangles and a clipped, bright target rectangle."""
    x, y, w, h = bounds
    tx, ty, tw, th = target
    left, top = max(x, tx), max(y, ty)
    right, bottom = min(x + w, tx + tw), min(y + h, ty + th)
    if right <= left or bottom <= top:
        return [(x, y, w, h)], None
    masks = [(x, y, w, top - y), (x, bottom, w, y + h - bottom),
             (x, top, left - x, bottom - top),
             (right, top, x + w - right, bottom - top)]
    return [r for r in masks if r[2] > 0 and r[3] > 0], (left, top, right - left, bottom - top)


def shade_rectangles(bounds, exclusions=()):
    """Cut foreground windows out of background masks, regardless of stacking."""
    rectangles = [bounds] if bounds[2] > 0 and bounds[3] > 0 else []
    for exclusion in exclusions:
        rectangles = [part for rect in rectangles
                      for part in spotlight_rectangles(rect, exclusion)[0]]
    return rectangles


def _frame_bounds(win):
    """Include the real title bar/borders when excluding a foreground dialog."""
    native = _native_api()
    if native is not None:
        from ctypes import wintypes
        api, ctypes, _ = native
        rect = wintypes.RECT()
        hwnd = api.GetAncestor(win.winfo_id(), 2) or win.winfo_id()
        if api.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    return win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()


def highlight_alpha(elapsed):
    """Blink the outline only; the target and its click shield stay in place."""
    return 1.0 if int(max(0.0, elapsed) / HIGHLIGHT_BLINK_SECONDS) % 2 == 0 else .12


def place_coach(bounds, target, size):
    """Choose a position avoiding the target, constrained to its owner window."""
    x, y, w, h = bounds
    tx, ty, tw, th = target
    cw, ch = size
    gap = 14
    candidates = [(tx + tw + gap, ty), (tx - cw - gap, ty),
                  (tx, ty + th + gap), (tx, ty - ch - gap),
                  (x + w - cw - 12, y + h - ch - 12), (x + 12, y + 12)]
    def overlap(px, py):
        return max(0, min(px + cw, tx + tw) - max(px, tx)) * max(0, min(py + ch, ty + th) - max(py, ty))
    clamped = [(max(x, min(px, x + max(0, w - cw))),
                max(y, min(py, y + max(0, h - ch)))) for px, py in candidates]
    return min(clamped, key=lambda p: overlap(*p))


def _available(widget):
    try:
        return widget is not None and bool(widget.winfo_exists())
    except tk.TclError:
        return False


def _walk(widget):
    yield widget
    # Tcl-created combobox popdowns have no Python Widget objects.
    for child in tuple(widget.children.values()):
        yield from _walk(child)


class _Spotlight:
    """Alpha masks never recolor/reparent the actual controls.

    A nearly transparent window over the bright hole catches clicks so even an
    Apply/Delete/Checkbutton target cannot run the original command.
    """
    def __init__(self, owner, advance, *, full=False, stop=None):
        self.owner = owner
        self.advance = advance
        self.full = full
        self.stop = stop
        self.target_pressed = False
        self.windows = []
        self.masks = [self._window("#020617", .58) for _ in range(4)]
        self.hit = self._window("#ffffff", .01)
        self.hit.configure(cursor="hand2")
        self.hit.bind("<Button-1>", self._press)
        self.hit.bind("<ButtonRelease-1>", self._click)
        self.outline_window = None
        self.outline_canvas = None
        self.outline_item = None
        self.borders = []
        if not full and sys.platform == "win32":
            # Use the same color-key Canvas approach as RegionOverlay. One
            # normal-sized window avoids relying on four 4-pixel-wide windows.
            self.outline_window = self._window(TRANSPARENT_COLOR, 1.0)
            self.outline_window.attributes("-transparentcolor", TRANSPARENT_COLOR)
            self.outline_canvas = tk.Canvas(self.outline_window, bg=TRANSPARENT_COLOR,
                                            highlightthickness=0, bd=0, cursor="hand2")
            self.outline_canvas.pack(fill="both", expand=True)
            self.outline_item = self.outline_canvas.create_rectangle(
                0, 0, 1, 1, outline=HIGHLIGHT_RED, width=HIGHLIGHT_WIDTH)
        elif not full:
            self.borders = [self._window(HIGHLIGHT_RED, 1.0) for _ in range(4)]
        for border in ([self.outline_canvas] if self.outline_canvas is not None else self.borders):
            border.bind("<Button-1>", self._press)
            border.bind("<ButtonRelease-1>", self._click)
        self.signature = None
        self.outline_visible = False
        self.outline_started_at = 0.0
        self.outline_alpha = None

    def _window(self, color, alpha):
        win = tk.Toplevel(self.owner)
        win.withdraw()
        # Withdraw before setting styles avoids flashes of empty windows.
        win.overrideredirect(True)
        win.configure(bg=color, takefocus=False)
        win.transient(self.owner)
        win.attributes("-topmost", True)
        win.attributes("-alpha", alpha)
        win.bind("<Button-1>", lambda _: "break")
        win.bind("<ButtonRelease-1>", lambda _: "break")
        win.bind("<Button-3>", lambda _: "break")
        win.bind("<MouseWheel>", lambda _: "break")
        win.bind("<KeyPress>", self._key)
        self.windows.append(win)
        return win

    def _press(self, _event):
        self.target_pressed = True
        return "break"

    def _click(self, _event):
        # Advance on release. Ending on press could let the release reach an
        # unblocked real Treeview (whose cut action is bound to button release).
        if self.target_pressed:
            self.target_pressed = False
            self.last_click_xy = (_event.x_root, _event.y_root)
            self.advance()
        return "break"

    def _key(self, event):
        if event.keysym == "Escape" and self.stop is not None:
            self.stop()
        return "break"

    @staticmethod
    def _place(win, rect):
        x, y, w, h = (int(v) for v in rect)
        if w <= 0 or h <= 0:
            win.withdraw()
            return
        _move_window(win, w, h, x, y)
        win.deiconify()
        # Commit mapping/geometry before caching its coordinates. Lift without
        # taking keyboard focus, including when the owner is itself topmost.
        win.update_idletasks()
        win.lift()

    def hide(self):
        for win in self.windows:
            if _available(win):
                win.withdraw()
        self.signature = None
        self.outline_visible = False
        self.outline_alpha = None

    def _blink_outline(self):
        if not self.outline_visible:
            return
        alpha = highlight_alpha(time.monotonic() - self.outline_started_at)
        if alpha == self.outline_alpha:
            return
        if self.outline_canvas is not None:
            # Changing only the drawing preserves Windows color-key transparency
            # and the independent hit shield; blinking cannot trigger real clicks.
            self.outline_canvas.itemconfigure(self.outline_item,
                                              state="normal" if alpha == 1.0 else "hidden")
        else:
            for border in self.borders:
                border.attributes("-alpha", alpha)
        self.outline_alpha = alpha

    def update(self, bounds, target, *, exclusions=()):
        exclusions = tuple(exclusions)
        signature = (bounds, target, exclusions)
        if signature == self.signature:
            # Reuse the existing position timer. Do not move/lift the windows
            # or flash the actual text when only the outline's phase changes.
            self._blink_outline()
            return False
        if not self.outline_visible:
            self.outline_started_at = time.monotonic()
        self.signature = signature
        if self.full or target is None:
            rectangles, hole = shade_rectangles(bounds, exclusions), None
        else:
            rectangles, hole = spotlight_rectangles(bounds, target)
        # Two overlapping foreground windows can require more than four
        # disjoint rectangles. Reuse the pool and hide every unused mask.
        while len(self.masks) < len(rectangles):
            self.masks.append(self._window("#020617", .58))
        for index, mask in enumerate(self.masks):
            if index < len(rectangles):
                self._place(mask, rectangles[index])
            else:
                mask.withdraw()
        if hole is None:
            self.outline_visible = False
            self.hit.withdraw()
            for border in self.borders:
                border.withdraw()
            if self.outline_window is not None:
                self.outline_window.withdraw()
            return True
        x, y, w, h = hole
        self._place(self.hit, hole)
        thickness = min(HIGHLIGHT_WIDTH, w, h)
        if self.outline_window is not None:
            inset = thickness / 2
            self.outline_canvas.coords(self.outline_item, inset, inset, w - inset, h - inset)
            self.outline_canvas.itemconfigure(self.outline_item, width=thickness)
            self._place(self.outline_window, hole)
        else:
            border_rects = [(x, y, w, thickness), (x, y + h - thickness, w, thickness),
                            (x, y, thickness, h), (x + w - thickness, y, thickness, h)]
            for win, rect in zip(self.borders, border_rects):
                self._place(win, rect)
        self.outline_visible = True
        self._blink_outline()
        return True

    def close(self):
        self.outline_visible = False
        for win in self.windows:
            if _available(win):
                win.destroy()
        self.windows.clear()


class TutorialController:
    def __init__(self, app):
        validate_content()
        self.app = app
        self.root = app.root
        self.main = app.schedule_window
        self.font = getattr(app, "current_font_family", "맑은 고딕")
        self.running = False
        self.closed = False
        self.chapter = None
        self.index = 0
        self.list_window = None
        self.coach = None
        self.live_windows = {}
        self.window_states = {}
        self.initial_windows = set()
        self.opening = set()
        self.opened_views = set()
        self.capture_allowed = False
        self.initial_flags = {}
        self.input_filter_values = {}
        self.authorized_windows = set()
        self.protocols = {}
        self.initial_notice_tab = None
        self.failed_views = set()
        self.notice_active_key = None
        self.suspended = False
        self.spotlight = None
        self.main_shade = None
        self.after_id = None
        self.current_target = None
        self.detail_open = False
        self.last_navigation = 0.0
        self.bindtag = f"BossTimerTutorial_{id(self)}"
        self.block_bindings = {}
        self.blocked = set()
        self.last_scan = 0.0
        self.extra_shades = {}
        self.layout_signature = None
        self.target_debug_signature = None
        self.previous_focus = self.root.focus_get()
        self.destroy_bind = self.main.bind("<Destroy>", self._main_destroyed, add="+")

    def _window(self, title, parent=None):
        win = tk.Toplevel(parent or self.main)
        win.withdraw()
        win.title(title)
        win.configure(bg=BG)
        win.transient(parent or self.main)
        win.resizable(False, False)
        win.bind("<Escape>", lambda _: self.finish())
        return win

    def _button(self, parent, text, command, *, primary=False):
        return tk.Button(parent, text=text, command=command, font=(self.font, 10, "bold"),
                         bg=BLUE if primary else "#e2e8f0", fg="white" if primary else INK,
                         activebackground="#1e40af" if primary else "#cbd5e1",
                         activeforeground="white" if primary else INK,
                         relief="flat", bd=0, padx=12, pady=7, cursor="hand2")

    def _title_band(self, win, title, subtitle):
        frame = tk.Frame(win, bg="#1e3a8a", padx=18, pady=12)
        frame.pack(fill="x")
        tk.Label(frame, text=title, bg="#1e3a8a", fg="white", anchor="w",
                 font=(self.font, 16, "bold")).pack(fill="x")
        tk.Label(frame, text=subtitle, bg="#1e3a8a", fg="#dbeafe", anchor="w",
                 font=(self.font, 10)).pack(fill="x", pady=(5, 0))

    def _center(self, win, width, height):
        self.main.update_idletasks()
        x = self.main.winfo_rootx() + (self.main.winfo_width() - width) // 2
        y = self.main.winfo_rooty() + (self.main.winfo_height() - height) // 2
        x, y = _clamp_position(x, y, (width, height), _work_area(self.main))
        _move_window(win, width, height, x, y)

    def show_list(self):
        if self.closed:
            return
        if self.running:
            if _available(self.coach):
                self.coach.lift()
                self.coach.focus_set()
            return
        if _available(self.list_window):
            self.list_window.lift()
            self.list_window.focus_set()
            return
        win = self.list_window = self._window("보스타이머 튜토리얼")
        win.protocol("WM_DELETE_WINDOW", self.finish)
        self._title_band(win, "매일 할 일은 스샷 → OCR → 확인 → 적용", "처음에는 1 · 3 · 12번을 추천합니다. 설정은 필요할 때만 확인하세요.")
        tk.Label(win, text="실제 화면에서 강조한 버튼을 누르면 다음 단계로 안내합니다.\n"
                 "빨간 표시를 클릭하면 다음 단계로 이동합니다. 캡처·OCR·적용·설정 저장은 실행하지 않습니다.",
                 bg="#fef3c7", fg="#92400e", justify="left", anchor="w", padx=16, pady=10,
                 font=(self.font, 10)).pack(fill="x")
        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)
        self.chapter_list = tk.Listbox(body, activestyle="none", exportselection=False,
                                      bg="white", fg=INK, selectbackground=BLUE,
                                      selectforeground="white", font=(self.font, 11),
                                      relief="solid", bd=1, height=13)
        scroll = tk.Scrollbar(body, command=self.chapter_list.yview)
        self.chapter_list.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.chapter_list.pack(fill="both", expand=True)
        for chapter in CHAPTERS:
            suffix = "  ★ 추천" if chapter.recommended else ""
            self.chapter_list.insert("end", chapter.title + suffix)
        self.chapter_list.selection_set(0)
        self.chapter_list.bind("<<ListboxSelect>>", self._list_selection)
        self.chapter_list.bind("<Double-Button-1>", lambda _: self._start_selected())
        self.chapter_list.bind("<Return>", lambda _: self._start_selected())
        self.list_description = tk.Label(win, bg=BG, fg="#334155", anchor="w", justify="left",
                                         font=(self.font, 10), wraplength=680)
        self.list_description.pack(fill="x", padx=18, pady=(0, 8))
        footer = tk.Frame(win, bg=BG)
        footer.pack(fill="x", padx=18, pady=(0, 14))
        self._button(footer, "선택한 튜토리얼 시작", self._start_selected, primary=True).pack(side="right")
        self._button(footer, "닫기", self.finish).pack(side="left")
        self._list_selection()
        self._center(win, 730, 610)
        win.deiconify()
        win.lift()
        self.chapter_list.focus_set()

    def _list_selection(self, _event=None):
        selected = self.chapter_list.curselection()
        if selected:
            chapter = CHAPTERS[selected[0]]
            self.list_description.configure(text=f"{chapter.summary}\n{len(chapter.steps)}단계 · 언제든 종료하고 다시 시작할 수 있습니다.")

    def _start_selected(self):
        selected = self.chapter_list.curselection()
        if not selected:
            return
        # Do not pause or cancel live work to run a tutorial.
        if (getattr(self.app, "_precision_session", None) is not None
                or getattr(self.app, "schedule_input_ocr_worker_active", False)
                or getattr(self.app, "schedule_input_ocr_addon_busy", False)
                or getattr(self.app, "schedule_input_window_busy", False)
                or getattr(self.app, "discord_handover_busy", False)):
            self.list_description.configure(text="현재 캡처·입력·승계 작업을 마친 뒤 시작해 주세요. 진행 중인 작업은 그대로 유지합니다.")
            return
        if self._external_grab():
            self.list_description.configure(text="열려 있는 확인 / 설정 창을 닫은 뒤 시작해 주세요.")
            return
        self.chapter = CHAPTERS[selected[0]]
        self.index = 0
        self.list_window.withdraw()
        self.running = True
        self.initial_windows = set(self._application_windows())
        self.window_states = {w: w.state() for w in self.initial_windows}
        self.initial_flags = {k: v for k, v in vars(self.app).items()
                              if (k.endswith("_window_open") or k in {"schedule_input_ocr_addon_open", "log_panel_open"}) and isinstance(v, bool)}
        self.failed_views.clear()
        self.opened_views.clear()
        self.notice_active_key = None
        self.initial_notice_tab = None
        self.suspended = False
        self.last_navigation = 0.0
        self.input_filter_values.clear()
        for name in ("schedule_input_ocr_within_day_only_var",):
            variable = getattr(self.app, name, None)
            if variable is not None:
                self.input_filter_values[name] = variable.get()
        try:
            self._install_blocking()
            self._build_coach()
            self._show_step()
            self._tick()
        except Exception as exc:
            # A failed overlay setup must never leave the app unclickable.
            self.finish()
            self.app._show_centered_messagebox(
                "showerror", "튜토리얼", f"튜토리얼 화면을 준비하지 못했습니다. 기존 자료는 유지합니다.\n{exc}",
                parent=self.main,
            )

    def _build_coach(self):
        win = self.coach = self._window("튜토리얼 · 실제 자료 변경 없음")
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self.finish)
        win.configure(highlightbackground=BLUE, highlightthickness=2)
        self.coach_header = tk.Label(win, bg="#1e3a8a", fg="white", anchor="w",
                                     font=(self.font, 11, "bold"), padx=14, pady=10)
        self.coach_header.pack(fill="x")
        self.step_picker = ttk.Combobox(win, state="readonly", font=(self.font, 10),
                                       values=[f"{i + 1}. {step.title}" for i, step in enumerate(self.chapter.steps)])
        self.step_picker.pack(fill="x", padx=14, pady=(8, 0))
        self.step_picker.bind("<<ComboboxSelected>>", self._choose_step)
        self.step_title = tk.Label(win, bg=BG, fg=INK, font=(self.font, 13, "bold"),
                                   anchor="w", justify="left", wraplength=374)
        self.step_title.pack(fill="x", padx=14, pady=(12, 8))
        frame = tk.Frame(win, bg=BG)
        frame.pack(fill="both", expand=True, padx=14)
        self.explanation = tk.Text(frame, height=8, wrap="word", font=(self.font, 11),
                                   bg=BG, fg=INK, relief="flat", padx=2, pady=4,
                                   state="disabled", takefocus=True)
        scrollbar = tk.Scrollbar(frame, command=self.explanation.yview)
        self.explanation.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.explanation.pack(fill="both", expand=True)
        # Reserve navigation space first; long copy is scrollable, never clips Exit.
        frame.pack_forget()
        footer = tk.Frame(win, bg=BG)
        footer.pack(side="bottom", fill="x")
        self.context_label = tk.Label(footer, bg="#dbeafe", fg="#1e3a8a", anchor="w", justify="left",
                                      wraplength=374, font=(self.font, 9), padx=14, pady=7)
        self.context_label.pack(fill="x", pady=(8, 0))
        self.detail_button = self._button(footer, "자세히 보기", self._toggle_detail)
        self.detail_button.pack(anchor="w", padx=14, pady=(8, 0))
        nav = tk.Frame(footer, bg=BG)
        nav.pack(fill="x", padx=14, pady=10)
        self.previous_button = self._button(nav, "이전", lambda: self.navigate(-1))
        self.previous_button.pack(side="left")
        self.next_button = self._button(nav, "다음", lambda: self.navigate(1), primary=True)
        self.next_button.pack(side="right")
        self._button(nav, "종료", self.finish).pack(side="right", padx=5)
        bottom = tk.Frame(footer, bg=BG)
        bottom.pack(fill="x", padx=14, pady=(0, 10))
        self._button(bottom, "처음부터", self.restart).pack(side="left")
        self._button(bottom, "목록", self.back_to_list).pack(side="right")
        frame.pack(fill="both", expand=True, padx=14)
        win.bind("<Left>", lambda _: self.navigate(-1))
        win.bind("<Right>", lambda _: self.navigate(1))
        win.geometry(f"{COACH_WIDTH}x{COACH_HEIGHT}")

    def _choose_step(self, _event=None):
        selected = self.step_picker.current()
        if selected >= 0:
            self.index = selected
            self._show_step()

    def _toggle_detail(self):
        self.detail_open = not self.detail_open
        self._set_explanation()

    def _set_explanation(self):
        step = self.chapter.steps[self.index]
        text = step.body
        if self.detail_open and step.detail:
            text += "\n\n" + step.detail
        self.explanation.configure(state="normal")
        self.explanation.delete("1.0", "end")
        self.explanation.insert("1.0", text)
        self.explanation.configure(state="disabled")
        self.explanation.yview_moveto(0)
        self.detail_button.configure(text="자세히 접기" if self.detail_open else "자세히 보기",
                                     state="normal" if step.detail else "disabled")

    def navigate(self, direction):
        if not self.running:
            return "break"
        now = time.monotonic()
        # Avoid accidental double click skipping two adjacent targets.
        if now - self.last_navigation < .25:
            return "break"
        self.last_navigation = now
        index = self.index + direction
        if index >= len(self.chapter.steps):
            self.back_to_list(completed=True)
        elif index >= 0:
            self.index = index
            self._show_step()
        return "break"

    def restart(self):
        self.index = 0
        self._show_step()

    def _resolve_main_target(self, key):
        aliases = {
            "추가": "schedule_tree_add_button", "수정": "schedule_tree_modify_button",
            "행삭제": "schedule_tree_delete_button", "행복구": "schedule_tree_restore_button",
            "삭제": "schedule_tree_cut_button", "복구": "schedule_tree_cut_time_button",
            "스케쥴 복사": "schedule_share_copy_button", "TXT": "schedule_share_txt_button",
        }
        if key in aliases:
            key = "@" + aliases[key]
        if key.startswith("@"):
            widget = getattr(self.app, key[1:], None)
            return widget if _available(widget) else None
        # Registry keeps the Discord button identifiable as its live text changes.
        widget = getattr(self.app, "schedule_tutorial_targets", {}).get(key)
        if _available(widget):
            return widget
        for candidate in _walk(self.main):
            if isinstance(candidate, (tk.Button, tk.Checkbutton, tk.Label, ttk.Button, ttk.Checkbutton)):
                try:
                    if (candidate.winfo_toplevel() is self.main
                            and str(candidate.cget("text")).strip() == key and candidate.winfo_viewable()):
                        return candidate
                except tk.TclError:
                    pass
        return None

    def _show_step(self):
        try:
            self._render_step()
        except Exception as exc:
            self.finish()
            self.app._show_centered_messagebox(
                "showerror", "튜토리얼", f"다음 설명을 표시하지 못해 튜토리얼을 종료했습니다.\n{exc}",
                parent=self.main,
            )

    def _render_step(self):
        step = self.chapter.steps[self.index]
        self.detail_open = False
        self.coach_header.configure(text=f"{self.chapter.title}\n{self.index + 1} / {len(self.chapter.steps)}")
        self.step_picker.current(self.index)
        self.step_title.configure(text=step.title)
        self.previous_button.configure(state="normal" if self.index else "disabled")
        self.next_button.configure(text="완료" if self.index == len(self.chapter.steps) - 1 else "다음")
        self._set_explanation()
        self._clear_layers()
        self.view, self.target_key = route(step.view, step.target)
        self._leave_modal_views(self.view)
        self.current_target = None
        self.owner = self.main
        self._ensure_live_view(self.view)
        self._attach_live_target()
        self._scan_widgets()
        self.layout_signature = None
        self.coach.deiconify()
        self.coach.lift()
        self._position()

    def _application_windows(self):
        return [w for w in _walk(self.root) if isinstance(w, (tk.Tk, tk.Toplevel))
                and not self._owned(w)]

    def _remember_window(self, win):
        if win not in self.window_states:
            self.window_states[win] = "withdrawn"
        self.authorized_windows.add(win)
        if win not in self.protocols:
            original = win.protocol("WM_DELETE_WINDOW")
            win.protocol("WM_DELETE_WINDOW", self.finish)
            self.protocols[win] = (original, win.protocol("WM_DELETE_WINDOW"))
        # The guide owns navigation, so a real dialog's modal grab must not
        # exclude its coach. Its original physical commands stay blocked.
        try:
            if str(self.root.tk.call("grab", "current", self.root._w)) == str(win):
                win.grab_release()
        except tk.TclError:
            pass
        win.deiconify()
        win.lift()

    def _ensure_live_view(self, key):
        if key == "main":
            return self.main
        cached = self.live_windows.get(key)
        if _available(cached):
            self._activate_live_window(key, cached)
            return cached
        spec = VIEWS[key]
        if spec.parent != "main":
            self._ensure_live_view(spec.parent)
        win = find_window(self.app, key, self._application_windows())
        if _available(win):
            self.live_windows[key] = win
            self._activate_live_window(key, win)
            return win
        if key in self.opening or key in self.failed_views:
            return None
        self.opening.add(key)
        # Schedule rather than call directly: wait_window() enters a nested Tk
        # loop, where _tick must already be able to attach the live spotlight.
        self.root.after(0, lambda k=key: self._open_live_view(k))
        return None

    def _activate_live_window(self, key, win):
        self.opened_views.add(key)
        self._remember_window(win)
        flag = {"capture": "schedule_input_ocr_addon_open", "log_records": "log_panel_open"}.get(key, VIEWS[key].attribute + "_open")
        if isinstance(getattr(self.app, flag, None), bool):
            setattr(self.app, flag, True)
        if key == "capture":
            self.app._position_schedule_input_ocr_addon_window()
            self.app._update_schedule_input_ocr_addon_controls()
        if key == "log_records":
            self.app._position_log_panel()
        if key == "notice" and win in self.initial_windows and self.initial_notice_tab is None:
            manager = self._notice_manager()
            if manager is not None:
                self.initial_notice_tab = manager.tabs.current

    def _notice_manager(self):
        runtime = getattr(self.app, "notice_runtime", None)
        session = getattr(runtime, "session", None)
        return getattr(getattr(session, "plugin", None), "window", None)

    def _open_live_view(self, key):
        if not self.running:
            self.opening.discard(key)
            return
        spec = VIEWS[key]
        try:
            if key == "notice_templates":
                owner = self.live_windows.get("notice")
                button = find_target(self.app, owner, "notice", "안내 문구 기본 설정 · 모든 유형") if _available(owner) else None
                if button is not None:
                    button.invoke()
                else:
                    self.failed_views.add(key)
            elif key != "notice_edit":
                opener = getattr(self.app, spec.opener, None)
                if opener is not None:
                    if key == "season":
                        opener(parent=self.main)
                    else:
                        opener()
            else:
                manager = self._notice_manager()
                if manager is not None and manager._selected():
                    # Open the selected row's real editor. Saving remains blocked.
                    manager._edit_tts()
                else:
                    self.failed_views.add(key)
            win = find_window(self.app, key, self._application_windows())
            if _available(win):
                self.live_windows[key] = win
                self._activate_live_window(key, win)
                if key == "input":
                    self.app.schedule_input_window_open = True
                    self.app._position_schedule_input_window()
                if key == "capture":
                    self.app.schedule_input_ocr_addon_open = True
                    self.app._position_schedule_input_ocr_addon_window()
            elif key not in self.opened_views:
                self.failed_views.add(key)
            if self.running:
                self._attach_live_target()
        except Exception as exc:
            self.failed_views.add(key)
            if self.running and _available(self.context_label):
                self.context_label.configure(text=f"실제 창을 열지 못했습니다: {exc}\n‘다음’으로 설명을 이어갈 수 있습니다.")
        finally:
            self.opening.discard(key)

    def _leave_modal_views(self, next_view):
        keep = {next_view}
        parent = next_view
        while parent != "main":
            parent = VIEWS[parent].parent
            keep.add(parent)
        for key, win in tuple(self.live_windows.items()):
            if key in keep or not _available(win):
                continue
            if win in self.initial_windows and self.window_states.get(win) != "withdrawn":
                continue
            if VIEWS[key].modal:
                # Destroying without invoking Confirm makes wait_window return
                # its original cancel payload; never run a data-changing command.
                win.destroy()
                self.live_windows.pop(key, None)
            else:
                win.withdraw()
            flag = {"capture": "schedule_input_ocr_addon_open", "log_records": "log_panel_open"}.get(key, VIEWS[key].attribute + "_open")
            if isinstance(getattr(self.app, flag, None), bool):
                setattr(self.app, flag, False)

    def _attach_live_target(self):
        view, key = self.view, self.target_key
        owner = self.main if view == "main" else self.live_windows.get(view)
        if not _available(owner):
            owner = find_window(self.app, view, self._application_windows())
            if _available(owner):
                self.live_windows[view] = owner
                self._activate_live_window(view, owner)
        if not _available(owner):
            self.context_label.configure(text="이 항목은 실제 창이나 선택한 자료가 있어야 볼 수 있습니다. ‘다음’으로 설명을 계속할 수 있습니다." if view in self.failed_views else
                                         "실제 창을 준비 중입니다. 필요한 자료가 없으면 ‘다음’으로 설명을 계속할 수 있습니다.")
            owner = self.main
            target = None
        else:
            target = self._resolve_main_target(key) if view == "main" else find_target(self.app, owner, view, key)
            # Actual notice tab buttons perform navigation, not changes.
            if view == "notice" and self.notice_active_key != key and key not in {"@window", "수집한 공지 / 원문", "서버별 수집 / 송출 설정", "종료·폐기 이력 · 30일"}:
                tab = find_target(self.app, owner, view, "등록된 알림 / 유효기간")
                if tab is not None and callable(getattr(tab, "invoke", None)):
                    tab.invoke()
                target = find_target(self.app, owner, view, key)
                self.notice_active_key = key
            self.context_label.configure(text=(
                "현재 항목의 위치를 찾지 못해 해당 창 전체를 강조합니다.\n설명을 읽고 ‘다음’으로 진행하세요."
                if target is None else
                "빨간 테두리 안쪽 또는 테두리를 클릭하면 ‘다음’과 똑같이 진행합니다.\n실제 버튼 동작은 실행하지 않습니다 · Esc로 종료"
            ))
        if owner is not self.owner or self.spotlight is None:
            # A previously inactive window must lose its full-window mask
            # immediately when it becomes the current tutorial window.
            old_shade = self.extra_shades.pop(owner, None)
            if old_shade is not None:
                old_shade.close()
            for layer in self.extra_shades.values():
                layer.hide()
            if self.spotlight is not None:
                self.spotlight.close()
            self.owner = owner
            self.spotlight = _Spotlight(owner, self._click_target, stop=self.finish)
            self.coach.transient(owner)
            self.layout_signature = None
            if self.main_shade is not None:
                self.main_shade.close()
                self.main_shade = None
            if owner is not self.main:
                self.main_shade = _Spotlight(self.main, lambda: None, full=True, stop=self.finish)
        self.current_target = target

    def _clear_layers(self):
        for name in ("spotlight", "main_shade"):
            layer = getattr(self, name)
            if layer is not None:
                layer.close()
                setattr(self, name, None)
        for layer in self.extra_shades.values():
            layer.close()
        self.extra_shades.clear()

    def _click_target(self):
        # Same path as the coach's Next button, including debounce/completion.
        # Do not invoke the underlying control, select a row or run OCR/capture.
        return self.navigate(1)

    def _hide_layers(self):
        for layer in (self.spotlight, self.main_shade, *self.extra_shades.values()):
            if layer is not None:
                layer.hide()
        if _available(self.coach):
            self.coach.withdraw()

    def _install_blocking(self):
        self.block_bindings["<KeyPress>"] = self.root.bind_class(self.bindtag, "<KeyPress>", self._block_key)
        for sequence in ("<KeyRelease>", "<ButtonPress>", "<ButtonRelease>", "<Motion>", "<MouseWheel>"):
            self.block_bindings[sequence] = self.root.bind_class(self.bindtag, sequence, lambda _: "break")
        self._scan_widgets()

    def _block_key(self, event):
        if event.keysym == "Escape":
            self.finish()
        return "break"

    def _owned(self, widget):
        top = widget.winfo_toplevel()
        if top in (self.coach, self.list_window):
            return True
        layers = (self.spotlight, self.main_shade, *self.extra_shades.values())
        return any(top in layer.windows for layer in layers if layer is not None)

    def _scan_widgets(self):
        # Physical-event bindtag runs BEFORE widget/class commands (Delete etc).
        # Keep virtual selection/configuration events and background alarms intact.
        visible_windows = set()
        if not hasattr(self, "owner"):
            self.owner = self.main
        for widget in list(_walk(self.root)):
            if not self._owned(widget) and widget not in self.blocked:
                widget.bindtags((self.bindtag,) + tuple(widget.bindtags()))
                self.blocked.add(widget)
            if (isinstance(widget, (tk.Tk, tk.Toplevel)) and not self._owned(widget)
                    and widget not in (self.main, self.owner) and widget.winfo_viewable()):
                visible_windows.add(widget)
        for win in list(self.extra_shades):
            if win not in visible_windows:
                self.extra_shades.pop(win).close()
        for win in visible_windows:
            if win not in self.extra_shades:
                self.extra_shades[win] = _Spotlight(win, lambda: None, full=True, stop=self.finish)
        # _position draws all masks together, after calculating foreground
        # exclusions. Scanning must never map an uncut full-window shade.
        self.last_scan = time.monotonic()

    def _remove_blocking(self):
        for widget in self.blocked:
            if _available(widget):
                widget.bindtags(tuple(t for t in widget.bindtags() if t != self.bindtag))
        self.blocked.clear()
        # bind_class registered only this unique tag; do not touch app bindings.
        # Delete registered Tcl callbacks as well, so repeated tutorials don't leak.
        for sequence, callback in self.block_bindings.items():
            try:
                self.root.unbind_class(self.bindtag, sequence)
                self.root.deletecommand(callback)
            except tk.TclError:
                pass
        self.block_bindings.clear()

    @staticmethod
    def _bounds(win):
        return win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()

    def _position(self):
        if not self.main.winfo_viewable() or not self.owner.winfo_viewable() or not _application_foreground(self.main):
            self._hide_layers()
            self.layout_signature = None
            return
        main_bounds = self._bounds(self.main)
        if self.layout_signature is None:
            self.coach.update_idletasks()
        bounds = self._bounds(self.owner)
        target_found = _available(self.current_target) and self.current_target.winfo_viewable()
        if target_found:
            target = self._bounds(self.current_target)
            step = self.chapter.steps[self.index]
            if step.view == "table" and step.target in {"컷", "취소", "컷시간"}:
                tree = self.current_target
                selected = tree.selection() or tree.get_children()
                column = "cut_time" if step.target == "컷시간" else "cut_now"
                for row in selected:
                    cell = tree.bbox(row, column)
                    if cell:
                        cx, cy, cw, ch = cell
                        target = (tree.winfo_rootx() + cx, tree.winfo_rooty() + cy, cw, ch)
                        break
            x, y, w, h = target
            target = (x - 4, y - 4, w + 8, h + 8)
        else:
            # A missing/temporarily unmapped field must not gray out the
            # entire dialog that the user is meant to inspect.
            target = bounds if self.owner is not self.main or self.view == "main" else None
        debug_signature = (self.view, self.target_key, str(self.current_target), target_found)
        if debug_signature != self.target_debug_signature:
            self.target_debug_signature = debug_signature
            log = getattr(self.app, "_append_debug_log", None)
            if callable(log):
                log(f"tutorial_target view={self.view} key={self.target_key} found={int(target_found)} fallback_window={int(not target_found and target is not None)} bounds={bounds} target={target}")
        signature = (bounds, target, main_bounds)
        if signature != self.layout_signature:
            self.layout_signature = signature
            if self.owner is self.main:
                x, y = place_coach(bounds, target or bounds, (COACH_WIDTH, COACH_HEIGHT))
            else:
                x, y = bounds[0] + bounds[2] + 16, bounds[1] + 4
                # If dragged toward screen edge, keep the explanation visible.
            x, y = _clamp_position(x, y, (COACH_WIDTH, COACH_HEIGHT), _work_area(self.owner))
            if target is not None:
                tx, ty, tw, th = target
                if min(x + COACH_WIDTH, tx + tw) > max(x, tx) and min(y + COACH_HEIGHT, ty + th) > max(y, ty):
                    # A child dialog near a monitor edge must remain clickable.
                    x, y = place_coach(_work_area(self.owner), target, (COACH_WIDTH, COACH_HEIGHT))
            _move_window(self.coach, COACH_WIDTH, COACH_HEIGHT, x, y)
            self.coach.update_idletasks()
        exclusions = (_frame_bounds(self.owner), _frame_bounds(self.coach))
        background_changed = False
        if self.main_shade is not None:
            background_changed = self.main_shade.update(main_bounds, None, exclusions=exclusions)
        for win, layer in self.extra_shades.items():
            if _available(win) and win.winfo_viewable():
                background_changed = layer.update(self._bounds(win), None, exclusions=exclusions) or background_changed
            else:
                layer.hide()
        if background_changed:
            self.owner.lift()
            self.spotlight.signature = None
        changed = self.spotlight.update(bounds, target)
        if changed or background_changed or self.coach.state() == "withdrawn":
            self.coach.deiconify()
            self.coach.lift()
        if not target_found and target is not None:
            self.context_label.configure(text="현재 항목의 위치를 찾지 못해 해당 창 전체를 강조합니다.\n설명을 읽고 ‘다음’으로 진행하세요.")
        elif target is None:
            self.context_label.configure(text="현재 화면에서 대상을 찾지 못했습니다.\n설명을 읽고 ‘다음’으로 진행할 수 있습니다.")

    def _external_grab(self):
        # Use the Tcl path: grab_current() cannot resolve a private ttk popdown.
        path = str(self.root.tk.call("grab", "current", self.root._w))
        if not path:
            return False
        for win in (self.coach, self.list_window):
            if _available(win) and (path == str(win) or path.startswith(str(win) + ".")):
                return False
        return True

    def _tick(self):
        self.after_id = None
        if not self.running or self.closed:
            return
        try:
            if not _available(self.main) or not _available(self.coach):
                self.finish()
                return
            # Real modal openers can still be inside wait_window(). Find and
            # authorize their window here, release only that known grab.
            for key in tuple(self.opening):
                win = find_window(self.app, key, self._application_windows())
                if _available(win):
                    self.live_windows[key] = win
                    grab = str(self.root.tk.call("grab", "current", self.root._w))
                    if win not in self.authorized_windows or grab == str(win):
                        self._activate_live_window(key, win)
            if self._external_grab():
                # A real capture/error dialog takes priority. Allow its own
                # controls (e.g. Cancel/OK), pause masks, then resume the guide.
                self._hide_layers()
                path = str(self.root.tk.call("grab", "current", self.root._w))
                for widget in tuple(self.blocked):
                    if _available(widget) and (str(widget) == path or str(widget).startswith(path + ".")):
                        widget.bindtags(tuple(t for t in widget.bindtags() if t != self.bindtag))
                        self.blocked.discard(widget)
                self.suspended = True
                self.after_id = self.root.after(150, self._tick)
                return
            if self.suspended:
                self.suspended = False
                self.layout_signature = None
                self.last_scan = 0
            self._attach_live_target()
            if time.monotonic() - self.last_scan >= .75:
                self._scan_widgets()
            self._position()
            self.after_id = self.root.after(150, self._tick)
        except Exception as exc:
            self.finish()
            if _available(self.main):
                self.app._show_centered_messagebox(
                    "showerror", "튜토리얼", f"설명 위치를 확인하지 못해 튜토리얼을 종료했습니다.\n{exc}",
                    parent=self.main,
                )

    def _stop_run(self):
        self.running = False
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        self._clear_layers()
        self._remove_blocking()
        for name in ("coach",):
            win = getattr(self, name)
            if _available(win):
                win.destroy()
            setattr(self, name, None)
        self.capture_allowed = False
        if self.initial_notice_tab is not None:
            manager = self._notice_manager()
            if manager is not None and _available(manager.window):
                manager.tabs.select(self.initial_notice_tab)
        for win, (original, registered) in tuple(self.protocols.items()):
            if _available(win):
                win.protocol("WM_DELETE_WINDOW", original)
                try:
                    win.deletecommand(registered)
                except tk.TclError:
                    pass
        self.protocols.clear()
        for win, state in reversed(tuple(self.window_states.items())):
            if not _available(win) or win is self.main:
                continue
            is_modal = any(VIEWS[k].modal and w is win for k, w in self.live_windows.items())
            if win not in self.initial_windows and is_modal:
                win.destroy()
            else:
                win.state(state)
        for name, value in self.initial_flags.items():
            setattr(self.app, name, value)
        if not self.initial_flags.get("schedule_input_ocr_addon_open", False):
            self.app._unregister_schedule_input_ocr_addon_hotkeys()
        for name, value in self.input_filter_values.items():
            variable = getattr(self.app, name, None)
            if variable is not None:
                variable.set(value)
        self.live_windows.clear()
        self.opening.clear()
        self.authorized_windows.clear()
        self.current_target = None

    def back_to_list(self, completed=False):
        self._stop_run()
        if _available(self.list_window):
            self.list_window.deiconify()
            self.list_window.lift()
            self.chapter_list.focus_set()
            self._list_selection()
            if completed:
                self.list_description.configure(text="완료했습니다. 매일 할 일은 스샷 → OCR → 확인 → 적용입니다.\n필요한 다른 항목을 선택하거나 닫아도 됩니다.")

    def _main_destroyed(self, event):
        if event.widget is self.main:
            self.finish()

    def finish(self):
        if self.closed:
            return "break"
        self.closed = True
        self._stop_run()
        if _available(self.list_window):
            self.list_window.destroy()
        self.list_window = None
        if _available(self.main) and self.destroy_bind:
            self.main.unbind("<Destroy>", self.destroy_bind)
        if getattr(self.app, "schedule_tutorial_controller", None) is self:
            self.app.schedule_tutorial_controller = None
        if _available(self.previous_focus):
            try:
                self.previous_focus.focus_set()
            except tk.TclError:
                pass
        return "break"
