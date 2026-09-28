"""Read-only OCR1 geometry guide; never captures frames or runs OCR."""
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk

from precision_region_overlay import RegionOverlay
from schedule_ocr1_regions import clock_rect


@dataclass(frozen=True)
class Guide:
    left: int
    top: int
    width: int
    height: int
    label: str
    color: str
    outline: bool = True


def regions(app, area, clock_band, extra=False):
    """Use OCR1's existing geometry helpers, not precision-tracker rectangles."""
    result = []
    def add(rect, label, color):
        left, top = max(0, int(rect['left'])), max(0, int(rect['top']))
        right, bottom = min(1600, int(rect['right'])), min(900, int(rect['bottom']))
        if right > left and bottom > top:
            result.append(Guide(left, top, right-left, bottom-top, label, color))

    board = app._get_schedule_input_ocr2_fixed_window_rect()  # UI OCR_1 uses historical internal ocr2 name.
    add(clock_rect(board, clock_band), '현재시간 판독', '#4ade80')
    slots = app._get_schedule_ocr_slot_rects(area, 1600, 900, window_rect=board)
    for slot in slots:
        number = slot['slot_index']
        for key, label, color in [('name', '이름 기준', '#38bdf8'), ('timer', '시간 기준', '#fbbf24')]:
            add({side: slot[f'{key}_{side}'] for side in ('left', 'top', 'right', 'bottom')},
                f'{number} {label}', color)
        if extra:
            # Retries may enlarge/scale the crop, but pixels outside the same
            # allowed glyph regions remain black; never display a broad ROI.
            add({side: slot[f'timer_{side}'] for side in ('left', 'top', 'right', 'bottom')},
                f'{number} 재시도도 이 영역만', '#fb7185')
    return result


def clear(app):
    preview = getattr(app, '_ocr1_region_preview', None)
    app._ocr1_region_preview = None
    if preview:
        preview.close()


def suspend(app):
    preview = getattr(app, '_ocr1_region_preview', None)
    if preview:
        preview.hide()


def open_preview(app, areas, clock_band):
    if not areas:
        return
    preview = getattr(app, '_ocr1_region_preview', None)
    if preview and preview.window.winfo_exists():
        return
    app._ocr1_region_preview = Preview(app, areas, clock_band)


def capture_popup_right(app):
    """Dock to the actual capture popup, including when it moves/reopens."""
    popup = getattr(app, 'schedule_input_ocr_addon_window', None)
    try:
        if popup is not None and popup.winfo_exists() and popup.winfo_ismapped():
            return (popup.winfo_rootx() + popup.winfo_width() + 6, popup.winfo_rooty())
    except tk.TclError:
        pass
    return None


