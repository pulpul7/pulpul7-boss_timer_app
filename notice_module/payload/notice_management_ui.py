"""Local notice controls; intentionally no live collection or audio here."""
from copy import deepcopy
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk
import uuid

from .ui_helpers import center_window
from .notice_management import (
    CATEGORIES, WEEKDAYS, NoticeStore, NoticeError, display_time, event_status,
)


class NoticeManagementWindow:
    def __init__(self, app, data_root: Path):
        self.app = app
        self.server_id = str(getattr(app, "schedule_server_profile_id", "") or "")
        self.server_name = str(getattr(app, "schedule_server_profile_name", "") or self.server_id)
        self.store = NoticeStore(data_root, self.server_id)
        state = self.store.snapshot()
        self.events = {}
        self.rules = deepcopy(state["settings"]["rules"])
        self.next_refresh = time.monotonic() + 30
        parent = getattr(app, "schedule_window", None) or app.root
        self.window = win = tk.Toplevel(app.root)
        win.title(f"알림 관리 · {self.server_name}")
        win.configure(bg="#eff6ff")
        win.geometry("1000x750")
        win.minsize(920, 660)
        win.transient(parent)
        tk.Label(win, text=f"알림 관리  ·  {self.server_name}", bg="#eff6ff", fg="#0f172a",
                 font=("맑은 고딕", 16, "bold"), anchor="w").pack(fill="x", padx=18, pady=(14, 5))
        tk.Label(win, text="이 PC의 해당 서버 설정만 저장합니다. 수집은 각 클라이언트가 독립적으로 수행합니다.",
                 bg="#eff6ff", fg="#475569", anchor="w").pack(fill="x", padx=18)
        tk.Label(win, text="공개 공지 수집 연결됨 · 수집 내용은 별도 탭에서 확인합니다. 기간 분석·자동 알림 생성·TTS는 다음 단계입니다.",
                 bg="#fef3c7", fg="#92400e", anchor="w", padx=8, pady=6).pack(fill="x", padx=18, pady=8)
        tabs = ttk.Notebook(win)
        tabs.pack(fill="both", expand=True, padx=16, pady=4)
        sources = ttk.Frame(tabs, padding=8)
        live = ttk.Frame(tabs, padding=8)
        history = ttk.Frame(tabs, padding=8)
        settings = ttk.Frame(tabs, padding=12)
        tabs.add(sources, text="수집한 공지 / 원문")
        tabs.add(live, text="등록된 알림 / 유효기간")
        tabs.add(history, text="종료·폐기 이력 · 30일")
        tabs.add(settings, text="서버별 수집 / 송출 설정")
        self._sources(sources)
        self.tree = self._event_tree(live)
        self.history_tree = self._event_tree(history, history=True)
        self.detail = self._text(live, 7)
        self.history_detail = self._text(history, 7)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._details(False))
        self.history_tree.bind("<<TreeviewSelect>>", lambda _: self._details(True))
        self.tree.bind("<ButtonRelease-1>", self._toggle_click)
        buttons = ttk.Frame(live)
        buttons.pack(fill="x", pady=8)
        for label, command in (("수동 알림 등록", self._add), ("사용 체크 전환", self._toggle),
                               ("유효기간 수정", self._edit_period), ("선택 알림 폐기", self._discard)):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=(0, 6))
        tk.Label(live, text="기간 미확정은 체크되어 있어도 실행 보류입니다. 사용 체크 해제와 이력 삭제는 별개입니다.",
                 anchor="w", fg="#475569").pack(fill="x")
        buttons = ttk.Frame(history)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="선택 이력 삭제", command=lambda: self._delete_history(False)).pack(side="left")
        ttk.Button(buttons, text="전체 이력 삭제", command=lambda: self._delete_history(True)).pack(side="left", padx=8)
        ttk.Label(history, text="종료·폐기 시점부터 30일 뒤 자동 삭제됩니다. 수동 삭제한 상세 이력은 복구할 수 없습니다.").pack(anchor="w")
        self._settings(settings, state["settings"])
        footer = ttk.Frame(win, padding=12)
        footer.pack(fill="x")
        ttk.Button(footer, text="목록 새로고침", command=self._refresh).pack(side="left")
        ttk.Button(footer, text="지금 공지 수집", command=self._collect_now).pack(side="left", padx=8)
        ttk.Button(footer, text="닫기", command=win.destroy).pack(side="right")
        self.status = tk.StringVar(win)
        tk.Label(win, textvariable=self.status, anchor="w", bg="#eff6ff", fg="#475569").pack(fill="x", padx=18, pady=(0, 10))
        self._refresh()
        center_window(win, parent)
        win.after(1500, self._watch_server)

    def _guard(self):
        if str(getattr(self.app, "schedule_server_profile_id", "") or "") == self.server_id:
            return True
        self.app._show_centered_messagebox("showwarning", "서버 변경",
            "현재 서버가 변경되었습니다. 이 창에서는 저장하지 않습니다.\n알림 관리 버튼을 눌러 현재 서버의 창을 다시 열어 주세요.", parent=self.window)
        return False

    def _watch_server(self):
        if not self.window.winfo_exists():
            return
        if str(getattr(self.app, "schedule_server_profile_id", "") or "") != self.server_id:
            self.status.set("서버가 변경되어 이 창의 저장을 차단했습니다. 알림 관리를 다시 열어 주세요.")
        elif time.monotonic() >= self.next_refresh:
            self.next_refresh = time.monotonic() + 30
            self._refresh()
        self.window.after(1500, self._watch_server)

    def _run(self, operation):
        if not self._guard():
            return False
        try:
            operation()
            self._refresh()
            return True
        except Exception as exc:
            self.app._show_centered_messagebox("showerror", "알림 관리", str(exc), parent=self.window)
            return False

    def _event_tree(self, parent, history=False):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        columns = ("enabled", "title", "from", "until", "state")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=10, selectmode="extended" if history else "browse")
        for key, label, width in (("enabled", "사용", 50), ("title", "알림 제목", 270),
                                  ("from", "유효 시작 (KST)", 165), ("until", "유효 종료 (KST)", 165),
                                  ("state", "현재 상태", 235)):
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=50)
        tree.tag_configure("held", foreground="#b45309")
        tree.tag_configure("disabled", foreground="#64748b")
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)
        return tree

    @staticmethod
    def _text(parent, height):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=8)
        text = tk.Text(frame, height=height, wrap="word", bg="#ffffff", fg="#0f172a", state="disabled", padx=8, pady=8)
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

    def _refresh(self):
        if not self._guard():
            return
        try:
            state = self.store.snapshot()
        except Exception as exc:
            self.status.set(f"기록을 읽지 못했습니다. 원본 보존: {exc}")
            return
        self.events = state["events"]
        self.source_state = state
        self._refresh_sources()
        for history, tree in ((False, self.tree), (True, self.history_tree)):
            selected = tree.selection()
            tree.delete(*tree.get_children())
            for item in sorted(self.events.values(), key=lambda e: e.get("retired_at") or e["created_at"], reverse=True):
                if bool(item.get("retired_at")) != history:
                    continue
                status = event_status(item)
                if not history and "유효 ·" in status and not state["settings"]["output_enabled"]:
                    status = "이 클라이언트 송출 꺼짐"
                tags = ("held",) if "미확정" in status else (("disabled",) if not item["enabled"] else ())
                tree.insert("", "end", iid=item["id"], values=("☑" if item["enabled"] else "☐", item["title"],
                            display_time(item["valid_from"]), display_time(item["valid_until"]), status), tags=tags)
            keep = [key for key in selected if tree.exists(key)]
            if keep:
                tree.selection_set(keep)
            self._details(history)
        collection = state.get("collection", {})
        self.status.set(f"{self.server_name} · 알림 {len(self.tree.get_children())}개 / 이력 {len(self.history_tree.get_children())}개 · "
                        f"{collection.get('status', '수집 대기')} · TTS 미연결")

    def _sources(self, parent):
        header = ttk.Frame(parent)
        header.pack(fill="x", pady=(0, 6))
        self.show_removed = tk.BooleanVar(parent, value=False)
        ttk.Checkbutton(header, text="공지 해제 내역 포함 (30일)", variable=self.show_removed,
                        command=self._refresh_sources).pack(side="left")
        self.source_summary = tk.StringVar(parent, value="아직 수집하지 않았습니다.")
        ttk.Label(parent, textvariable=self.source_summary, wraplength=900, justify="left").pack(fill="x", pady=6)
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        self.source_tree = ttk.Treeview(frame, columns=("date", "title", "state"), show="headings", height=8, selectmode="browse")
        for key, label, width in (("date", "작성일 (KST)", 115), ("title", "상단 공지 제목", 530), ("state", "수집 상태", 245)):
            self.source_tree.heading(key, text=label)
            self.source_tree.column(key, width=width, minwidth=90)
        scroll = ttk.Scrollbar(frame, command=self.source_tree.yview)
        self.source_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.source_tree.pack(side="left", fill="both", expand=True)
        self.source_tree.bind("<<TreeviewSelect>>", lambda _: self._source_details())
        self.source_detail = self._text(parent, 10)

    def _refresh_sources(self):
        state = getattr(self, "source_state", {})
        selected = self.source_tree.selection()
        self.source_tree.delete(*self.source_tree.get_children())
        categories = state.get("settings", {}).get("categories", {})
        for key, item in sorted(state.get("articles", {}).items(), key=lambda pair: (not pair[1].get("pinned", False), pair[1].get("rank", 0))):
            if not categories.get(item.get("category"), False) or (not item.get("pinned") and not self.show_removed.get()):
                continue
            status = ("공지 해제" if not item.get("pinned") else "본문 갱신 실패 · 이전 내용 보존" if item.get("body_error")
                      else item.get("review_note") or "본문 확인 · 알림 분석 대기")
            self.source_tree.insert("", "end", iid=key, values=(item.get("published_date") or "미확정", item["title"], status))
        for key in selected:
            if self.source_tree.exists(key):
                self.source_tree.selection_set(key)
        if not self.source_tree.selection() and self.source_tree.get_children():
            self.source_tree.selection_set(self.source_tree.get_children()[0])
        collection = state.get("collection", {})
        message = f"{collection.get('status', '수집 대기')} · 마지막 성공: {display_time(collection.get('last_success'))}\n{collection.get('message', '조회 시간표에 따라 자동 수집하거나 지금 공지 수집을 눌러 주세요.')}"
        self.source_summary.set(message)
        self._source_details()

    def _source_details(self):
        selected = self.source_tree.selection()
        item = getattr(self, "source_state", {}).get("articles", {}).get(selected[0]) if selected else None
        if not item:
            self._set_text(self.source_detail, "계정보호·런처 도움말·운영정책을 제외한 상단 공지만 수집합니다.\n수집한 본문은 알림 이벤트와 별개이며, 유효기간 분석 전에는 안내하지 않습니다.")
            return
        self._set_text(self.source_detail, f"{item['title']}\n작성일: {item.get('published_date') or '미확정'}"
                       f"\n출처: {item['url']}\n최근 본문 확인: {display_time(item.get('last_checked'))}"
                       f"\n본문 변경 감지: {display_time(item.get('changed_at'))} / 판본 {item.get('revision', 0)}"
                       f"\n이미지 {len(item.get('images', []))}개 (다운로드·OCR 하지 않음)"
                       f"\n{item.get('body_error') or item.get('review_note') or ''}\n\n{item.get('body') or '본문을 아직 읽지 못했습니다.'}")

    def _collect_now(self):
        if not self._guard():
            return
        try:
            if not self.store.snapshot()["settings"]["collection_enabled"]:
                raise NoticeError("이 서버의 수집이 꺼져 있습니다. 설정 탭에서 수집을 켜고 저장해 주세요.")
            request = getattr(self.app, "collect_notices_now", None)
            if not callable(request) or not request(self.server_id):
                self.status.set("수집 작업이 이미 실행 중이거나 서버가 변경되었습니다. 잠시 후 확인해 주세요.")
                return
            self.status.set("공지 수집을 요청했습니다. 조회 시간표와 관계없이 이번 한 번 확인합니다.")
            self.window.after(1000, self._refresh)
            self.next_refresh = time.monotonic() + 3
        except Exception as exc:
            self.app._show_centered_messagebox("showwarning", "공지 수집", str(exc), parent=self.window)

    def _details(self, history):
        tree, text = (self.history_tree, self.history_detail) if history else (self.tree, self.detail)
        selected = tree.selection()
        if not selected or selected[0] not in self.events:
            self._set_text(text, "알림을 선택하면 수집 내용, 행사 기간, 안내 문장과 출처를 확인할 수 있습니다.")
            return
        item = self.events[selected[0]]
        lines = [f"{item['title']}  /  {CATEGORIES[item['category']]}",
                 f"알림 유효기간: {display_time(item['valid_from'])} ~ {display_time(item['valid_until'])}",
                 f"행사 자체 기간: {display_time(item['event_from'])} ~ {display_time(item['event_until'])}",
                 f"기간 기준: {'사용자 지정' if item['manual_period'] else '등록/수집 정보'}",
                 f"마지막 안내: {display_time(item['last_delivery'])}",
                 f"종료·폐기: {display_time(item.get('retired_at'))}" if history else "",
                 f"출처: {item['source_url'] or '수동 등록 / 출처 없음'}",
                 f"\n안내 문장:\n{item['tts_text'] or '(없음 · 송출 불가)'}", f"\n수집/참고 내용:\n{item['body'] or '(없음)'}"]
        self._set_text(text, "\n".join(lines))

    def _selected(self):
        selected = self.tree.selection()
        return self.events.get(selected[0]) if selected else None

    def _toggle_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell" and self.tree.identify_column(event.x) == "#1":
            key = self.tree.identify_row(event.y)
            if key:
                self.tree.selection_set(key)
                self._toggle()

    def _toggle(self):
        item = self._selected()
        if item:
            self._run(lambda: self.store.set_enabled(item["id"], not item["enabled"]))

    def _discard(self):
        item = self._selected()
        if item and self._guard() and self.app._show_centered_messagebox("askyesno", "알림 폐기",
            f"{item['title']}\n이 알림을 폐기할까요? 앞으로 안내하지 않고 이력으로 옮깁니다.", parent=self.window, default="no"):
            self._run(lambda: self.store.discard(item["id"]))

    def _delete_history(self, all_items):
        keys = None if all_items else self.history_tree.selection()
        if not self._guard() or (keys is not None and not keys):
            return
        if self.app._show_centered_messagebox("askyesno", "이력 삭제",
            "전체 이력을 삭제할까요?" if all_items else "선택한 이력을 삭제할까요?",
            parent=self.window, default="no"):
            if self._run(lambda: self.store.delete_history(keys)):
                self.status.set("종료·폐기된 상세 이력을 삭제했습니다. 복구할 수 없습니다. 재수집 방지 식별자는 유지합니다.")

    def _form(self, title, fields, save):
        if not self._guard():
            return
        win = tk.Toplevel(self.window)
        win.title(title)
        win.transient(self.window)
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=18)
        frame.pack(fill="both", expand=True)
        variables = {}
        for row, (key, label, value) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
            variable = variables[key] = tk.StringVar(win, value=value)
            ttk.Entry(frame, textvariable=variable, width=54).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Label(frame, text="날짜: YYYY-MM-DD HH:MM (한국시간) · 미확정이면 비워두기\n유효기간이 없으면 저장은 되지만 안내하지 않습니다.").grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=8)
        def submit():
            if self._run(lambda: save({key: var.get().strip() for key, var in variables.items()})):
                win.destroy()
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e", pady=6)
        ttk.Button(buttons, text="저장", command=submit).pack(side="left", padx=6)
        ttk.Button(buttons, text="취소", command=win.destroy).pack(side="left")
        center_window(win, self.window)

    def _edit_period(self):
        item = self._selected()
        if item:
            self._form("알림 유효기간 수정", [("start", "유효 시작", display_time(item["valid_from"]) if item["valid_from"] else ""),
                                               ("end", "유효 종료", display_time(item["valid_until"]) if item["valid_until"] else "")],
                       lambda values: self.store.edit_period(item["id"], values["start"], values["end"]))

    def _add(self):
        self._form("수동 알림 등록", [("title", "알림 제목", ""), ("start", "알림 유효 시작", ""),
                                     ("end", "알림 유효 종료", ""), ("event_start", "행사 자체 시작 (선택)", ""),
                                     ("event_end", "행사 자체 종료 (선택)", ""), ("tts", "안내 문장", ""),
                                     ("body", "참고 내용", "")],
                   lambda values: self.store.register({"id": "manual-" + uuid.uuid4().hex, "title": values["title"],
                       "valid_from": values["start"], "valid_until": values["end"], "event_from": values["event_start"],
                       "event_until": values["event_end"], "tts_text": values["tts"], "body": values["body"]}))

    def _settings(self, frame, values):
        self.collection = tk.BooleanVar(frame, value=values["collection_enabled"])
        self.output = tk.BooleanVar(frame, value=values["output_enabled"])
        ttk.Checkbutton(frame, text="이 서버의 공지를 이 클라이언트에서 수집", variable=self.collection).pack(anchor="w", pady=4)
        ttk.Checkbutton(frame, text="이 클라이언트에서 자동 안내 송출", variable=self.output).pack(anchor="w", pady=4)
        ttk.Label(frame, text="배포 안내: 같은 서버에서는 한 클라이언트만 자동 안내 송출을 켜 주세요.\n현재 중복 접속·송출을 자동으로 차단하거나 조정하지 않습니다.", foreground="#b45309").pack(anchor="w", pady=6)
        category_frame = ttk.LabelFrame(frame, text="수집할 종류", padding=8)
        category_frame.pack(fill="x", pady=6)
        self.category_vars = {}
        for index, (key, label) in enumerate(CATEGORIES.items()):
            var = self.category_vars[key] = tk.BooleanVar(frame, value=values["categories"][key])
            ttk.Checkbutton(category_frame, text=label, variable=var).grid(row=index // 3, column=index % 3, sticky="w", padx=8, pady=3)
        ttk.Label(frame, text="조회 시간표 · 위에서 먼저 일치하는 규칙 적용 / 규칙 밖은 조회 안 함\n이 시간표는 수집용이며, 음성 안내의 일괄 시간 제한이 아닙니다.").pack(anchor="w", pady=6)
        self.rules_tree = ttk.Treeview(frame, columns=("days", "start", "end", "minutes"), show="headings", height=4)
        for key, label in (("days", "요일"), ("start", "조회 시작"), ("end", "조회 종료"), ("minutes", "주기(분)")):
            self.rules_tree.heading(key, text=label)
            self.rules_tree.column(key, width=130)
        self.rules_tree.pack(fill="x")
        self.rules_tree.bind("<<TreeviewSelect>>", self._select_rule)
        editor = ttk.Frame(frame)
        editor.pack(fill="x", pady=6)
        self.rule_vars = {key: tk.StringVar(frame, value=value) for key, value in
                          (("days", "매일"), ("start", "06:50"), ("end", "24:00"), ("minutes", "10"))}
        ttk.Combobox(editor, textvariable=self.rule_vars["days"], values=("매일", *WEEKDAYS), state="readonly", width=8).pack(side="left", padx=2)
        for key in ("start", "end", "minutes"):
            ttk.Entry(editor, textvariable=self.rule_vars[key], width=7).pack(side="left", padx=3)
        for label, command in (("추가", lambda: self._put_rule(False)), ("수정", lambda: self._put_rule(True)),
                               ("삭제", self._remove_rule), ("위로", lambda: self._move_rule(-1)), ("아래로", lambda: self._move_rule(1))):
            ttk.Button(editor, text=label, width=6, command=command).pack(side="left", padx=2)
        ttk.Button(frame, text="이 서버의 수집 / 송출 설정 저장", command=self._save_settings).pack(anchor="e", pady=8)
        self._render_rules()

    def _render_rules(self, select=None):
        self.rules_tree.delete(*self.rules_tree.get_children())
        for i, rule in enumerate(self.rules):
            self.rules_tree.insert("", "end", iid=str(i), values=tuple(rule[k] for k in ("days", "start", "end", "minutes")))
        if select is not None:
            self.rules_tree.selection_set(str(select))

    def _select_rule(self, _event=None):
        selection = self.rules_tree.selection()
        if selection:
            for key, value in self.rules[int(selection[0])].items():
                self.rule_vars[key].set(str(value))

    def _put_rule(self, replace):
        if not self._guard():
            return
        try:
            rule = {key: value.get().strip() for key, value in self.rule_vars.items()}
            rule["minutes"] = int(rule["minutes"])
            from .notice_management import validate_settings
            settings = self._settings_value()
            settings["rules"] = [rule]
            validate_settings(settings)
            selection = self.rules_tree.selection()
            if replace:
                if not selection:
                    return
                index = int(selection[0])
                self.rules[index] = rule
            else:
                self.rules.append(rule)
                index = len(self.rules) - 1
            self._render_rules(index)
        except (ValueError, NoticeError) as exc:
            self.app._show_centered_messagebox("showerror", "조회 규칙", str(exc), parent=self.window)

    def _remove_rule(self):
        selection = self.rules_tree.selection()
        if self._guard() and selection:
            del self.rules[int(selection[0])]
            self._render_rules()

    def _move_rule(self, delta):
        selection = self.rules_tree.selection()
        if self._guard() and selection:
            index = int(selection[0])
            target = index + delta
            if 0 <= target < len(self.rules):
                self.rules[index], self.rules[target] = self.rules[target], self.rules[index]
                self._render_rules(target)

    def _settings_value(self):
        return {"collection_enabled": self.collection.get(), "output_enabled": self.output.get(),
                "categories": {key: value.get() for key, value in self.category_vars.items()}, "rules": deepcopy(self.rules)}

    def _save_settings(self):
        if self._run(lambda: self.store.configure(self._settings_value())):
            self.status.set(f"{self.server_name}의 수집 / 송출 설정을 저장했습니다. 실제 TTS 송출은 아직 연결되지 않았습니다.")
