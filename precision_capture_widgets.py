"""Code-native capture UI; no external images or bundled animation needed."""
import tkinter as tk
import sys


def prevent_mouse_activation(window):
    """Clickable tool window, without deactivating/dimming the game.

    WS_EX_NOACTIVATE retains mouse input (unlike WS_EX_TRANSPARENT).
    https://learn.microsoft.com/en-us/windows/win32/winmsg/extended-window-styles
    """
    if sys.platform != 'win32':
        return
    import ctypes as c
    from ctypes import wintypes as w
    user32 = c.WinDLL('user32', use_last_error=True)
    user32.GetAncestor.argtypes, user32.GetAncestor.restype = [w.HWND, w.UINT], w.HWND
    user32.GetWindowLongPtrW.argtypes, user32.GetWindowLongPtrW.restype = [w.HWND, c.c_int], c.c_ssize_t
    user32.SetWindowLongPtrW.argtypes, user32.SetWindowLongPtrW.restype = [w.HWND, c.c_int, c.c_ssize_t], c.c_ssize_t
    window.update_idletasks()
    handle = user32.GetAncestor(window.winfo_id(), 2)
    if not handle:
        raise RuntimeError('측정창의 Windows 핸들을 찾지 못했습니다.')
    style = user32.GetWindowLongPtrW(handle, -20)
    c.set_last_error(0)
    previous = user32.SetWindowLongPtrW(handle, -20, style | 0x08000000)
    if not previous and c.get_last_error():
        raise c.WinError(c.get_last_error())


class CaptureProgress:
    def __init__(self, owner, rect, cancel, *, retry_names=(), rate=5, topmost=True,
                 auto_apply=False, on_auto_apply=None):
        self.window = tk.Toplevel(owner)
        window = self.window
        window.withdraw()
        window.title('초정밀 측정 · 재시도' if retry_names else '초정밀 측정')
        # Ends above y=190: never covers the timetable, clock or title guards.
        window.geometry(f"460x148{int(rect['left'])+570:+d}{int(rect['top'])+6:+d}")
        window.resizable(False,False)
        window.configure(bg='#f8fafc')
        window.attributes('-topmost',topmost)
        window.protocol('WM_DELETE_WINDOW',cancel)
        font=('맑은 고딕',9)
        tk.Frame(window,bg='#2563eb').place(x=0,y=0,relwidth=1,height=4)
        tk.Label(window,text='PRECISION SCAN',font=('Segoe UI',8,'bold'),fg='#2563eb',bg='#f8fafc').place(x=16,y=11)
        tk.Label(window,text=f'{rate:g}회/초',font=font,fg='#64748b',bg='#f8fafc').place(x=384,y=11)
        self.auto_apply_var = tk.BooleanVar(master=window, value=bool(auto_apply))
        self.auto_apply_check = tk.Checkbutton(window, text='자동적용', variable=self.auto_apply_var,
            font=font, bg='#f8fafc', fg='#1e3a8a', activebackground='#f8fafc',
            selectcolor='#ffffff', takefocus=False, highlightthickness=0, bd=0,
            command=lambda: on_auto_apply(bool(self.auto_apply_var.get())) if on_auto_apply else None)
        self.auto_apply_check.place(x=360,y=34,width=90,height=24)
        title='미확정 보스만 다시 측정합니다' if retry_names else '초단위 젠 시간을 측정합니다'
        tk.Label(window,text=title,font=('맑은 고딕',12,'bold'),fg='#0f172a',bg='#f8fafc').place(x=16,y=33)
        self.status=tk.Label(window,text='최초 화면 분석 · 변화 추적 준비 중',font=font,fg='#334155',bg='#f8fafc',anchor='w')
        self.status.place(x=16,y=65,width=430,height=20)
        self.bar=tk.Canvas(window,width=334,height=6,bg='#e2e8f0',highlightthickness=0)
        self.bar.place(x=16,y=95)
        self.fill=self.bar.create_rectangle(0,0,0,6,fill='#2563eb',outline='')
        self.timer=tk.Label(window,text='0 / 65초',font=font,fg='#64748b',bg='#f8fafc',anchor='e')
        self.timer.place(x=354,y=87,width=88,height=22)
        tk.Label(window,text='시간표를 움직이거나 다른 창으로 가리지 마세요.',font=('맑은 고딕',8),fg='#64748b',bg='#f8fafc').place(x=16,y=116)
        tk.Button(window,text='측정 취소',font=font,bg='#e2e8f0',fg='#334155',activebackground='#cbd5e1',
                  relief='flat',bd=0,cursor='hand2',takefocus=False,command=cancel).place(x=360,y=111,width=84,height=28)
        try:
            prevent_mouse_activation(window)
        except Exception:
            window.destroy()
            raise
        # On Windows, wm deiconify explicitly calls TkSetFocusWin even when
        # WS_EX_NOACTIVATE is set. wm state normal uses SW_SHOWNOACTIVATE
        # without that extra focus/raise step. Keep the game active from T0.
        window.state('normal')
        window.update_idletasks()

    def update(self, elapsed, total, tracking, completed, duration):
        self.timer.config(text=f'{min(elapsed,duration):.0f} / {duration:.0f}초')
        self.bar.coords(self.fill,0,0,334*min(1,max(0,elapsed/duration)),6)
        self.status.config(text=('최초 화면 분석 · 후보 영역 수집 중' if total is None else
                                f'총 {total}개   ·   남은추적 {tracking}개   /   완료 {completed}개 (배제 포함)'))
