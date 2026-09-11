"""Code-native capture UI; no external images or bundled animation needed."""
import tkinter as tk


class CaptureProgress:
    def __init__(self, owner, rect, cancel, *, retry_names=(), rate=5):
        self.window = tk.Toplevel(owner)
        window = self.window
        window.title('초정밀 측정 · 재시도' if retry_names else '초정밀 측정')
        # Ends above y=190: never covers the timetable, clock or title guards.
        window.geometry(f"460x148{int(rect['left'])+570:+d}{int(rect['top'])+6:+d}")
        window.resizable(False,False)
        window.configure(bg='#f8fafc')
        window.attributes('-topmost',True)
        window.protocol('WM_DELETE_WINDOW',cancel)
        font=('맑은 고딕',9)
        tk.Frame(window,bg='#2563eb').place(x=0,y=0,relwidth=1,height=4)
        tk.Label(window,text='PRECISION SCAN',font=('Segoe UI',8,'bold'),fg='#2563eb',bg='#f8fafc').place(x=16,y=11)
        tk.Label(window,text=f'{rate:g}회/초',font=font,fg='#64748b',bg='#f8fafc').place(x=384,y=11)
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
                  relief='flat',bd=0,cursor='hand2',command=cancel).place(x=360,y=111,width=84,height=28)

    def update(self, elapsed, total, tracking, completed, duration):
        self.timer.config(text=f'{min(elapsed,duration):.0f} / {duration:.0f}초')
        self.bar.coords(self.fill,0,0,334*min(1,max(0,elapsed/duration)),6)
        self.status.config(text=('최초 화면 분석 · 후보 영역 수집 중' if total is None else
                                f'총 {total}개   ·   남은추적 {tracking}개   /   완료 {completed}개 (배제 포함)'))