class Preview:
    def __init__(self, app, areas, clock_band):
        self.app, self.clock_band = app, clock_band
        self.owner = app.schedule_input_window
        self.window = tk.Toplevel(self.owner)
        self.window.title('OCR1 글자 읽기 영역')
        self.window.withdraw()
        self.window.configure(bg='#eff6ff')
        self.window.resizable(False, False)
        self.window.attributes('-topmost', True)
        self.overlay = None
        self.after = None
        self.signature = None
        self.closed = False
        self.failed = False
        previous = getattr(app, '_ocr1_preview_area', '')
        if previous not in areas:
            previous = (getattr(app, 'schedule_input_precision_report', None) or {}).get('area')
        self.area = tk.StringVar(value=previous if previous in areas else areas[0])
        self.enabled = tk.BooleanVar(value=True)
        self.extra = tk.BooleanVar(value=False)
        self.status = tk.StringVar()
        tk.Label(self.window, text='OCR1 글자 영역만 읽기 · 전체/보드 제외', bg='#1e3a8a', fg='white',
                 font=('맑은 고딕', 10, 'bold'), anchor='w', padx=10, pady=5).pack(fill='x')
        controls = tk.Frame(self.window, bg='#eff6ff')
        controls.pack(fill='x', padx=8, pady=4)
        tk.Checkbutton(controls, text='영역 표시', variable=self.enabled, bg='#eff6ff').pack(side='left')
        menu = ttk.Combobox(controls, textvariable=self.area, values=areas, state='readonly', width=10)
        menu.pack(side='left', padx=6)
        tk.Checkbutton(controls, text='재시도 영역', variable=self.extra, bg='#eff6ff').pack(side='left')
        tk.Label(self.window, text='초록: 현재시간  /  파랑: 이름  /  노랑: 남은시간',
                 bg='#eff6ff', fg='#0f172a', anchor='w').pack(fill='x', padx=10)
        tk.Label(self.window, text='챕터는 미리보기용 · 최초 판독은 3/4칸 후보 글자 영역을 함께 사용',
                 bg='#eff6ff', fg='#334155', anchor='w').pack(fill='x', padx=10)
        tk.Label(self.window, textvariable=self.status, bg='#eff6ff', fg='#334155', anchor='w').pack(fill='x', padx=10, pady=4)
        self.window.protocol('WM_DELETE_WINDOW', lambda: clear(app))
        self.window.bind('<Destroy>', self.destroyed)
        self.tick()

    def destroyed(self, event):
        if event.widget is self.window:
            self.close(destroy=False)

    def hide(self):
        if self.overlay:
            self.overlay.clear()
            self.overlay = None
        self.signature = None
        if not self.closed:
            try:
                self.window.withdraw()
            except tk.TclError:
                pass

    def close(self, destroy=True):
        if self.closed:
            return
        self.hide()
        self.closed = True
        if self.after:
            try:
                self.app.root.after_cancel(self.after)
            except tk.TclError:
                pass
            self.after = None
        if destroy:
            self.window.destroy()
        if getattr(self.app, '_ocr1_region_preview', None) is self:
            self.app._ocr1_region_preview = None

    def tick(self):
        self.after = None
        if self.closed:
            return
        try:
            if self.app.schedule_input_window is not self.owner or not self.owner.winfo_exists():
                self.close()
                return
            busy = (getattr(self.app, 'schedule_input_ocr_addon_busy', False)
                    or getattr(self.app, 'schedule_input_ocr_worker_active', False)
                    or getattr(self.app, '_precision_session', None)
                    or self.owner.state() in {'withdrawn', 'iconic'})
            if busy:
                self.hide()
            else:
                hwnd = self.app._get_preferred_odin_window_handle()
                screen = self.app._get_odin_client_screen_rect(hwnd) if hwnd else None
                valid = bool(screen and (screen['width'], screen['height']) == (1600, 900))
                dock = capture_popup_right(self.app)
                signature = ((screen['left'], screen['top']) if valid else None,
                             self.area.get(), self.enabled.get(), self.extra.get(), hwnd, dock)
                if signature != self.signature:
                    self.hide()
                    self.signature = signature
                    self.app._ocr1_preview_area = self.area.get()
                    if dock is not None:
                        self.window.geometry(f"500x154{int(dock[0]):+d}{int(dock[1]):+d}")
                    elif valid:
                        self.window.geometry(f"500x154{int(screen['left'])+550:+d}{int(screen['top'])+10:+d}")
                    else:
                        self.app._center_window_over_parent(self.window, self.owner, 500, 154)
                    if valid and self.enabled.get() and not self.failed:
                        self.overlay = RegionOverlay(self.owner, (screen['left'], screen['top']), set())
                        self.overlay.show(regions(self.app, self.area.get(), self.clock_band, self.extra.get()))
                    self.status.set('1600×900 기준 · 촬영 중 자동 숨김' if valid else '오딘 창을 1600×900으로 맞추면 표시됩니다.')
                    self.window.deiconify()
        except (tk.TclError, OSError, RuntimeError) as exc:
            self.hide()
            self.failed = True
            if not self.closed:
                self.status.set(f'영역 표시 실패: {exc}')
                self.window.deiconify()
        if not self.closed:
            self.after = self.app.root.after(300, self.tick)
