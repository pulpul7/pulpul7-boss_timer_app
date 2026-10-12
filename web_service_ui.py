"""Independent Tk control panel. All network/process work stays in the manager."""
from __future__ import annotations

import threading
from datetime import datetime
import secrets
import tkinter as tk
from tkinter import ttk
import webbrowser

from web_access_settings import validate_pin


class WebServiceWindow:
    def __init__(self, root, manager, messagebox):
        self.root, self.manager, self.messagebox = root, manager, messagebox
        self.window = None
        self.after_id = None
        self.selected = "서비스"
        self.pending_job = None
        self.pending_operation = None
        self.pending_ddns_job = None
        self.pending_ddns_operation = None
        self.pending_https_job = None

    def open(self, parent):
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            return
        self.window = window = tk.Toplevel(parent)
        window.title("웹 스케줄 서비스")
        window.configure(bg="#f1f5f9")
        window.transient(parent)
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.minsize(720, 650)
        parent.update_idletasks()
        width, height = 760, 700
        x = max(0, parent.winfo_rootx() + (parent.winfo_width()-width)//2)
        y = max(0, parent.winfo_rooty() + (parent.winfo_height()-height)//2)
        window.geometry(f"{width}x{height}+{x}+{y}")
        self.font = ("맑은 고딕", 9)
        header = tk.Frame(window, bg="#1e3a8a")
        header.pack(fill="x")
        tk.Label(header, text="웹 스케줄 서비스", font=("맑은 고딕", 14, "bold"),
                 bg="#1e3a8a", fg="white", anchor="w").pack(fill="x", padx=18, pady=(12, 3))
        tk.Label(header, text="공동 관리 · 기본은 Discord와 함께 승계 · 예외 운영은 '공동 운영'에서 선택", font=self.font,
                 bg="#1e3a8a", fg="#dbeafe", anchor="w").pack(fill="x", padx=18, pady=(0, 12))
        outer = tk.Frame(window, bg="#f1f5f9")
        outer.pack(fill="both", expand=True, padx=14, pady=12)
        tabs = tk.Frame(outer, bg="#f1f5f9")
        tabs.pack(fill="x")
        self.buttons, self.frames = {}, {}
        self.content = tk.Frame(outer, bg="#1e3a8a", bd=0)
        self.content.pack(fill="both", expand=True)
        for name in ("서비스", "공동 운영", "비밀번호", "접속 관리", "네트워크", "무료 주소", "HTTPS", "외부 공개", "진단 로그"):
            self.buttons[name] = tk.Button(tabs, text=name, font=self.font, cursor="hand2",
                command=lambda tab=name: self._select(tab), highlightthickness=0)
            self.buttons[name].pack(side="left", padx=(0, 3), ipadx=4, ipady=4)
            self.frames[name] = tk.Frame(self.content, bg="#f8fafc")
        self._build_service(self.frames["서비스"])
        self._build_joint(self.frames["공동 운영"])
        self.operation_buttons = []
        self.access_message = tk.StringVar(value="")
        self._build_password(self.frames["비밀번호"])
        self._build_access(self.frames["접속 관리"])
        self._build_network(self.frames["네트워크"])
        self._build_ddns(self.frames["무료 주소"])
        self._build_https(self.frames["HTTPS"])
        self._build_public(self.frames["외부 공개"])
        self._build_logs(self.frames["진단 로그"])
        self._select("비밀번호" if self.manager.status()["enabled"] and not self.manager.access_status()["configured"] else "서비스")
        self._refresh()

    def _label(self, parent, text, **options):
        return tk.Label(parent, text=text, font=self.font, bg="#f8fafc", fg="#0f172a", **options)

    def _button(self, parent, text, command):
        return tk.Button(parent, text=text, command=command, font=self.font, cursor="hand2",
                         bg="#dbeafe", fg="#1e3a8a", activebackground="#bfdbfe", relief="raised", bd=1)

    def _build_joint(self, frame):
        relay = self.manager.delegation
        self.split_web = tk.BooleanVar(value=relay.status()["split_for_next"])
        tk.Checkbutton(frame, text="다음 승계는 Discord만 · 기존 관리자 PC의 웹 유지", variable=self.split_web,
            command=lambda: relay.choose_next(self.split_web.get()), font=("맑은 고딕", 10, "bold"),
            bg="#f8fafc", fg="#1e3a8a", activebackground="#f8fafc", selectcolor="white").pack(
                anchor="w", padx=18, pady=(18, 10))
        self._label(frame, "웹을 운영할 수 없는 PC가 Discord 송출을 맡아야 할 때, 승계 전에 선택하세요.\n"
                    "한 번의 승계에만 적용합니다. 연결 중·재시도 중이라는 이유로 자동 선택하지 않습니다.\n"
                    "같은 시즌·게임 서버에서, 모든 관리자의 GUI와 봇을 새 버전으로 사용해야 합니다.",
                    anchor="w", justify="left", wraplength=660).pack(fill="x", padx=22, pady=8)
        self.joint_text = tk.StringVar()
        self._label(frame, "", textvariable=self.joint_text, anchor="w", justify="left", wraplength=660).pack(
            fill="x", padx=22, pady=18)
        actions = tk.Frame(frame, bg="#f8fafc")
        actions.pack(fill="x", padx=22, pady=8)
        self.joint_sync = self._button(actions, "지금 웹 갱신", lambda: self._joint_action(relay.sync_now))
        self.joint_return = self._button(actions, "웹도 이 PC로 승계", lambda: self._joint_action(self.manager.retry_public))
        self.joint_stop = self._button(actions, "이 PC 웹 종료", lambda: self._joint_action(self.manager.stop_web))
        self.joint_force = self._button(actions, "웹 단독 강제 실행", lambda: self._joint_action(self.manager.force_start_web))
        self.joint_stop.configure(bg="#fee2e2", fg="#991b1b")
        self.joint_force.configure(bg="#dcfce7", fg="#166534")
        for button in (self.joint_sync, self.joint_return, self.joint_stop, self.joint_force):
            button.pack(side="left", padx=(0, 10), ipady=4)
        self._label(frame, "스케줄 마지막 변경 1분 30초 후 자동 업로드하고, 기존 웹 PC가 요청을 받아 자동 반영합니다.\n"
                    "기존 웹 PC에는 확인창이 뜨지 않습니다. GitHub 스케줄을 반복 조회하지 않습니다.\n"
                    "'웹도 이 PC로 승계'는 기존 웹의 외부 접근 중단을 자동 확인한 뒤 진행합니다.\n"
                    "기존 웹 PC의 로컬 스케줄은 보존하고, 웹 표시 데이터만 갱신합니다.\n"
                    "단독 강제 실행: 상대 웹 종료를 확인하고 이 PC의 현재 스케줄로 공개합니다.\n"
                    "디코 송출권한은 그대로이며, 스케줄 업로드·다운로드를 기다리지 않습니다.",
                    anchor="w", justify="left", wraplength=660).pack(fill="x", padx=22, pady=14)

    def _joint_action(self, callback):
        try:
            callback()
        except Exception as exc:
            self.messagebox("showerror", "공동 웹 운영", str(exc), parent=self.window)

    def _refresh_joint(self):
        state = self.manager.delegation.status()
        manual = self.manager.standalone.status()
        self.split_web.set(state["split_for_next"])
        roles = {"host": "이 PC: 웹 담당", "source": "이 PC: Discord·스케줄 담당", "": "일반 운영"}
        role = ("이 PC: 단독 웹 담당" if manual['local'] else "다른 PC에서 단독 웹 운영") if manual['state'] == 'active' else roles[state['role']]
        host = manual['owner_name'] if manual['state'] == 'active' else state['host']
        self.joint_text.set(f"{role}\n웹 담당: {host}   ·   스케줄 담당: {state['source']}\n"
                            f"{state['message']}\n최근 웹 반영: {state['last_applied_at']}\n"
                            f"{manual['message']}")
        for button in (self.joint_sync, self.joint_return):
            button.configure(state="normal" if state["role"] == "source" else "disabled")
        for button in (self.joint_stop, self.joint_force):
            button.configure(state="disabled" if manual["busy"] else "normal")

    def _build_service(self, frame):
        self.enabled = tk.BooleanVar(value=self.manager.status()["enabled"])
        tk.Checkbutton(frame, text="웹서비스 사용", variable=self.enabled, command=self._toggle,
            font=("맑은 고딕", 11, "bold"), bg="#f8fafc", fg="#1e3a8a",
            activebackground="#f8fafc", selectcolor="white").pack(anchor="w", padx=18, pady=(16, 8))
        self._label(frame, "켜면 웹서버를 시작합니다. 처음에는 '비밀번호'에서 네 자리만 설정하세요.",
                    anchor="w").pack(fill="x", padx=22)
        status = tk.Frame(frame, bg="#f8fafc")
        status.pack(fill="x", padx=22, pady=14)
        self.state_text, self.address, self.detail, self.count, self.schedule_text = (tk.StringVar() for _ in range(5))
        self.scope_text = tk.StringVar(value="같은 PC만 접속 가능")
        for row, (name, variable) in enumerate((("웹서버", self.state_text),
                                               ("접속 범위", None), ("웹 요청 수", self.count),
                                               ("스케줄 자료", self.schedule_text))):
            self._label(status, name, anchor="w").grid(row=row, column=0, sticky="w", padx=(0, 24), pady=4)
            label = self._label(status, "같은 PC만 접속 가능" if variable is None else "", anchor="w")
            if variable is not None:
                label.configure(textvariable=variable)
            elif name == "접속 범위":
                label.configure(textvariable=self.scope_text)
            label.grid(row=row, column=1, sticky="w", pady=4)
            if row == 0:
                self.state_label = label
        self._label(frame, "접속 주소", anchor="w").pack(fill="x", padx=22)
        tk.Entry(frame, textvariable=self.address, state="readonly", font=self.font,
                 readonlybackground="white").pack(fill="x", padx=22, pady=6, ipady=5)
        actions = tk.Frame(frame, bg="#f8fafc")
        actions.pack(fill="x", padx=22, pady=6)
        self.copy_button = self._button(actions, "주소 복사", self._copy_address)
        self.open_button = self._button(actions, "브라우저에서 열기", self._open_browser)
        self.retry_button = self._button(actions, "다시 시도", lambda: self._retry(False))
        self.port_button = self._button(actions, "다른 포트로 재시도", lambda: self._retry(True))
        for button in (self.copy_button, self.open_button, self.retry_button, self.port_button):
            button.pack(side="left", padx=(0, 6), ipady=3)
        tk.Label(frame, textvariable=self.detail, font=self.font, bg="#f8fafc", fg="#475569",
                 anchor="w", justify="left", wraplength=630).pack(fill="x", padx=22, pady=10)
        updates = tk.LabelFrame(frame, text="웹 화면 업데이트", font=self.font,
                                bg="#f8fafc", fg="#1e3a8a", padx=10, pady=8)
        updates.pack(fill="x", padx=22, pady=(0, 12))
        controls = tk.Frame(updates, bg="#f8fafc")
        controls.pack(fill="x")
        data = self.manager.page_updater.status()
        self.page_update_button = self._button(controls, "수동 웹 업데이트", self._update_pages)
        self.page_update_button.pack(side="left", padx=(0, 10))
        self.page_auto_enabled = tk.BooleanVar(value=data["enabled"])
        tk.Checkbutton(controls, text="자동 업데이트", variable=self.page_auto_enabled, font=self.font,
                       bg="#f8fafc", activebackground="#f8fafc").pack(side="left")
        self.page_update_hour = tk.StringVar(value=f"{data['hour']:02}")
        self.page_update_minute = tk.StringVar(value=f"{data['minute']:02}")
        tk.Spinbox(controls, from_=0, to=23, width=3, textvariable=self.page_update_hour,
                   format="%02.0f", font=self.font).pack(side="left", padx=(6, 2))
        self._label(controls, ":").pack(side="left")
        tk.Spinbox(controls, from_=0, to=59, width=3, textvariable=self.page_update_minute,
                   format="%02.0f", font=self.font).pack(side="left", padx=(2, 8))
        self._button(controls, "설정 저장", self._save_page_update).pack(side="left")
        self.page_update_message = tk.StringVar(value=data["message"])
        tk.Label(updates, textvariable=self.page_update_message, font=self.font, bg="#f8fafc", fg="#475569",
                 anchor="w", justify="left", wraplength=630).pack(fill="x", pady=(6, 0))
        self._label(updates, "한국시간 · 파일 복사 후 웹만 재시작합니다. Discord 송출은 유지합니다.",
                    anchor="w").pack(fill="x", pady=(3, 0))

    def _update_pages(self):
        try:
            self.manager.page_updater.request()
        except Exception as exc:
            self.messagebox("showerror", "웹 업데이트", str(exc), parent=self.window)
        self._refresh_page_update()

    def _save_page_update(self):
        try:
            hour, minute = int(self.page_update_hour.get()), int(self.page_update_minute.get())
            self.manager.page_updater.configure(self.page_auto_enabled.get(), hour, minute)
            self.page_update_hour.set(f"{hour:02}")
            self.page_update_minute.set(f"{minute:02}")
            self.page_update_message.set("자동 업데이트 설정을 저장했습니다.")
        except Exception as exc:
            self.messagebox("showerror", "웹 자동 업데이트 설정",
                "시간을 00:00~23:59 사이 숫자로 입력해주세요." if isinstance(exc, ValueError) else
                "설정을 저장하지 못했습니다. 진단 로그를 확인해주세요.", parent=self.window)

    def _refresh_page_update(self):
        data = self.manager.page_updater.status()
        self.page_update_button.configure(state="disabled" if data["busy"] else "normal")
        detail = data["message"]
        if data["checked_at"]:
            detail += "\n최근 확인: " + self._local_timestamp(data["checked_at"])
        if data["version"]:
            detail += " · 버전: " + data["version"]
        self.page_update_message.set(detail)

    def _operation(self, parent, text, command):
        button = self._button(parent, text, command)
        self.operation_buttons.append(button)
        return button

    def _build_password(self, frame):
        self.pin_status = tk.StringVar()
        tk.Label(frame, textvariable=self.pin_status, font=("맑은 고딕", 11, "bold"),
                 bg="#f8fafc", fg="#1e3a8a", anchor="w").pack(fill="x", padx=22, pady=(20, 10))
        self._label(frame, "길드원에게 알려줄 네 자리 비밀번호를 설정하세요. 별도 계정은 필요하지 않습니다.",
                    anchor="w", wraplength=630, justify="left").pack(fill="x", padx=22)
        self.current_pin = tk.StringVar()
        tk.Label(frame, textvariable=self.current_pin, font=self.font, bg="#f8fafc", fg="#1e3a8a",
                 anchor="w", wraplength=630, justify="left").pack(fill="x", padx=22, pady=(8, 0))
        row = tk.Frame(frame, bg="#f8fafc")
        row.pack(fill="x", padx=22, pady=16)
        self._label(row, "새 일반 비밀번호").pack(side="left", padx=(0, 10))
        self.pin = tk.StringVar()
        self.pin_entry = tk.Entry(row, textvariable=self.pin, width=8, show="●", font=("맑은 고딕", 12))
        self.pin_entry.pack(side="left", ipady=4)
        self.show_pin = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="보기", variable=self.show_pin, bg="#f8fafc", font=self.font,
                       command=lambda: self.pin_entry.configure(show="" if self.show_pin.get() else "●")).pack(side="left", padx=8)
        self._operation(row, "자동 생성", lambda: self.pin.set(f"{secrets.randbelow(10000):04d}")).pack(side="left", padx=4)
        self._operation(row, "비밀번호 저장", self._save_pin).pack(side="left", padx=4)
        guest = tk.LabelFrame(frame, text="게스트 비밀번호 관리", font=("맑은 고딕", 10, "bold"),
                              bg="#eef2ff", fg="#1e3a8a", bd=1, relief="solid")
        guest.pack(fill="x", padx=22, pady=(6, 10))
        self.guest_pin_status = tk.StringVar()
        tk.Label(guest, textvariable=self.guest_pin_status, font=self.font, bg="#eef2ff", fg="#1e3a8a",
                 anchor="w").pack(fill="x", padx=10, pady=(8, 4))
        self.current_guest_pin = tk.StringVar()
        tk.Label(guest, textvariable=self.current_guest_pin, font=self.font, bg="#eef2ff", fg="#1e3a8a",
                 anchor="w", wraplength=610, justify="left").pack(fill="x", padx=10, pady=(0, 4))
        row = tk.Frame(guest, bg="#eef2ff")
        row.pack(fill="x", padx=10, pady=6)
        self.guest_pin = tk.StringVar()
        self.guest_pin_entry = tk.Entry(row, textvariable=self.guest_pin, width=8, show="●", font=("맑은 고딕", 12))
        self.guest_pin_entry.pack(side="left", ipady=4)
        self.show_guest_pin = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text="보기", variable=self.show_guest_pin, bg="#eef2ff", font=self.font,
                       command=lambda: self.guest_pin_entry.configure(show="" if self.show_guest_pin.get() else "●")).pack(side="left", padx=8)
        self._operation(row, "자동 생성", lambda: self.guest_pin.set(f"{secrets.randbelow(10000):04d}")).pack(side="left", padx=4)
        self._operation(row, "게스트 비밀번호 저장", self._save_guest_pin).pack(side="left", padx=4)
        self._operation(row, "게스트 입장 중지", lambda: self._submit_access("guest_disable")).pack(side="left", padx=4)
        tk.Label(guest, text="일반 비밀번호와 다른 네 자리 숫자를 사용하세요. 변경·중지하면 게스트 로그인만 해제됩니다.",
                 font=self.font, bg="#eef2ff", fg="#0f172a", anchor="w", wraplength=610,
                 justify="left").pack(fill="x", padx=10, pady=(2, 8))
        self._label(frame, "로그인은 재시작·웹 승계 후에도 유지됩니다. 같은 비밀번호를 다시 저장해도 유지됩니다.\n"
                    "비밀번호를 바꾸면 해당 접속자만 입력 화면으로 돌아갑니다.\n"
                    "닉네임은 접속 관리에서 식별용으로 표시됩니다. 게스트 여부는 입력한 비밀번호로 구분합니다.\n"
                    "아래 버튼은 일반·게스트 모두의 로그인을 해제합니다.",
                    anchor="w", justify="left", wraplength=630).pack(fill="x", padx=22, pady=6)
        self._operation(frame, "모든 브라우저 로그인 해제", lambda: self._submit_access("logout_all")).pack(anchor="w", padx=22, pady=12)
        tk.Label(frame, textvariable=self.access_message, font=self.font, bg="#f8fafc", fg="#1e3a8a",
                 anchor="w", justify="left", wraplength=630).pack(fill="x", padx=22, pady=10)

    def _build_access(self, frame):
        self.visitor_status = tk.StringVar()
        tk.Label(frame, textvariable=self.visitor_status, font=self.font, bg="#f8fafc", fg="#0f172a",
                 anchor="w").pack(fill="x", padx=12, pady=(10, 6))
        self._label(frame, "최근 7일 접속 (브라우저 단위 · 기록 30일 보관 · 최대 1,000개)", anchor="w").pack(fill="x", padx=12)
        self.visitors = self._tree(frame, ("nickname", "id", "ip", "browser", "seen", "state", "failures"),
            ("닉네임", "접속 ID", "IP", "브라우저", "최근 시각", "상태", "실패"), (90, 80, 95, 115, 100, 145, 45), 4)
        actions = tk.Frame(frame, bg="#f8fafc")
        actions.pack(fill="x", padx=12, pady=5)
        self._operation(actions, "선택 브라우저 차단", self._ban_visitor).pack(side="left", padx=(0, 6))
        self._operation(actions, "선택 IP 차단", self._ban_ip).pack(side="left")
        self._label(frame, "차단 목록 (웹서비스를 꺼도 보존)", anchor="w").pack(fill="x", padx=12, pady=(6, 0))
        self.bans = self._tree(frame, ("kind", "value", "at"),
                               ("차단 종류", "대상", "차단 시각"), (85, 310, 160), 2)
        self._operation(frame, "선택 차단 해제", self._unban).pack(anchor="w", padx=12, pady=5)
        self.failure_status = tk.StringVar()
        tk.Label(frame, textvariable=self.failure_status, font=self.font, bg="#f8fafc", fg="#475569",
                 anchor="w", wraplength=650).pack(fill="x", padx=12)
        self._label(frame, "브라우저 차단은 쿠키를 지우면 우회할 수 있습니다. IP 차단은 같은 IP의 다른 사람도 차단합니다.\n"
                    "지금은 같은 PC에서 확인하므로 IP가 모두 127.0.0.1일 수 있습니다.",
                    anchor="w", justify="left", wraplength=650).pack(fill="x", padx=12, pady=(4, 6))
        tk.Label(frame, textvariable=self.access_message, font=self.font, bg="#f8fafc", fg="#1e3a8a",
                 anchor="w", justify="left", wraplength=650).pack(fill="x", padx=12, pady=(0, 8))
        self.visitor_rows, self.ban_rows = {}, {}
        self.access_render_signature = None

    def _tree(self, parent, columns, headings, widths, height):
        area = tk.Frame(parent, bg="#f8fafc")
        area.pack(fill="both", expand=True, padx=12, pady=3)
        tree = ttk.Treeview(area, columns=columns, show="headings", height=height, selectmode="browse")
        for column, heading, width in zip(columns, headings, widths):
            tree.heading(column, text=heading)
            tree.column(column, width=width, minwidth=40, stretch=column in {"browser", "state", "value"})
        scroll = ttk.Scrollbar(area, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=horizontal.set)
        horizontal.pack(side="bottom", fill="x")
        tree.pack(fill="both", expand=True)
        return tree

    def _save_pin(self):
        value = self.pin.get()
        try:
            validate_pin(value)
        except ValueError as exc:
            self.access_message.set(str(exc))
            self.pin_entry.focus_set()
            return
        self._submit_access("pin", value)

    def _submit_access(self, operation, value=""):
        if self.pending_job:
            return
        try:
            self.pending_job = self.manager.change_access(operation, value)
            self.pending_operation = operation
            self.access_message.set("웹 접속 설정을 저장합니다…")
            for button in self.operation_buttons:
                button.configure(state="disabled")
        except Exception as exc:
            self.access_message.set(str(exc))

    def _save_guest_pin(self):
        value = self.guest_pin.get()
        try:
            validate_pin(value)
        except ValueError as exc:
            self.access_message.set(str(exc))
            self.guest_pin_entry.focus_set()
            return
        self._submit_access("guest_pin", value)

    def _selected_visitor(self):
        selected = self.visitors.selection()
        return self.visitor_rows.get(selected[0]) if selected else None

    def _ban_visitor(self):
        row = self._selected_visitor()
        if row:
            self._submit_access("ban_visitor", row["id"])
        else:
            self.access_message.set("차단할 접속자를 목록에서 선택해주세요.")

    def _ban_ip(self):
        row = self._selected_visitor()
        if row is None:
            self.access_message.set("차단할 접속자를 목록에서 선택해주세요.")
            return
        if self.messagebox("askyesno", "웹 IP 차단", f"{row['ip']}에서 들어오는 모든 접속을 차단할까요?\n"
                           "같은 IP를 쓰는 다른 브라우저도 차단됩니다.\n"
                           "차단 목록에서 언제든 해제할 수 있습니다.", parent=self.window):
            self._submit_access("ban_ip", row["ip"])

    def _unban(self):
        selected = self.bans.selection()
        row = self.ban_rows.get(selected[0]) if selected else None
        if row:
            self._submit_access("unban_" + row["kind"], row["value"])
        else:
            self.access_message.set("해제할 차단 항목을 선택해주세요.")

    @staticmethod
    def _seen(value):
        try:
            return datetime.fromtimestamp(value).strftime("%m/%d %H:%M:%S")
        except (OSError, OverflowError, ValueError):
            return "—"

    def _refresh_access(self):
        data = self.manager.access_status()
        pins = self.manager.desktop_pin_status()
        for role, variable in (("member", self.current_pin), ("guest", self.current_guest_pin)):
            item = pins[role]
            value = item["value"] if item["state"] == "ready" else {
                "unset": "미설정", "checking": "확인 중…",
                "legacy": "이전 설정 · 같은 비밀번호를 한 번 저장하면 표시됩니다",
                "unavailable": "읽기 실패 · 같은 비밀번호를 다시 저장해주세요",
            }[item["state"]]
            variable.set("현재 비밀번호: " + value)
        self.pin_status.set("접속 설정 읽기 오류 · 진단 로그 확인" if data["settings_error"] else
                            "일반 비밀번호 설정됨" if data.get("member_configured", data["configured"]) else "일반 비밀번호 미설정")
        self.guest_pin_status.set("게스트 입장 허용 · 전용 비밀번호 설정됨" if data.get("guest_configured") else
                                  "게스트 입장 중지 · 비밀번호를 저장하면 허용됩니다")
        if self.pending_job:
            for button in self.operation_buttons:
                button.configure(state="disabled")
            result = self.manager.access_result(self.pending_job)
            if result:
                self.pending_job = None
                self.access_message.set(result["message"])
                if result["ok"] and self.pending_operation == "pin":
                    self.pin.set("")
                if result["ok"] and self.pending_operation in {"guest_pin", "guest_disable"}:
                    self.guest_pin.set("")
                self.pending_operation = None
                for button in self.operation_buttons:
                    button.configure(state="normal")
        online = sum(row["state"] == "조회 중" for row in data["visitors"]) if data["live"] else 0
        self.visitor_status.set(f"현재 조회 중 {online}개 브라우저 · " +
            ("약 5초 간격 갱신, 60초 동안 요청이 없으면 최근 접속으로 표시" if data["live"] else "서버 꺼짐 · 마지막 접속 기록"))
        failures = data["failures"]
        self.failure_status.set(f"최근 7일 실패 기록 표시 {len(failures)}건" +
            (f" · 마지막 {self._seen(failures[-1]['at'])} / {failures[-1]['ip']}" if failures else "") +
            " · 같은 IP에서 5분 내 5회 틀리면 잠시 입장을 제한합니다.")
        signature = repr((data["visitors"], data["bans"]))
        if signature == self.access_render_signature:
            return
        self.access_render_signature = signature
        # Update existing rows, retaining selection and scroll position.
        new_rows = {row["id"]: row for row in data["visitors"]}
        for key in set(self.visitor_rows) - set(new_rows):
            self.visitors.delete(key)
        for key, row in sorted(new_rows.items(), key=lambda item: item[1]["last_seen"], reverse=True):
            kind = {"guest": " · 게스트", "member": " · 일반"}.get(row.get("role"), "")
            values = (row.get("nickname", "guest"), key[:8], row["ip"], row["browser"],
                      self._seen(row["last_seen"]), row["state"] + kind, row["failures"])
            if self.visitors.exists(key):
                self.visitors.item(key, values=values)
            else:
                self.visitors.insert("", "end", iid=key, values=values)
        self.visitor_rows = new_rows
        new_bans = {row["kind"] + ":" + row["value"]: row for row in data["bans"]}
        for key in set(self.ban_rows) - set(new_bans):
            self.bans.delete(key)
        for key, row in new_bans.items():
            try:
                banned_at = datetime.fromisoformat(row["at"]).astimezone().strftime("%m/%d %H:%M:%S")
            except ValueError:
                banned_at = "—"
            values = ("브라우저" if row["kind"] == "visitor" else "IP", row["value"], banned_at)
            if self.bans.exists(key):
                self.bans.item(key, values=values)
            else:
                self.bans.insert("", "end", iid=key, values=values)
        self.ban_rows = new_bans

    def _build_ddns(self, frame):
        area = tk.Frame(frame, bg="#f8fafc")
        area.pack(fill="both", expand=True)
        canvas = tk.Canvas(area, bg="#f8fafc", highlightthickness=0)
        scroll = tk.Scrollbar(area, command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(fill="both", expand=True)
        frame = tk.Frame(canvas, bg="#f8fafc")
        pane = canvas.create_window((0, 0), window=frame, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(pane, width=event.width))
        frame.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        data = self.manager.ddns_status()
        tk.Label(frame, text="무료 주소를 한 번 등록하면 IP 변경은 자동으로 처리합니다.", anchor="w",
                 font=("맑은 고딕", 11, "bold"), bg="#f8fafc", fg="#1e3a8a").pack(fill="x", padx=18, pady=(16, 8))
        self._label(frame, "1. 홈페이지 열기 → 로그인 → 원하는 주소 이름 등록\n"
                    "2. 등록한 주소 이름과 홈페이지의 Token을 아래에 복사\n"
                    "3. 설정 저장 → 웹서비스·외부 공개를 켜고 송출권한에 따라 자동 준비합니다.\n"
                    "공동 관리자는 같은 주소·Token만 입력하세요. 비밀번호·인증서·약관 동의 기록은 인계받습니다.",
                    anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=6)
        self._button(frame, "DuckDNS 홈페이지 열기 (무료)", self._open_ddns_registration).pack(anchor="w", padx=18, pady=6)
        inputs = tk.Frame(frame, bg="#f8fafc")
        inputs.pack(fill="x", padx=18, pady=8)
        inputs.columnconfigure(1, weight=1)
        self.ddns_domain = tk.StringVar(value=data["domain"])
        self.ddns_token = tk.StringVar(value="")
        for row, (label, variable, masked) in enumerate((("주소 이름", self.ddns_domain, False), ("Token", self.ddns_token, True))):
            self._label(inputs, label, anchor="w").grid(row=row, column=0, sticky="w", padx=(0, 16), pady=6)
            tk.Entry(inputs, textvariable=variable, show="●" if masked else "", font=self.font).grid(row=row, column=1, sticky="ew", pady=6, ipady=4)
        self.ddns_token_hint = tk.StringVar()
        tk.Label(frame, textvariable=self.ddns_token_hint, font=self.font, bg="#f8fafc", fg="#475569",
                 anchor="w", wraplength=660).pack(fill="x", padx=18)
        self.ddns_enabled = tk.BooleanVar(value=True)
        self.ddns_enable_check = tk.Checkbutton(frame, text="무료 주소 자동 갱신", variable=self.ddns_enabled,
            bg="#f8fafc", font=self.font, activebackground="#f8fafc")
        self.ddns_enable_check.pack(anchor="w", padx=18, pady=8)
        actions = tk.Frame(frame, bg="#f8fafc")
        actions.pack(fill="x", padx=18, pady=4)
        self.ddns_save_button = self._button(actions, "설정 저장", self._save_ddns)
        self.ddns_update_button = self._button(actions, "지금 갱신·확인", self._update_ddns)
        self.ddns_copy_button = self._button(actions, "준비한 주소 복사", self._copy_ddns)
        for button in (self.ddns_save_button, self.ddns_update_button, self.ddns_copy_button):
            button.pack(side="left", padx=(0, 6), ipady=3)
        self.ddns_state = tk.StringVar()
        self.ddns_details = tk.StringVar()
        self.ddns_feedback = tk.StringVar(value="")
        tk.Label(frame, textvariable=self.ddns_state, font=("맑은 고딕", 10, "bold"),
                 bg="#f8fafc", fg="#1e3a8a", anchor="w", wraplength=660).pack(fill="x", padx=18, pady=(12, 6))
        tk.Label(frame, textvariable=self.ddns_details, font=self.font, bg="#f8fafc", fg="#0f172a",
                 anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=4)
        tk.Label(frame, textvariable=self.ddns_feedback, font=self.font, bg="#f8fafc", fg="#b45309",
                 anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=6)
        self._label(frame, "지금은 주소를 준비하는 단계입니다. HTTPS·포트 개방 후 외부에서 접속할 수 있습니다.\n"
                    "Token은 현재 Windows 계정에 암호화해 저장합니다. 다른 PC에서는 다시 입력하세요.",
                    anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=(8, 12))
        self._refresh_ddns()
        def bind_scroll(widget):
            widget.bind("<MouseWheel>", lambda event: canvas.yview_scroll(-int(event.delta / 120), "units"), add="+")
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(frame)

    def _open_ddns_registration(self):
        def launch():
            try:
                webbrowser.open("https://www.duckdns.org/")
            except Exception as exc:
                self.manager._log("ddns_registration_browser_failed", error_type=type(exc).__name__)
        threading.Thread(target=launch, name="ddns-registration", daemon=True).start()

    def _save_ddns(self):
        if self.pending_ddns_job:
            return
        try:
            self.pending_ddns_job = self.manager.configure_ddns(self.ddns_enabled.get(), self.ddns_domain.get(), self.ddns_token.get())
            self.pending_ddns_operation = "save"
            self.ddns_feedback.set("설정을 저장합니다…")
        except ValueError as exc:
            self.ddns_feedback.set(str(exc))
        self._refresh_ddns()

    def _update_ddns(self):
        if self.pending_ddns_job:
            return
        try:
            self.pending_ddns_job = self.manager.update_ddns()
            self.pending_ddns_operation = "update"
            self.ddns_feedback.set("공인 IP와 무료 주소를 확인합니다…")
        except ValueError as exc:
            self.ddns_feedback.set(str(exc))
        self._refresh_ddns()

    def _copy_ddns(self):
        domain = self.manager.ddns_status()["domain"]
        if domain:
            self._copy(domain)

    @staticmethod
    def _local_timestamp(value):
        try:
            return datetime.fromisoformat(value).astimezone().strftime("%m/%d %H:%M:%S") if value else "—"
        except (TypeError, ValueError):
            return "—"

    def _refresh_ddns(self):
        data = self.manager.ddns_status()
        if self.pending_ddns_job:
            result = self.manager.ddns_result(self.pending_ddns_job)
            if result:
                if result["ok"] and self.pending_ddns_operation == "save":
                    self.ddns_token.set("")
                self.ddns_feedback.set(result["message"])
                self.pending_ddns_job = self.pending_ddns_operation = None
        waiting = bool(self.pending_ddns_job)
        self.ddns_save_button.configure(state="disabled" if waiting or data["busy"] else "normal")
        self.ddns_update_button.configure(state="normal" if not waiting and not data["busy"] and data["configured"] and data["enabled"] else "disabled")
        self.ddns_copy_button.configure(state="normal" if data["domain"] else "disabled")
        self.ddns_token_hint.set("Token 저장됨 · 변경할 때만 입력하세요. 비워두면 저장된 Token을 유지합니다." if data["has_token"] else
                                "Token은 홈페이지 상단에서 복사하세요. Discord Bot Token과 다른 값입니다.")
        self.ddns_state.set(data["message"])
        self.ddns_details.set(f"준비한 주소: {data['domain'] or '미설정'}\n"
            f"공인 IPv4: {data['current_ip'] or '미확인'} · {data['dns_message']}\n"
            f"최근 확인: {self._local_timestamp(data['last_checked'])} · 최근 갱신: {self._local_timestamp(data['last_updated'])}")

    def _build_https(self, frame):
        data = self.manager.https_status()
        tk.Label(frame, text="무료 인증서로 HTTPS 연결 준비", font=("맑은 고딕", 11, "bold"),
                 bg="#f8fafc", fg="#1e3a8a", anchor="w").pack(fill="x", padx=18, pady=(16, 8))
        self._label(frame, "1. 무료 주소에 저장한 주소와 Token을 그대로 사용합니다.\n"
                    "2. 공동 관리자는 기존 인증서·약관 동의 기록을 자동으로 인계받습니다.\n"
                    "3. 인계 시 로컬 HTTPS도 자동 확인합니다. 아래 버튼은 최초 설정·수동 점검용입니다.",
                    anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=6)
        self.https_agreed = tk.BooleanVar(value=True)
        tk.Checkbutton(frame, text="Let’s Encrypt 무료 인증기관 이용약관에 동의합니다", variable=self.https_agreed,
                       font=self.font, bg="#f8fafc", activebackground="#f8fafc").pack(anchor="w", padx=18, pady=(8, 3))
        self._button(frame, "인증기관 약관 보기", self._open_https_terms).pack(anchor="w", padx=18, pady=3)
        ports = tk.Frame(frame, bg="#f8fafc")
        ports.pack(fill="x", padx=18, pady=8)
        self._label(ports, "HTTPS 포트", anchor="w").pack(side="left", padx=(0, 12))
        self.https_port = tk.StringVar(value=str(data["port"]))
        tk.Entry(ports, textvariable=self.https_port, width=8, font=self.font).pack(side="left", padx=(0, 12))
        self._label(ports, "기본값 8766 유지 권장 · 기존 로컬 웹 포트와 별도", anchor="w").pack(side="left")
        actions = tk.Frame(frame, bg="#f8fafc")
        actions.pack(fill="x", padx=18, pady=6)
        self.https_issue_button = self._button(actions, "무료 인증서 발급·확인", lambda: self._request_https("issue"))
        self.https_probe_button = self._button(actions, "로컬 HTTPS 확인", lambda: self._request_https("probe"))
        self.https_issue_button.pack(side="left", padx=(0, 8), ipady=3)
        self.https_probe_button.pack(side="left", ipady=3)
        self.https_state, self.https_details, self.https_feedback = (tk.StringVar() for _ in range(3))
        tk.Label(frame, textvariable=self.https_state, font=("맑은 고딕", 10, "bold"),
                 bg="#f8fafc", fg="#1e3a8a", anchor="w", wraplength=660).pack(fill="x", padx=18, pady=(12, 4))
        tk.Label(frame, textvariable=self.https_details, font=self.font, bg="#f8fafc", fg="#0f172a",
                 anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=4)
        tk.Label(frame, textvariable=self.https_feedback, font=self.font, bg="#f8fafc", fg="#b45309",
                 anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=6)
        self._label(frame, "인증서는 만료 전에 자동 갱신합니다. 유효한 인증서는 다시 발급하지 않습니다.\n"
                    "로컬 HTTPS 확인 후 '외부 공개' 탭에서 공유기·방화벽 연결을 시작하세요.",
                    anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=8)
        self._refresh_https()

    def _open_https_terms(self):
        def launch():
            try:
                webbrowser.open("https://letsencrypt.org/repository/")
            except Exception as exc:
                self.manager._log("https_terms_browser_failed", error_type=type(exc).__name__)
        threading.Thread(target=launch, name="https-terms", daemon=True).start()

    def _request_https(self, operation):
        if self.pending_https_job:
            return
        try:
            port = int(self.https_port.get())
            self.pending_https_job = self.manager.request_https(operation, agreed=self.https_agreed.get(), port=port)
            self.https_feedback.set("HTTPS 작업을 시작합니다…")
        except ValueError as exc:
            self.https_feedback.set(str(exc) if str(exc) and not str(exc).startswith("invalid literal") else "HTTPS 포트를 숫자로 입력해주세요.")
        self._refresh_https()

    def _refresh_https(self):
        data = self.manager.https_status()
        if self.pending_https_job:
            result = self.manager.https_result(self.pending_https_job)
            if result:
                self.https_feedback.set(result["message"])
                self.pending_https_job = None
        busy = bool(self.pending_https_job) or data["busy"]
        active = self.manager.status()["state"] == "running"
        self.https_issue_button.configure(state="normal" if active and not busy and self.manager.ddns_status()["configured"] else "disabled")
        self.https_probe_button.configure(state="normal" if not busy and active and data["https_state"] == "running" else "disabled")
        certificate = data["certificate"]
        self.https_state.set(data["message"])
        self.https_details.set(f"인증서 주소: {certificate.get('domain') or self.manager.ddns_status()['domain'] or '무료 주소 미설정'}\n"
            f"만료: {self._local_timestamp(certificate.get('expires_at'))} · 갱신 예정: {self._local_timestamp(certificate.get('renew_at'))}\n"
            f"HTTPS: {data['https_message'] or '설정 전'}\n{data['probe_message']}")

    def _build_public(self, frame):
        data = self.manager.public_status()
        tk.Label(frame, text="IPv4 직접 공개 · 별도 웹서버 설치 없이 연결", font=("맑은 고딕", 11, "bold"),
                 bg="#f8fafc", fg="#1e3a8a", anchor="w").pack(fill="x", padx=18, pady=(16, 8))
        self._label(frame, "무료 주소와 HTTPS가 준비되면 아래 체크만 켜세요.\n"
                    "현재 PC의 HTTPS 포트만 방화벽에 허용하고 공유기 UPnP로 외부 포트를 연결합니다.\n"
                    "기존 공유기 규칙은 유지하며, 공개 해제·프로그램 종료 시 자체 규칙을 정리합니다.",
                    anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=6)
        self.public_enabled = tk.BooleanVar(value=data["enabled"])
        tk.Checkbutton(frame, text="웹서비스 외부 공개", variable=self.public_enabled, command=self._toggle_public,
                       font=("맑은 고딕", 11, "bold"), bg="#f8fafc", fg="#1e3a8a",
                       activebackground="#f8fafc", selectcolor="white").pack(anchor="w", padx=18, pady=10)
        self.public_state, self.public_details, self.public_address = (tk.StringVar() for _ in range(3))
        tk.Label(frame, textvariable=self.public_state, font=("맑은 고딕", 10, "bold"),
                 bg="#f8fafc", fg="#1e3a8a", anchor="w", wraplength=660).pack(fill="x", padx=18, pady=6)
        tk.Label(frame, textvariable=self.public_details, font=self.font, bg="#f8fafc", fg="#0f172a",
                 anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=6)
        self._label(frame, "공개 주소", anchor="w").pack(fill="x", padx=18, pady=(12, 3))
        tk.Entry(frame, textvariable=self.public_address, state="readonly", font=self.font,
                 readonlybackground="white", fg="#1e3a8a").pack(fill="x", padx=18, ipady=5)
        actions = tk.Frame(frame, bg="#f8fafc")
        actions.pack(fill="x", padx=18, pady=10)
        self.public_copy_button = self._button(actions, "주소 복사", lambda: self._copy(self.manager.public_status()["address"]))
        self.public_open_button = self._button(actions, "브라우저에서 열기", self._open_public)
        self.public_retry_button = self._button(actions, "웹 강제 승계/재시도", self._retry_public)
        for button in (self.public_copy_button, self.public_open_button, self.public_retry_button):
            button.pack(side="left", padx=(0, 8), ipady=3)
        self.public_stop_button = self._button(actions, "웹 긴급 중지", self._emergency_stop)
        self.public_stop_button.pack(side="left", ipady=3)
        self._label(frame, "기본은 송출권한을 따라 공개합니다. 분리 운영은 기존 웹 유지 · 웹 승계 시 기존 공개 중단부터 확인합니다.",
                    anchor="w", wraplength=660).pack(fill="x", padx=18, pady=4)
        self._label(frame, "'공개 준비 완료'는 외부 접속 성공을 뜻하지 않습니다.\n"
                    "휴대폰 Wi-Fi를 끄고 모바일 데이터로 공개 주소에 접속해 비밀번호 입장을 확인해주세요.\n"
                    "같은 집에서 공개 주소가 안 열려도 공유기의 내부 접속 지원 차이일 수 있습니다.",
                    anchor="w", justify="left", wraplength=660).pack(fill="x", padx=18, pady=6)
        self._label(frame, "방화벽 변경에는 Windows 관리자 권한이 필요합니다. 설정·스케줄은 보존합니다.",
                    anchor="w", wraplength=660).pack(fill="x", padx=18, pady=6)
        self._refresh_public()

    def _toggle_public(self):
        self.manager.configure_public(self.public_enabled.get())
        self._refresh_public()

    def _retry_public(self):
        try:
            self.manager.retry_public()
        except Exception as exc:
            self.messagebox("showerror", "웹 공개 재시도", str(exc), parent=self.window)
        self._refresh_public()

    def _emergency_stop(self):
        try:
            self.manager.emergency_stop()
        except Exception as exc:
            self.messagebox("showerror", "웹 긴급 중지", str(exc), parent=self.window)
        self._refresh_public()

    def _open_public(self):
        address = self.manager.public_status()["address"]
        if address:
            def launch():
                try:
                    webbrowser.open(address)
                except Exception as exc:
                    self.manager._log("public_browser_failed", error_type=type(exc).__name__)
            threading.Thread(target=launch, name="web-public-browser", daemon=True).start()

    def _refresh_public(self):
        data = self.manager.public_status()
        self.public_enabled.set(data["enabled"])
        self.public_state.set(data["message"])
        self.public_details.set(f"Windows 방화벽: {data['firewall']}\n공유기 포트: {data['mapping']}\n"
                                f"웹서버: {data['https']}\n접속 확인: {data['external']}")
        self.public_address.set(data["address"])
        for button in (self.public_copy_button, self.public_open_button):
            button.configure(state="normal" if data["address"] and data["state"] == "prepared" else "disabled")
        self.public_retry_button.configure(state="normal" if self.manager.authority_retry is not None else "disabled")

    def _build_network(self, frame):
        tk.Label(frame, text="외부 공개를 준비하기 위한 자동 진단", anchor="w", bg="#f8fafc", fg="#0f172a",
                 font=("맑은 고딕", 11, "bold")).pack(fill="x", padx=16, pady=(16, 8))
        self._label(frame, "추가 입력 없이 PC 주소·공인 IP·공유기 응답을 확인합니다.\n"
                    "여기는 조건 확인용입니다. 실제 공개 URL은 '외부 공개' 탭에서 준비합니다.",
                    anchor="w", justify="left", wraplength=670).pack(fill="x", padx=16)
        actions = tk.Frame(frame, bg="#f8fafc")
        actions.pack(fill="x", padx=16, pady=10)
        self.network_button = self._button(actions, "다시 진단", self._diagnose_network)
        self.network_button.pack(side="left", padx=(0, 8))
        self._button(actions, "진단 결과 복사", lambda: self._copy(self.manager.network_report())).pack(side="left")
        self.network_text = tk.StringVar()
        tk.Label(frame, textvariable=self.network_text, font=self.font, bg="#f8fafc", fg="#1e3a8a",
                 anchor="w", wraplength=670).pack(fill="x", padx=16, pady=4)
        area = tk.Frame(frame, bg="#f8fafc")
        area.pack(fill="both", expand=True, padx=14, pady=8)
        self.network_results = tk.Text(area, state="disabled", font=self.font, wrap="word", height=14,
                                      bg="white", fg="#0f172a", padx=10, pady=8)
        scroll = tk.Scrollbar(area, command=self.network_results.yview)
        self.network_results.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.network_results.pack(fill="both", expand=True)
        self.network_results.tag_configure("heading", foreground="#1e3a8a", font=("맑은 고딕", 9, "bold"))
        self.network_results.tag_configure("ok", foreground="#15803d")
        self.network_results.tag_configure("warning", foreground="#b45309")
        self.network_results.tag_configure("info", foreground="#475569")
        self._label(frame, "진단 중 화면과 봇은 계속 사용할 수 있습니다. 공인 IP는 ipify에 HTTPS로 조회합니다.\n"
                    "방화벽·공유기 설정은 바꾸지 않습니다. 결과와 로그는 웹 전용 폴더에 기록합니다.",
                    anchor="w", justify="left", wraplength=670).pack(fill="x", padx=16, pady=(0, 12))
        self.last_network = None
        self._refresh_network()

    def _diagnose_network(self):
        self.manager.diagnose_network()
        self._refresh_network()

    def _refresh_network(self):
        state = self.manager.network_status()
        running = state["state"] == "running"
        self.network_button.configure(state="disabled" if running else "normal")
        self.network_text.set((f"확인 중 {state['progress']} / 7 · " if running else "") + state["message"])
        signature = repr((state["checks"], state["checked_at"]))
        if signature == self.last_network:
            return
        self.last_network = signature
        position = self.network_results.yview()[0]
        self.network_results.configure(state="normal")
        self.network_results.delete("1.0", "end")
        if state["checked_at"]:
            try:
                checked = datetime.fromisoformat(state["checked_at"]).astimezone().strftime("%m/%d %H:%M:%S")
            except (TypeError, ValueError):
                checked = "—"
            self.network_results.insert("end", f"최근 진단: {checked}\n\n", "info")
        for row in state["checks"]:
            self.network_results.insert("end", row["label"] + "\n", "heading")
            self.network_results.insert("end", row["value"] + "\n", row["health"])
            self.network_results.insert("end", row["detail"] + "\n\n", "info")
        if not state["checks"]:
            self.network_results.insert("end", "PC·공유기 정보를 확인할 준비가 되었습니다.\n")
        self.network_results.configure(state="disabled")
        self.network_results.yview_moveto(position)

    def _build_logs(self, frame):
        actions = tk.Frame(frame, bg="#f8fafc")
        actions.pack(fill="x", padx=12, pady=10)
        self._button(actions, "새로고침", self._refresh_logs).pack(side="left", padx=(0, 8))
        self._button(actions, "진단 로그 복사", self._copy_logs).pack(side="left")
        self._label(frame, "시작·연결·스케줄 변경·갱신 지연·종료 과정을 기록합니다.", anchor="w").pack(fill="x", padx=14)
        area = tk.Frame(frame, bg="#f8fafc")
        area.pack(fill="both", expand=True, padx=12, pady=10)
        self.logs = tk.Text(area, state="disabled", font=("맑은 고딕", 8), wrap="word", height=12)
        scroll = tk.Scrollbar(area, command=self.logs.yview)
        self.logs.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.logs.pack(fill="both", expand=True)
        self.last_logs = ""

    def _select(self, name):
        self.selected = name
        for tab, frame in self.frames.items():
            frame.pack_forget()
            active = tab == name
            self.buttons[tab].configure(bg="#1e3a8a" if active else "#e2e8f0",
                fg="white" if active else "#334155", relief="sunken" if active else "raised", bd=2)
        self.frames[name].pack(fill="both", expand=True, padx=2, pady=(0, 2))
        if name == "진단 로그":
            self._refresh_logs()
        elif name == "네트워크":
            if self.manager.network_status()["state"] == "idle":
                self._diagnose_network()
            else:
                self._refresh_network()

    def _toggle(self):
        self._retry(False, enabled=self.enabled.get())

    def _retry(self, alternate, enabled=True):
        try:
            self.manager.set_enabled(enabled, alternate_port=alternate)
        except Exception as exc:
            self.manager._log("settings_ui_failed", error_type=type(exc).__name__)
            self.messagebox("showerror", "웹서비스 설정", "웹 설정을 저장하지 못했습니다. 진단 로그를 확인해주세요.", parent=self.window)
        self.enabled.set(self.manager.status()["enabled"])
        if enabled and not self.manager.access_status()["configured"]:
            self._select("비밀번호")
            self.pin_entry.focus_set()

    def _copy(self, text):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            self.messagebox("showwarning", "웹서비스", "클립보드를 사용할 수 없습니다.", parent=self.window)

    def _copy_address(self):
        address = self.manager.status()["address"]
        if address:
            self._copy(address)

    def _open_browser(self):
        address = self.manager.status()["address"]
        if not address:
            return
        def launch():
            try:
                opened = webbrowser.open(address)
                self.manager._log("browser_open_requested", accepted=bool(opened))
            except Exception as exc:
                self.manager._log("browser_open_failed", error_type=type(exc).__name__)
        threading.Thread(target=launch, name="web-browser-open", daemon=True).start()

    def _refresh_logs(self):
        text = self.manager.diagnostics()
        if text != self.last_logs:
            self.last_logs = text
            self.logs.configure(state="normal")
            self.logs.delete("1.0", "end")
            self.logs.insert("1.0", text)
            self.logs.see("end")
            self.logs.configure(state="disabled")

    def _copy_logs(self):
        self._copy(self.manager.diagnostics())

    def _refresh(self):
        self.after_id = None
        if self.window is None:
            return
        try:
            state = self.manager.status()
            names = dict(stopped="○ 꺼짐", starting="● 시작 중", running="● 실행 중",
                         stopping="● 종료 중", updating="● 웹 업데이트 중", error="● 오류")
            self.state_text.set(names.get(state["state"], "○ 확인 중"))
            self.state_label.configure(fg="#15803d" if state["state"] == "running" else
                                       "#b91c1c" if state["state"] == "error" else "#475569")
            self.enabled.set(state["enabled"])
            self.scope_text.set("외부 공개 준비됨 · 비밀번호 필요" if self.manager.public_status()["state"] == "prepared" else "같은 PC만 접속 가능")
            self.address.set(state["address"])
            self.count.set(str(state["request_count"]))
            schedule_names = dict(waiting="자료 준비 중", ready="연결됨", stale="갱신 지연 · 마지막 자료")
            self.schedule_text.set("웹서비스 꺼짐" if not state["enabled"] else
                f"{schedule_names.get(state['schedule_state'], '확인 중')} · {state['schedule_count']}개 일정")
            self.detail.set(state["message"] + "\n" + state["authority_message"])
            for button in (self.copy_button, self.open_button):
                button.configure(state="normal" if state["state"] == "running" else "disabled")
            for button in (self.retry_button, self.port_button):
                button.configure(state="normal" if state["state"] == "error" else "disabled")
            if self.selected == "진단 로그":
                self._refresh_logs()
            self._refresh_access()
            self._refresh_ddns()
            self._refresh_https()
            self._refresh_public()
            self._refresh_joint()
            self._refresh_page_update()
            if self.selected == "네트워크":
                self._refresh_network()
            self.after_id = self.root.after(1000, self._refresh)
        except tk.TclError:
            self.close()

    def close(self):
        if self.after_id is not None:
            try:
                self.root.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None
