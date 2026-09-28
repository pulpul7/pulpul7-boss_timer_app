"""Tk-only ROI guide windows, excluded from Windows screen capture."""
import ctypes as c
from ctypes import wintypes as w
import tkinter as tk


class RegionOverlay:
    def __init__(self, owner, origin, handles):
        self.owner, self.origin, self.handles = owner, origin, handles
        self.windows = []
        self.user32 = c.WinDLL('user32', use_last_error=True)
        for name, args, result in (
            ('GetAncestor', [w.HWND, w.UINT], w.HWND),
            ('SetWindowDisplayAffinity', [w.HWND, w.DWORD], w.BOOL),
            ('GetWindowLongPtrW', [w.HWND, c.c_int], c.c_ssize_t),
            ('SetWindowLongPtrW', [w.HWND, c.c_int, c.c_ssize_t], c.c_ssize_t),
        ):
            method = getattr(self.user32, name)
            method.argtypes, method.restype = args, result

    def clear(self):
        for window, handle in self.windows:
            try:
                window.destroy()
            except tk.TclError:
                pass
            self.handles.discard(handle)
        self.windows.clear()

    def show(self, regions):
        self.clear()
        try:
            for rect in regions:
                window = tk.Toplevel(self.owner)
                window.withdraw()
                window.overrideredirect(True)
                outline = getattr(rect, 'outline', False)
                color = getattr(rect, 'color', '#00dfff')
                window.configure(bg='#010203' if outline else color)
                window.attributes('-topmost', True)
                if outline:
                    window.attributes('-transparentcolor', '#010203')
                    canvas = tk.Canvas(window, bg='#010203', highlightthickness=0, bd=0)
                    canvas.pack(fill='both', expand=True)
                    canvas.create_rectangle(1, 1, rect.width-2, rect.height-2, outline=color, width=2)
                    label = getattr(rect, 'label', '')
                    if label:
                        item = canvas.create_text(4, 3, anchor='nw', text=label,
                                                  fill=color, font=('맑은 고딕', 8, 'bold'))
                        background = canvas.create_rectangle(canvas.bbox(item), fill='#111827', outline='')
                        canvas.tag_lower(background, item)
                else:
                    window.attributes('-alpha', 0.22)
                x, y = self.origin
                window.geometry(f'{rect.width}x{rect.height}{x+rect.left:+d}{y+rect.top:+d}')
                window.update_idletasks()
                handle = self.user32.GetAncestor(window.winfo_id(), 2)
                self.windows.append((window, handle))
                # Layered, click-through, no activation. Never let the guide
                # itself become OCR input or generate a false pixel change.
                style = self.user32.GetWindowLongPtrW(handle, -20)
                self.user32.SetWindowLongPtrW(handle, -20, style | 0x80000 | 0x20 | 0x08000000)
                if not self.user32.SetWindowDisplayAffinity(handle, 0x11):
                    raise RuntimeError('이 Windows 환경에서는 감지영역의 캡처 제외를 설정하지 못했습니다.')
                self.handles.add(handle)
                window.deiconify()
        except Exception:
            self.clear()
            raise
