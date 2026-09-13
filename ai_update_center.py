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
        runtime = getattr(app, "notice_runtime", None)
        self.updater = runtime.updater if runtime is not None else AiModuleUpdater(data_root, app_version)
        self.window = None
        self.prompt = None
        self.busy = False
        self.events = queue.Queue()
        self.rows = []
        self.status_text = "업데이트 정보를 확인할 수 있습니다."
        self.root.after(100, self._poll)
        self.root.after(2000, self._daily)

    def _parent(self):
        for widget in (self.window, getattr(self.app, "schedule_window", None), self.root):
            if widget is not None and widget.winfo_exists() and widget.winfo_viewable():
                return widget
        return self.root

    def _status(self, message):
        self.status_text = message
        if self.window is not None and self.window.winfo_exists():
            self.status.set(message)

    def _work(self, message, task, done, *, failure_version="", silent=False):
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
        self.root.after(100, self._poll)

    def _daily(self):
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
        win.title("업데이트 확인 · 공지 / AI 모듈")
        win.configure(bg="#eff6ff")
        win.geometry("880x640")
        win.minsize(740, 530)
        win.transient(parent)
        tk.Label(win, text="업데이트 센터", font=("맑은 고딕", 17, "bold"),
                 bg="#eff6ff", fg="#0f172a", anchor="w").pack(fill="x", padx=20, pady=(16, 4))
        tk.Label(win, text=f"공식 배포: {REPOSITORY}  ·  날짜는 한국시간",
                 bg="#eff6ff", fg="#475569", anchor="w").pack(fill="x", padx=20)
        self.summary = tk.StringVar(win)
        tk.Label(win, textvariable=self.summary, bg="#eff6ff", fg="#1d4ed8", anchor="w").pack(fill="x", padx=20, pady=8)
        options = ttk.Frame(win, padding=(12, 8))
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
        self.tabs = ttk.Notebook(win)
        self.tabs.pack(fill="both", expand=True, padx=16, pady=10)
        releases = ttk.Frame(self.tabs, padding=8)
        history = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(releases, text="배포 목록 / 패치 설명")
        self.tabs.add(history, text="설치 / 실패 내역")
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
        actions = ttk.Frame(win, padding=(16, 0, 16, 8))
        actions.pack(fill="x")
        ttk.Button(actions, text="지금 확인", command=self.check).pack(side="left")
        ttk.Button(actions, text="선택 버전 설치 / 재시도", command=self._install_selected).pack(side="left", padx=8)
        ttk.Button(actions, text="닫기", command=self._close).pack(side="right")
        self.status = tk.StringVar(win, value=self.status_text)
        tk.Label(win, textvariable=self.status, anchor="w", justify="left", wraplength=825,
                 bg="#eff6ff", fg="#334155").pack(fill="x", padx=20, pady=(0, 6))
        tk.Label(win, text="설정·스케줄·음성은 변경하지 않습니다. 설치 파일은 다음 실행 때 적용됩니다.\n"
                 "알림 기능은 독립 모듈로 실행됩니다. 기능별 연결 상태는 알림 관리 창에서 확인해 주세요.",
                 anchor="w", justify="left", bg="#eff6ff", fg="#64748b").pack(fill="x", padx=20, pady=(0, 12))
        win.protocol("WM_DELETE_WINDOW", self._close)
        center_window(win, parent)
        self._render()
        win.after(200, self._check_when_ready)

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

    def _close(self):
        self.window.destroy()
        self.window = None

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
        self.summary.set(f"시작 확인: {running}{origin}  |  다음 실행 적용: {state.get('pending') or '없음'}"
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
