"""Tk update center. Workers communicate through a queue, never through Tk."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from ai_module_updater import AiModuleUpdater, KST, REPOSITORY
from app_patch_notes import patch_history_text


def center_window(window, parent):
    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    width = max(width, window.winfo_width())
    height = max(height, window.winfo_height())
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    window.geometry(f"+{max(0, x)}+{max(0, y)}")


def display_date(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(KST).strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return str(value)


class AiUpdateCenter:
    def __init__(self, app, data_root: Path, app_version: str):
        self.app = app
        self.root = app.root
        self.app_version = str(app_version)
        runtime = getattr(app, "notice_runtime", None)
        self.updater = runtime.updater if runtime is not None else AiModuleUpdater(data_root, app_version)
        self.window = None
        self.patch_window = None
        self.policy_window = None
        self.prompt = None
        self.busy = False
        self.events = queue.Queue()
        self.rows = []
        self.status_text = "업데이트 정보를 확인할 수 있습니다."
        self.root.after(100, self._poll)
        self.root.after(2000, self._daily)
        self.root.after(4000, self._daily_full)

    def _parent(self):
        for widget in (self.window, getattr(self.app, "schedule_window", None), self.root):
            if widget is not None and widget.winfo_exists() and widget.winfo_viewable():
                return widget
        return self.root

    def _status(self, message):
        self.status_text = message
        if self.window is not None and self.window.winfo_exists():
            self.status.set(message)

    def _work(self, message, task, done, *, failure_version="", silent=False, domain="notice"):
        if self.busy:
            self._status("현재 작업이 끝난 뒤 다시 눌러 주세요.")
            return False
        self.busy = True
        self._status(message)

        def run():
            try:
                result = task()
                self.events.put((done, result, None, silent))
            except Exception as exc:
                detail = str(exc)
                try:
                    if domain == "notice":
                        self.updater.record("설치 실패" if failure_version else "확인 실패", failure_version, detail)
                except Exception:
                    pass
                self.events.put((done, None, detail, silent))
        threading.Thread(target=run, name="ai-update-worker", daemon=True).start()
        return True

    def _poll(self):
        try:
            while True:
                done, result, error, silent = self.events.get_nowait()
                self.busy = False
                if error:
                    self._status(f"업데이트 처리 실패: {error} · 기존 파일은 유지됩니다.")
                    self.app._append_debug_log(f"ai_update_failed {error}")
                    self._render()
                    if not silent:
                        self.app._show_centered_messagebox("showerror", "업데이트", self.status_text, parent=self._parent())
                else:
                    done(result)
        except queue.Empty:
            pass
        if (self.window is not None and self.window.winfo_exists()
                and getattr(self, "selected_scope", 0) == 1
                and time.monotonic() >= getattr(self, "web_refresh_at", 0)):
            self._refresh_web_panel()
            self.web_refresh_at = time.monotonic() + 1
        self.root.after(100, self._poll)

    def _daily(self):
        from program_update_guard import active
        if active():
            self.root.after(60_000, self._daily)
            return
        if not self.busy and self.prompt is None:
            def check_if_due():
                if self.updater.claim_daily_check():
                    return self.updater.check()
                return None

            def checked(rows):
                if rows is not None:
                    self._checked(rows, automatic=True)
                else:
                    self._status("자동 확인 대기 · 수동 확인은 언제든 가능합니다.")
                    self._render()
            self._work("자동 확인 일정 확인 중…", check_if_due, checked, silent=True)
        self.root.after(60_000, self._daily)

    def open(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            self.window.focus_force()
            return
        parent = self._parent()
        win = self.window = tk.Toplevel(self.root)
        win.title("업데이트 관리")
        win.configure(bg="#eff6ff")
        win.geometry("920x720")
        win.minsize(800, 620)
        win.transient(parent)
        header = tk.Frame(win, bg="#1e3a8a", padx=18, pady=14)
        header.pack(fill="x")
        self._build_history_actions(header)
        tk.Label(header, text="업데이트 관리", font=("맑은 고딕", 17, "bold"),
                 bg="#1e3a8a", fg="#ffffff", anchor="w").pack(fill="x")
        tk.Label(header, text=f"현재 프로그램 버전: {self.app_version}", font=("맑은 고딕", 11, "bold"),
                 bg="#1e3a8a", fg="#dbeafe", anchor="w").pack(fill="x", pady=(5, 0))
        tk.Label(win, text=f"공식 배포: {REPOSITORY}  ·  날짜는 한국시간",
                 bg="#eff6ff", fg="#475569", anchor="w").pack(fill="x", padx=20)
        full, web, notice = self._build_scope_tabs(win)
        self._build_full_panel(full)
        self._build_web_panel(web)
        self.summary = tk.StringVar(win)
        tk.Label(notice, textvariable=self.summary, bg="#eff6ff", fg="#1d4ed8", anchor="w").pack(fill="x", padx=20, pady=8)
        options = ttk.Frame(notice, padding=(12, 8))
        options.pack(fill="x", padx=16)
        try:
            state = self.updater.snapshot()
        except Exception as exc:
            self._close()
            self.app._show_centered_messagebox("showerror", "업데이트 정보 오류", str(exc), parent=parent)
            return
        self.auto = tk.BooleanVar(win, value=state["auto_enabled"])
        self.after_time = tk.StringVar(win, value=state["check_after"])
        ttk.Checkbutton(options, text="하루 한 번 자동 확인 / 5초 후 설치", variable=self.auto).pack(side="left")
        ttk.Label(options, text="  확인 시작 시각(KST)").pack(side="left")
        ttk.Entry(options, textvariable=self.after_time, width=6).pack(side="left", padx=6)
        ttk.Button(options, text="설정 저장", command=self._save_options).pack(side="left")
        releases, history = self._build_update_tabs(notice)
        tree_frame = ttk.Frame(releases)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("date", "version", "status", "title"), show="headings", height=7)
        for key, label, width in (("date", "배포일", 132), ("version", "버전", 85),
                                  ("status", "설치 상태", 168), ("title", "업데이트 제목", 340)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, minwidth=65)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._details)
        self.detail = self._text_box(releases, height=8)
        self.history_text = self._text_box(history, height=16)
        actions = ttk.Frame(notice, padding=(16, 0, 16, 8))
        actions.pack(fill="x")
        ttk.Button(actions, text="지금 확인", command=self.check).pack(side="left")
        ttk.Button(actions, text="선택 버전 설치 / 재시도", command=self._install_selected).pack(side="left", padx=8)
        tk.Label(notice, text="알림 모듈 전용 · Update_AI_v버전.zip · 다음 프로그램 실행 때 적용됩니다.\n"
                 "기능별 연결 상태는 알리미 관리 창에서 확인해 주세요.",
                 anchor="w", justify="left", bg="#eff6ff", fg="#64748b").pack(fill="x", padx=20, pady=(0, 12))
        footer = tk.Frame(win, bg="#eff6ff", padx=20, pady=10)
        footer.pack(fill="x")
        ttk.Button(footer, text="닫기", command=self._close).pack(side="right", padx=(12, 0))
        self.status = tk.StringVar(win, value=self.status_text)
        tk.Label(footer, textvariable=self.status, anchor="w", justify="left", wraplength=740,
                 bg="#eff6ff", fg="#334155").pack(side="left", fill="x", expand=True)
        win.protocol("WM_DELETE_WINDOW", self._close)
        center_window(win, parent)
        self._render()

    def _build_scope_tabs(self, parent):
        section = tk.Frame(parent, bg="#eff6ff")
        section.pack(fill="both", expand=True, padx=16, pady=(12, 0))
        bar = tk.Frame(section, bg="#eff6ff")
        bar.pack(fill="x")
        content = tk.Frame(section, bg="#1e3a8a", padx=2, pady=2)
        content.pack(fill="both", expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self.scope_titles = ("전체 업데이트", "웹서버 업데이트", "알림 모듈")
        self.scope_buttons, self.scope_pages = [], []
        for index, title in enumerate(self.scope_titles):
            bar.columnconfigure(index, weight=1, uniform="update-scope")
            button = tk.Button(bar, text=title, command=lambda i=index: self._select_scope(i),
                               font=("맑은 고딕", 11, "bold"), padx=12, pady=10,
                               bd=0, relief="flat", takefocus=True, cursor="hand2",
                               highlightthickness=2, highlightcolor="#f59e0b")
            button.grid(row=0, column=index, sticky="ew", padx=(0, 4 if index < 2 else 0))
            button.bind("<Return>", lambda _event, i=index: self._select_scope(i))
            button.bind("<Left>", lambda _event, i=index: self._focus_scope(i - 1))
            button.bind("<Right>", lambda _event, i=index: self._focus_scope(i + 1))
            page = tk.Frame(content, bg="#eff6ff")
            page.grid(row=0, column=0, sticky="nsew")
            self.scope_buttons.append(button)
            self.scope_pages.append(page)
        self._select_scope(0)
        return self.scope_pages

    def _select_scope(self, index):
        self.selected_scope = index
        for i, button in enumerate(self.scope_buttons):
            selected = i == index
            background = "#1e3a8a" if selected else "#dbeafe"
            foreground = "#ffffff" if selected else "#1e293b"
            button.configure(text=("● " if selected else "") + self.scope_titles[i],
                             bg=background, fg=foreground, activebackground=background,
                             activeforeground=foreground,
                             highlightbackground="#1e3a8a" if selected else "#94a3b8")
        self.scope_pages[index].tkraise()
        if index == 1 and hasattr(self, "web_summary"):
            self._refresh_web_panel()

    def _focus_scope(self, index):
        index %= len(self.scope_pages)
        self._select_scope(index)
        self.scope_buttons[index].focus_set()
        return "break"

    @staticmethod
    def _panel_label(parent, text, *, heading=False):
        label = tk.Label(parent, text=text, anchor="w", justify="left", wraplength=710,
                         bg="#eff6ff", fg="#1e3a8a" if heading else "#334155",
                         font=("맑은 고딕", 13 if heading else 10, "bold" if heading else "normal"))
        label.pack(fill="x", padx=22, pady=(16 if heading else 8, 0))
        return label

    def _build_full_panel(self, parent):
        self._panel_label(parent, "프로그램 전체 업데이트", heading=True)
        self._panel_label(parent, "공식 배포 파일을 검증하고 새 버전으로 다시 시작합니다.\n"
                          "Discord 봇은 종료 후 다시 연결하며, 실행 중인 웹서버는 유지합니다.")
        card = tk.Frame(parent, bg="#ffffff", padx=18, pady=16,
                        highlightthickness=1, highlightbackground="#bfdbfe")
        card.pack(fill="x", padx=22, pady=18)
        for text in (f"현재 프로그램  {self.app_version}",
                     "배포 파일명  bosstimer_update_버전.zip",
                     "구성  프로그램 본체 · Discord 봇 · 본체에 포함된 웹 / 알림 모듈"):
            tk.Label(card, text=text, anchor="w", justify="left", bg="#ffffff", fg="#334155",
                     font=("맑은 고딕", 11), wraplength=680).pack(fill="x", pady=5)
        self.full_summary = tk.StringVar(parent, value="전체 업데이트 확인 전")
        tk.Label(parent, textvariable=self.full_summary, anchor='w', justify='left', wraplength=710,
                 bg='#eff6ff', fg='#1e3a8a', font=('맑은 고딕', 11, 'bold')).pack(fill='x', padx=22, pady=12)
        actions = ttk.Frame(parent, padding=20)
        actions.pack(fill="x")
        from program_updater import UPDATE_ENABLED
        self.full_check_button = ttk.Button(actions, text="전체 업데이트 확인", command=self._check_full,
                                            state='normal' if UPDATE_ENABLED else 'disabled')
        self.full_check_button.pack(side="left")
        self.full_install_button = ttk.Button(actions, text="다운로드 / 적용", state="disabled", command=self._install_full)
        self.full_install_button.pack(side="left", padx=8)
        self._panel_label(parent, "Discord 명령  /보탐 업데이트 전체 비밀번호\n"
                          "현재 송출 담당 PC를 우선해 업데이트하며, 결과 안내 후 다시 시작합니다.")
        self._panel_label(parent, "매일 03:00 이후 하루 한 번 확인합니다. 꺼져 있었다면 다음 실행 때 확인합니다.\n"
                          "소스 실행에서는 자동 확인·설치를 하지 않습니다. 이전 버전과 복원용 설정은 보관합니다.")
        if not UPDATE_ENABLED:
            self.full_summary.set('준비 중 · 실제 업데이트 연결 승인 대기')

    def _check_full(self):
        def done(row):
            self.full_row = row
            self.full_summary.set('새 전체 버전: ' + row['version'] if row else '현재 프로그램이 최신 버전입니다.')
            self._status('전체 배포 확인 완료' if row else '현재 프로그램이 최신 버전입니다.')
            import sys
            from program_updater import UPDATE_ENABLED
            self.full_install_button.configure(state='normal' if UPDATE_ENABLED and row and getattr(sys, 'frozen', False) else 'disabled')
        self._work('전체 배포 확인 중…', self.app._get_program_update_service().updater.check, done, domain='full')

    def _install_full(self):
        if not self.app._show_centered_messagebox('askyesno', '전체 업데이트',
                '검증 완료 후 프로그램과 Discord 봇을 다시 시작할까요?\n'
                '실행 중인 웹서버와 사용자 설정은 유지합니다.', parent=self.window, default='no'):
            return
        service = self.app._get_program_update_service()
        def done(result):
            self._status(result['message'])
            service.schedule(result)
        self._work('전체 업데이트 다운로드·검증 중…', service.prepare, done, domain='full')

    def _daily_full(self):
        import sys
        from program_updater import UPDATE_ENABLED
        from program_update_guard import active
        if (UPDATE_ENABLED and getattr(sys, 'frozen', False) and not active() and not self.busy and self.prompt is None
                and not self.root.grab_current()):
            service = self.app._get_program_update_service()
            def task():
                return service.prepare(automatic=True) if service.updater.claim_daily() else None
            def done(result):
                if result:
                    self._status(result['message'])
                    service.schedule(result, automatic=True)
            self._work('전체 업데이트 일정 확인 중…', task, done, silent=True, domain='full')
        self.root.after(60_000, self._daily_full)

    def _web_updater(self):
        return getattr(getattr(self.app, "web_service_manager", None), "page_updater", None)

    def _build_web_panel(self, parent):
        self._panel_label(parent, "웹페이지 업데이트", heading=True)
        self._panel_label(parent, "기존 수동 웹 업데이트를 이곳에서도 실행할 수 있습니다.\n"
                          "웹페이지 파일을 확인하고 변경된 파일을 적용합니다. 프로그램 전체 업데이트와는 별개입니다.")
        self.web_summary = tk.StringVar(parent, value="웹 업데이트 상태 확인 중…")
        tk.Label(parent, textvariable=self.web_summary, bg="#ffffff", fg="#1e3a8a",
                 anchor="w", justify="left", padx=18, pady=16, wraplength=710,
                 font=("맑은 고딕", 11), highlightthickness=1,
                 highlightbackground="#bfdbfe").pack(fill="x", padx=22, pady=18)
        actions = ttk.Frame(parent, padding=(22, 0))
        actions.pack(fill="x")
        self.web_update_button = ttk.Button(actions, text="수동 웹 업데이트", command=self._request_web_update)
        self.web_update_button.pack(side="left")
        self._panel_label(parent, "현재 웹 업데이트는 ZIP을 사용하지 않습니다.\n"
                          "공식 저장소 web_assets 폴더의 다음 6개 파일을 사용합니다.\n"
                          "index.html · style.css · schedule.js · login.html · login.js · metadata.json")
        self._panel_label(parent, "Discord 명령\n/웹서버 업데이트 비밀번호\n/보탐 업데이트 웹서버 비밀번호")
        self._refresh_web_panel()

    def _refresh_web_panel(self):
        updater = self._web_updater()
        try:
            state = updater.status() if updater is not None else None
            if state is None:
                message, enabled = "웹 모듈이 준비되지 않았습니다.", False
            else:
                message = (f"{state.get('message', '웹 업데이트 확인 전')}\n"
                           f"적용 버전: {state.get('version') or '확인 전'}  |  "
                           f"마지막 확인: {display_date(state.get('checked_at') or '없음')}")
                enabled = not state.get("busy")
        except Exception:
            message, enabled = "웹 업데이트 상태를 읽지 못했습니다. 웹서비스의 진단 로그를 확인해 주세요.", False
        if self.web_summary.get() != message:
            self.web_summary.set(message)
        self.web_update_button.configure(state="normal" if enabled else "disabled")

    def _request_web_update(self):
        try:
            updater = self._web_updater()
            if updater is None:
                raise ValueError("웹 모듈이 준비되지 않았습니다.")
            updater.request()  # Existing nonblocking manual updater owns all network work.
        except Exception as exc:
            self.app._show_centered_messagebox("showerror", "웹 업데이트", str(exc), parent=self.window)
        self._refresh_web_panel()

    def _build_update_tabs(self, parent):
        # Windows' native ttk notebook paints its own tab background while
        # accepting our white foreground. Use local Tk widgets so both colors
        # remain under our control, without changing the application's theme.
        self.tabs = tk.Frame(parent, bg="#eff6ff")
        self.tabs.pack(fill="both", expand=True, padx=16, pady=10)
        bar = tk.Frame(self.tabs, bg="#eff6ff")
        bar.pack(fill="x")
        content = tk.Frame(self.tabs, bg="#1e3a8a", padx=2, pady=2)
        content.pack(fill="both", expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self.update_tab_titles = ("배포 목록 / 패치 설명", "설치 / 실패 내역")
        self.update_tab_buttons = []
        self.update_tab_pages = []
        for index, title in enumerate(self.update_tab_titles):
            button = tk.Button(
                bar, text=title, command=lambda i=index: self._select_update_tab(i),
                font=("맑은 고딕", 10, "bold"), padx=16, pady=9,
                bd=0, relief="flat", cursor="hand2", takefocus=True,
                highlightthickness=2, highlightbackground="#94a3b8", highlightcolor="#f59e0b",
            )
            button.pack(side="left", padx=(0, 4))
            button.bind("<Return>", lambda _event, i=index: self._select_update_tab(i))
            button.bind("<Left>", lambda _event, i=index: self._focus_update_tab(i - 1))
            button.bind("<Right>", lambda _event, i=index: self._focus_update_tab(i + 1))
            page = tk.Frame(content, bg="#eff6ff", padx=8, pady=8)
            page.grid(row=0, column=0, sticky="nsew")
            self.update_tab_buttons.append(button)
            self.update_tab_pages.append(page)
        self._select_update_tab(0)
        return self.update_tab_pages

    def _select_update_tab(self, index):
        self.selected_update_tab = index
        for i, button in enumerate(self.update_tab_buttons):
            selected = i == index
            background = "#1e3a8a" if selected else "#dbeafe"
            foreground = "#ffffff" if selected else "#1e293b"
            button.configure(
                text=("● " if selected else "") + self.update_tab_titles[i],
                bg=background, fg=foreground,
                activebackground=background if selected else "#bfdbfe",
                activeforeground=foreground,
                highlightbackground="#1e3a8a" if selected else "#94a3b8",
            )
        self.update_tab_pages[index].tkraise()

    def _focus_update_tab(self, index):
        index %= len(self.update_tab_pages)
        self._select_update_tab(index)
        self.update_tab_buttons[index].focus_set()
        return "break"

    def _check_when_ready(self):
        if self.window is None or not self.window.winfo_exists():
            return
        if self.busy or self.prompt is not None:
            self.window.after(200, self._check_when_ready)
        else:
            self.check()

    def _text_box(self, parent, height):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=(8, 0))
        text = tk.Text(frame, height=height, wrap="word", font=("맑은 고딕", 10),
                       bg="#ffffff", fg="#0f172a", relief="flat", padx=10, pady=8, state="disabled")
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(side="left", fill="both", expand=True)
        return text

    @staticmethod
    def _set_text(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def _build_history_actions(self, parent):
        actions = tk.Frame(parent, bg="#1e3a8a")
        actions.pack(side="right", padx=(12, 0))
        ttk.Button(actions, text="버전별 패치내역", command=self._open_patch_notes).pack(fill="x")
        ttk.Button(actions, text="업데이트·복구 규칙", command=self._open_update_policy).pack(fill="x", pady=(6, 0))

    def _close(self):
        if self.patch_window is not None and self.patch_window.winfo_exists():
            self.patch_window.destroy()
        self.patch_window = None
        policy = getattr(self, 'policy_window', None)
        if policy is not None and policy.winfo_exists():
            policy.destroy()
        self.policy_window = None
        self.window.destroy()
        self.window = None

    def _open_update_policy(self):
        existing = getattr(self, 'policy_window', None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        parent = self._parent()
        try:
            from update_policy import policy_text
            content = policy_text()
        except (OSError, UnicodeError) as exc:
            self.app._show_centered_messagebox('showerror', '업데이트·복구 규칙',
                '배포된 UPDATE_POLICY.md를 읽지 못했습니다.\n' + str(exc), parent=parent)
            return
        win = self.policy_window = tk.Toplevel(parent)
        win.title('업데이트·복구 규칙')
        win.geometry('880x700')
        win.minsize(620, 440)
        win.configure(bg='#eff6ff')
        win.transient(parent)
        tk.Label(win, text='업데이트·복구 규칙', bg='#1e3a8a', fg='white',
                 font=('맑은 고딕', 16, 'bold'), anchor='w', padx=18, pady=16).pack(fill='x')
        tk.Label(win, text='UPDATE_POLICY.md · 구현 범위와 후속 보완 항목은 8절에서 확인할 수 있습니다.',
                 bg='#eff6ff', fg='#334155', anchor='w', wraplength=800).pack(fill='x', padx=16, pady=10)
        body = self._text_box(win, height=24)
        body.configure(font=('맑은 고딕', 11))
        self._set_text(body, content)
        body.tag_configure('heading', foreground='#1e3a8a', font=('맑은 고딕', 13, 'bold'),
                           spacing1=10, spacing3=6)
        for number, line in enumerate(content.splitlines(), 1):
            if line.startswith('#'):
                body.tag_add('heading', f'{number}.0', f'{number}.end')
        ttk.Button(win, text='닫기', command=win.destroy).pack(pady=12)
        win.bind('<Escape>', lambda _event: win.destroy())
        center_window(win, parent)

    def _open_patch_notes(self):
        if self.patch_window is not None and self.patch_window.winfo_exists():
            self.patch_window.lift()
            self.patch_window.focus_force()
            return
        parent = self._parent()
        win = self.patch_window = tk.Toplevel(parent)
        win.title("버전별 패치내역")
        win.geometry("760x600")
        win.minsize(580, 420)
        win.configure(bg="#eff6ff")
        win.transient(parent)
        tk.Label(win, text="버전별 패치내역", bg="#1e3a8a", fg="white",
                 font=("맑은 고딕", 16, "bold"), anchor="w", padx=18, pady=16).pack(fill="x")
        tk.Label(win, text=f"현재 프로그램: {self.app_version} · 준비 중인 변경사항은 버전 번호 없이 표시합니다.",
                 bg="#eff6ff", fg="#334155", anchor="w", wraplength=700).pack(fill="x", padx=16, pady=10)
        body = self._text_box(win, height=20)
        try:
            catalog = self.updater.snapshot().get("catalog", [])
        except Exception:
            catalog = []  # Local notes remain available even if update state is unreadable.
        self._set_text(body, patch_history_text(self.app_version, catalog))
        ttk.Button(win, text="닫기", command=win.destroy).pack(pady=12)
        win.bind("<Escape>", lambda _event: win.destroy())
        center_window(win, parent)

    def _save_options(self):
        try:
            self.updater.configure(self.auto.get(), self.after_time.get().strip())
            self._status("자동 업데이트 설정을 저장했습니다.")
        except Exception as exc:
            self.app._show_centered_messagebox("showerror", "업데이트 설정", str(exc), parent=self.window)

    def check(self):
        self._work("GitHub 배포 목록 확인 중…", self.updater.check, self._checked)

    def _checked(self, rows, automatic=False):
        self._status("배포 목록 확인 완료" if rows else "아직 등록된 공지 / AI 업데이트가 없습니다.")
        self._render()
        if automatic:
            state = self.updater.snapshot()
            row = next((row for row in rows if not row["blocked"]
                        and row["version"] not in state.get("failed_versions", {})
                        and self.updater.newer(row["version"], state)), None)
            if row is not None:
                self._offer(row)

    def _render(self):
        if self.window is None or not self.window.winfo_exists():
            return
        try:
            state = self.updater.snapshot()
        except Exception as exc:
            self._status(f"업데이트 기록 오류: {exc} · 원본을 보존했습니다.")
            return
        selected = self.tree.selection()
        old_version = self.rows[int(selected[0])]["version"] if selected and self.rows else ""
        self.rows = state.get("catalog", [])
        self.tree.delete(*self.tree.get_children())
        loaded = state.get("runtime_loaded", {})
        running = loaded.get("version") or state.get("active") or "미시작"
        origin = " (내장)" if loaded.get("source") == "bundled" else ""
        self.summary.set(f"현재 알리미 모듈: {running}{origin}  |  다음 실행 적용: {state.get('pending') or '없음'}"
                         f"  |  마지막 확인: {display_date(state.get('last_checked', '없음'))}")
        latest = {}
        for entry in state["history"]:
            if entry.get("version"):
                latest[entry["version"]] = entry["status"]
        for index, row in enumerate(self.rows):
            version = row["version"]
            if version == state.get("pending"):
                status = "설치됨 · 다음 실행 적용"
            elif version == state.get("active"):
                status = "파일 적용됨"
            elif not self.updater.newer(version, state):
                status = "이전 버전 · 설치 불가"
            else:
                status = latest.get(version, "미설치")
            if row["blocked"]:
                status = "설치 불가 · 설명 확인"
            elif version in state.get("failed_versions", {}) and self.updater.newer(version, state):
                status = "시작 실패 · 수동 재시도"
            self.tree.insert("", "end", iid=str(index), values=(display_date(row["date"]), version, status, row["title"]))
            if version == old_version:
                self.tree.selection_set(str(index))
        if self.rows and not self.tree.selection():
            self.tree.selection_set("0")
        self._details()
        self._set_text(self.history_text, "\n\n".join(
            f"{display_date(item['date'])}  |  {item.get('version') or '-'}  |  {item['status']}\n{item.get('detail', '')}"
            for item in reversed(state["history"])) or "아직 설치 내역이 없습니다.")

    def _details(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            self._set_text(self.detail, "공식 배포가 등록되면 날짜, 버전, 설명이 여기에 표시됩니다.")
            return
        row = self.rows[int(selected[0])]
        failure = self.updater.snapshot().get("failed_versions", {}).get(row["version"], "")
        self._set_text(self.detail, f"{row['title']}\n{row['name']}\n{row['blocked']}\n{failure}\n\n{row['description']}")

    def _install_selected(self):
        if self.busy or self.prompt is not None:
            self._status("진행 중인 작업 또는 안내창을 먼저 완료해 주세요.")
            return
        selection = self.tree.selection()
        if not selection:
            return
        row = self.rows[int(selection[0])]
        if row["blocked"] or not self.updater.newer(row["version"], self.updater.snapshot()):
            self.app._show_centered_messagebox("showwarning", "설치 불가",
                row["blocked"] or "같은 버전 또는 구버전은 설치할 수 없습니다.", parent=self.window)
            return
        answer = self.app._show_centered_messagebox("askyesno", "업데이트 설치",
            f"공지 / AI 모듈 {row['version']}을 설치할까요?\n검증 후 설치하고 다음 실행 때 파일을 적용합니다.\n"
            "보탐매니저를 자동으로 종료하거나 재시작하지 않습니다.", parent=self.window, default="no")
        if answer:
            self._install(row["version"])

    def _install(self, version):
        def done(_):
            self._status(f"{version} 설치 완료 · 다음 실행 때 파일이 적용됩니다.")
            self._render()
            self.app._show_centered_messagebox("showinfo", "업데이트 설치 완료", self.status_text,
                                               parent=self._parent())
        self._work(f"{version} 다운로드 및 검증 중…", lambda: self.updater.install(version), done, failure_version=version)

    def _offer(self, row):
        # Do not steal a modal dialog or begin an invisible countdown.
        if not self.updater.snapshot()["auto_enabled"]:
            return
        if self.busy or self.root.grab_current() is not None or not self._parent().winfo_viewable():
            self.root.after(5000, lambda: self._offer(row))
            return
        if not self.updater.newer(row["version"], self.updater.snapshot()):
            return
        if row["version"] in self.updater.snapshot().get("failed_versions", {}):
            return
        parent = self._parent()
        win = self.prompt = tk.Toplevel(parent)
        win.title("새 업데이트")
        win.configure(bg="#eff6ff")
        win.resizable(False, False)
        win.transient(parent)
        tk.Label(win, text="업데이트가 진행됩니다", bg="#eff6ff", fg="#1d4ed8",
                 font=("맑은 고딕", 14, "bold")).pack(padx=28, pady=(20, 8))
        text = tk.StringVar(win)
        tk.Label(win, textvariable=text, bg="#eff6ff", fg="#334155", justify="left").pack(padx=28, pady=8)
        finished = False

        def finish(install):
            nonlocal finished
            if finished:
                return
            finished = True
            win.grab_release()
            win.destroy()
            self.prompt = None
            if install:
                self._install(row["version"])
            else:
                try:
                    self.updater.record("사용자 취소 · 미설치", row["version"], "업데이트 확인 창에서 수동 설치 가능")
                except Exception as exc:
                    self.app._append_debug_log(f"ai_update_cancel_record_failed {exc}")
                self._status("자동 설치를 취소했습니다. 업데이트 확인에서 직접 설치할 수 있습니다.")
                self._render()
        buttons = ttk.Frame(win, padding=12)
        buttons.pack(fill="x", padx=16, pady=(0, 8))
        ttk.Button(buttons, text="지금 업데이트", command=lambda: finish(True)).pack(side="left")
        cancel = ttk.Button(buttons, text="취소", command=lambda: finish(False))
        cancel.pack(side="right")
        win.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        win.bind("<Escape>", lambda _: finish(False))
        win.bind("<Return>", lambda _: finish(False))
        text.set(f"공지 / AI 모듈 {row['version']}\n5초 후 설치합니다. 원하지 않으면 취소를 눌러 주세요.\n설치 파일 적용은 다음 실행 때 이루어집니다.")
        center_window(win, parent)
        win.grab_set()
        cancel.focus_set()
        deadline = time.monotonic() + 5

        def tick():
            if finished:
                return
            seconds = max(0, int(deadline - time.monotonic() + 0.999))
            text.set(f"공지 / AI 모듈 {row['version']}\n{seconds}초 후 설치합니다. 원하지 않으면 취소를 눌러 주세요.\n설치 파일 적용은 다음 실행 때 이루어집니다.")
            if not seconds:
                finish(True)
            else:
                win.after(100, tick)
        tick()
