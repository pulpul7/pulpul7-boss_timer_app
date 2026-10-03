"""Local progress display only; no connection, polling, or handover work."""
import tkinter as tk
from tkinter import ttk


class DiscordConnectionProgress:
    STEPS = {
        "incoming": (
            ("bot", "봇 연결 확인"),
            ("owner", "현재 관리자 확인"),
            ("handover", "승계 처리 (기존 관리자 접속 시)"),
            ("authority", "관리자 권한 획득"),
            ("voice", "음성채널 입장"),
        ),
        "outgoing": (
            ("stop", "송출 권한 반납 · 송출 중단"),
            ("upload", "스케줄 비교 · 필요한 자료 업로드"),
            ("voice", "음성 연결 종료 확인"),
            ("standby", "봇 연결 유지 · 대기 전환"),
        ),
    }

    def __init__(self, app, *, role="incoming", token=""):
        self.role, self.token = role, token
        self.finished = False
        self.rows = {}
        self.close_job = None
        parent = getattr(app, "schedule_window", None)
        if not app._widget_available(parent) or not parent.winfo_viewable():
            parent = app.root
        self.window = tk.Toplevel(app.root)
        self.window.withdraw()
        self.window.title("관리자 인계 진행" if role == "outgoing" else "디코 연결 · 관리자 승계")
        self.window.configure(bg="#f1f5f9")
        self.window.transient(parent)
        self.window.resizable(False, False)
        font = getattr(app, "current_font_family", "Malgun Gothic")
        header = tk.Frame(self.window, bg="#1e3a8a", padx=18, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="관리자 인계 중" if role == "outgoing" else "디코 연결 중",
                 bg="#1e3a8a", fg="white", font=(font, 14, "bold")).pack(anchor="w")
        self.peer = tk.StringVar(value="새 관리자에게 권한을 넘기고 있습니다." if role == "outgoing"
                                 else "봇 연결과 관리자 상태를 확인하고 있습니다.")
        tk.Label(header, textvariable=self.peer, bg="#1e3a8a", fg="#dbeafe",
                 font=(font, 9), wraplength=480, justify="left").pack(anchor="w", pady=(5, 0))
        body = tk.Frame(self.window, bg="#f1f5f9", padx=18, pady=10)
        body.pack(fill="both", expand=True)
        for key, label in self.STEPS[role]:
            row = tk.Label(body, text="○  " + label, bg="#f1f5f9", fg="#64748b",
                           font=(font, 10), anchor="w", pady=3)
            row.pack(fill="x")
            self.rows[key] = row
        self.detail = tk.StringVar(value="기존 연결 과정을 진행하고 있습니다.")
        tk.Label(body, textvariable=self.detail, bg="#ffffff", fg="#17365d", anchor="w",
                 justify="left", wraplength=466, font=(font, 9), padx=10, pady=8).pack(fill="x", pady=(8, 6))
        self.bar = ttk.Progressbar(body, mode="indeterminate")
        self.bar.pack(fill="x", pady=(0, 8))
        self.bar.start(60)
        footer = tk.Frame(body, bg="#f1f5f9")
        footer.pack(fill="x")
        tk.Label(footer, text="이 창을 닫아도 연결·인계는 계속 진행됩니다.", bg="#f1f5f9",
                 fg="#64748b", font=(font, 9)).pack(side="left")
        tk.Button(footer, text="닫기", command=self.close, font=(font, 9), padx=8).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        app._center_window_over_parent(self.window, parent, 520, 410)
        self.window.deiconify()
        self.window.lift()
        # Paint before the existing synchronous settings/status checks. Do not
        # run a nested event loop or grab focus from confirmation dialogs.
        self.window.update_idletasks()

    def is_open(self):
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def update(self, step, detail="", peer=""):
        if self.finished or not self.is_open():
            return
        keys = [key for key, _label in self.STEPS[self.role]]
        if step not in keys:
            return
        position = keys.index(step)
        for index, (key, label) in enumerate(self.STEPS[self.role]):
            mark, color = (("✓", "#15803d") if index < position else
                           ("●", "#1d4ed8") if index == position else ("○", "#64748b"))
            self.rows[key].configure(text=f"{mark}  {label}", fg=color)
        if detail:
            self.detail.set(detail)
        if peer:
            self.peer.set(peer)

    def finish(self, detail, *, success=True):
        if not self.is_open() or self.finished:
            return
        self.finished = True
        self.bar.stop()
        self.bar.configure(mode="determinate", value=100 if success else 0)
        if success:
            for key, label in self.STEPS[self.role]:
                self.rows[key].configure(text="✓  " + label, fg="#15803d")
        self.detail.set(detail)
        # Only the display stays briefly; connection workers never wait for it.
        self.close_job = self.window.after(1800 if success else 3500, self.close)

    def close(self):
        if not self.is_open():
            return
        if self.close_job is not None:
            try:
                self.window.after_cancel(self.close_job)
            except tk.TclError:
                pass
            self.close_job = None
        self.bar.stop()
        self.window.destroy()


def show_connection_progress(app, *, role="incoming", token=""):
    existing = getattr(app, "discord_connection_progress", None)
    if existing is not None:
        # Adopt the button's preliminary window when the worker starts.
        if (existing.role == role and not existing.finished
                and (existing.token == token or not existing.token)):
            existing.token = token
            return existing
        existing.close()
    app.discord_connection_progress = DiscordConnectionProgress(app, role=role, token=token)
    return app.discord_connection_progress
