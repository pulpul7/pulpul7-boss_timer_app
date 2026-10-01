"""Notice controls and explicit local-only previews; no automatic output here."""
from copy import deepcopy
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk
import uuid

from .ui_helpers import center_window, title_band, notice_styles, NoticeTabs, example_entry
from .notice_templates import event_template_enabled
from .notice_preview import LocalPreview
from .notice_polling import polling_plan
from .notice_opportunities import policy_description, playback_description, group_of
from .notice_management import (
    CATEGORIES, WEEKDAYS, NoticeStore, NoticeError, display_time, event_status, local_now, parse_time,
)

EVENT_COLUMNS = ('enabled', 'playback', 'title', 'from', 'until', 'state', 'listen')
LISTEN_COLUMN = f'#{EVENT_COLUMNS.index("listen") + 1}'


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
        win.title(f"알리미 관리 · {self.server_name}")
        win.configure(bg="#eff6ff")
        win.geometry("1280x800")
        win.minsize(960, 740)
        win.transient(parent)
        notice_styles(win)
        title_band(win, f"알리미 관리  ·  {self.server_name}",
                   "이 PC의 해당 서버 설정만 저장합니다. 수집은 각 클라이언트가 독립적으로 수행합니다.")
        tk.Label(win, text="기간 미확정은 발견 안내 1회 후 폐기 · 자동 송출은 연결된 디코봇에서만 · 미리듣기는 이 PC에서만 재생합니다.",
                 bg="#fef3c7", fg="#92400e", anchor="w", padx=8, pady=6).pack(fill="x", padx=18, pady=8)
        self.tabs = tabs = NoticeTabs(win)
        tabs.pack(fill="both", expand=True, padx=16, pady=4)
        sources = tabs.add_page("수집한 공지 / 원문")
        live = tabs.add_page("등록된 알림 / 유효기간")
        history = tabs.add_page("종료·폐기 이력 · 30일")
        settings = tabs.add_page("서버별 수집 / 송출 설정")
        self._preview = LocalPreview(getattr(self.app, "preview_synthesizer", None))
        self._preview_key = None
        self._preview_after = None
        self._ignore_release = False
        self._sources(sources)
        self.tree = self._event_tree(live)
        self.history_tree = self._event_tree(history, history=True)
        self.detail = self._text(live, 4)
        self.detail.bind("<Double-Button-1>", lambda _: self._edit_tts())
        self.history_detail = self._text(history, 4)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._details(False))
        self.history_tree.bind("<<TreeviewSelect>>", lambda _: self._details(True))
        self.tree.bind("<ButtonRelease-1>", self._event_click)
        self.tree.bind("<Double-Button-1>", self._event_double_click)
        self.tree.bind("<Return>", lambda _: self._edit_tts())
        buttons = ttk.Frame(live)
        buttons.pack(fill="x", pady=8)
        for label, command in (("수동 알림 등록", self._add), ("참여 독려 등록", lambda: self._add(participation=True)), ("사용 체크 전환", self._toggle),
                               ("유효기간 수정", self._edit_period), ("선택 알림 폐기", self._discard)):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=(0, 6))
        ttk.Button(live, text="안내 문구 기본 설정 · 모든 유형", command=self._open_tts_templates).pack(anchor="w", pady=(0, 6))
        tk.Label(live, text="행 더블클릭: TTS 편집 · 오른쪽 듣기: 이 PC에서만 재생/정지 · 미리듣기는 안내 횟수에 반영되지 않습니다.",
                 anchor="w", bg="#f8fafc", fg="#334155").pack(fill="x")
        self.preview_status = tk.StringVar(win, value="미리듣기 대기 · Edge 온라인 합성 사용")
        tk.Label(live, textvariable=self.preview_status, bg="#dbeafe", fg="#1e3a8a",
                 anchor="w", padx=8, pady=4).pack(fill="x", pady=(4, 0))
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
        win.bind("<Destroy>", self._close_preview, add="+")
        self._preview_after = win.after(100, self._poll_row_preview)

    def _guard(self):
        if str(getattr(self.app, "schedule_server_profile_id", "") or "") == self.server_id:
            return True
        self.app._show_centered_messagebox("showwarning", "서버 변경",
            "현재 서버가 변경되었습니다. 이 창에서는 저장하지 않습니다.\n알리미 관리 버튼을 눌러 현재 서버의 창을 다시 열어 주세요.", parent=self.window)
        return False

    def _watch_server(self):
        if not self.window.winfo_exists():
            return
        if str(getattr(self.app, "schedule_server_profile_id", "") or "") != self.server_id:
            self.status.set("서버가 변경되어 이 창의 저장을 차단했습니다. 알리미 관리를 다시 열어 주세요.")
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
            self.app._show_centered_messagebox("showerror", "알리미 관리", str(exc), parent=self.window)
            return False

    def _event_tree(self, parent, history=False):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        columns = EVENT_COLUMNS[:-1] if history else EVENT_COLUMNS
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=6, style="Notice.Treeview",
                            selectmode="extended" if history else "browse")
        for key, label, width in (("enabled", "사용", 45), ("playback", "재생 예정 / 조건 (KST)", 330),
                                  ("title", "알림 제목", 220), ("from", "유효 시작 (KST)", 145),
                                  ("until", "유효 종료 (KST)", 145), ("state", "현재 상태", 190)):
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=width, stretch=key in {'playback', 'title', 'state'})
        if not history:
            tree.heading("listen", text="음성 듣기", command=self._listen_selected)
            tree.column("listen", width=88, minwidth=88, stretch=False, anchor="center")
        tree.tag_configure("held", foreground="#b45309")
        tree.tag_configure("disabled", foreground="#64748b")
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
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
                if (not history and item['id'].startswith('manual-') and item.get('category') == 'participation'
                        and item.get('tts_template', '') in {'', 'participation.general'} and item.get('enabled')):
                    status = '대표 대상 선정용 · 저녁 1/2차로 통합'
                type_enabled = event_template_enabled(state, item)
                if not history and not type_enabled:
                    status = '유형별 실행 꺼짐'
                if not history and "유효 ·" in status and not state["settings"]["output_enabled"]:
                    status = "이 클라이언트 송출 꺼짐"
                tags = ("held",) if "미확정" in status else (("disabled",) if not item["enabled"] or not type_enabled else ())
                values = ("☑" if item["enabled"] else "☐", playback_description(item), item["title"],
                          display_time(item["valid_from"]), display_time(item["valid_until"]), status)
                if not history:
                    values += (self._listen_label(item),)
                tree.insert("", "end", iid=item["id"], values=values, tags=tags)
            keep = [key for key in selected if tree.exists(key)]
            if keep:
                tree.selection_set(keep)
            self._details(history)
        collection = state.get("collection", {})
        self.status.set(f"{self.server_name} · 알림 {len(self.tree.get_children())}개 / 이력 {len(self.history_tree.get_children())}개 · "
                        f"{collection.get('status', '수집 대기')} · "
                        f"{getattr(getattr(self, 'app', None), 'notice_output_status', lambda: '자동 송출 상태 확인 필요')()}")

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
        self.source_tree = ttk.Treeview(frame, columns=("date", "title", "state"), show="headings", height=6,
                                       selectmode="browse", style="Notice.Treeview")
        for key, label, width in (("date", "작성일 (KST)", 115), ("title", "상단 공지 제목", 530), ("state", "수집 상태", 245)):
            self.source_tree.heading(key, text=label)
            self.source_tree.column(key, width=width, minwidth=90)
        scroll = ttk.Scrollbar(frame, command=self.source_tree.yview)
        self.source_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.source_tree.pack(side="left", fill="both", expand=True)
        self.source_tree.bind("<<TreeviewSelect>>", lambda _: self._source_details())
        self.source_detail = self._text(parent, 6)

    def _refresh_sources(self):
        state = getattr(self, "source_state", {})
        selected = self.source_tree.selection()
        self.source_tree.delete(*self.source_tree.get_children())
        categories = state.get("settings", {}).get("categories", {})
        for key, item in sorted(state.get("articles", {}).items(), key=lambda pair: (not pair[1].get("pinned", False), pair[1].get("rank", 0))):
            if not categories.get(item.get("category"), False) or (not item.get("pinned") and not self.show_removed.get()):
                continue
            status = ("공지 해제" if not item.get("pinned") else "본문 갱신 실패 · 이전 내용 보존" if item.get("body_error")
                      else item.get("analysis", {}).get("status") or item.get("review_note") or "본문 확인 · 알림 분석 대기")
            self.source_tree.insert("", "end", iid=key, values=(item.get("published_date") or "미확정", item["title"], status))
        for key in selected:
            if self.source_tree.exists(key):
                self.source_tree.selection_set(key)
        if not self.source_tree.selection() and self.source_tree.get_children():
            self.source_tree.selection_set(self.source_tree.get_children()[0])
        collection = state.get("collection", {})
        message = f"{collection.get('status', '수집 대기')} · 마지막 성공: {display_time(collection.get('last_success'))}\n{collection.get('message', '조회 시간표에 따라 자동 수집하거나 지금 공지 수집을 눌러 주세요.')}"
        self.source_summary.set(message)
        plan = polling_plan(state, local_now()) if state.get("settings") else None
        if plan:
            self.source_summary.set(message + "\n자동 조회: " + plan["message"])
        self._source_details()

    def _source_details(self):
        selected = self.source_tree.selection()
        item = getattr(self, "source_state", {}).get("articles", {}).get(selected[0]) if selected else None
        if not item:
            self._set_text(self.source_detail, "계정보호·런처 도움말·운영정책을 제외한 상단 공지만 수집합니다.\n수집한 본문은 알림 이벤트와 별개이며, 유효기간 분석 전에는 안내하지 않습니다.")
            return
        analysis = item.get("analysis", {})
        facts = [analysis.get("status", "분석 대기")]
        imported = getattr(self, 'source_state', {}).get('maintenance_imports', {}).get(selected[0])
        if imported:
            statuses = {'pending': '처리 중 중단 가능 · 스케줄 확인 필요 (자동 재등록 안 함)',
                        'applied': '스케줄 등록 완료', 'existing': '이미 스케줄에 있음',
                        'conflict': '기존 임시점검과 충돌 · 수동 확인 필요', 'failed': '등록 실패 · 수동 확인 필요'}
            facts.append('임시점검 1회 등록: ' + statuses.get(imported.get('status'), '확인 필요')
                         + ' / ' + display_time(imported.get('scheduled_at')))
        if (item.get("uncertain_first_seen") and item.get("category") in {"maintenance", "transfer", "class_change", "event"}
                and (not analysis.get('windows') or any(fact.get('issue') for fact in analysis['windows']))):
            facts.append("기간 미확정 발견 안내: 등록 후 24시간 이내 1회 · 재생 완료 시 자동 폐기 (원문은 유지)")
        for fact in analysis.get("windows", []):
            facts.append(f"• {fact['label']}: {display_time(fact['start'])} ~ {display_time(fact['end'])}"
                         f"\n  {fact.get('issue') or fact.get('note') or '기간 확인'}\n  근거: {fact['evidence']}")
        self._set_text(self.source_detail, f"{item['title']}\n작성일: {item.get('published_date') or '미확정'}"
                       f"\n출처: {item['url']}\n최근 본문 확인: {display_time(item.get('last_checked'))}"
                       f"\n본문 변경 감지: {display_time(item.get('changed_at'))} / 판본 {item.get('revision', 0)}"
                       f"\n이미지 {len(item.get('images', []))}개 (다운로드·OCR 하지 않음)"
                       f"\n{item.get('body_error') or item.get('review_note') or ''}\n\n" + "\n".join(facts)
                       + f"\n\n{item.get('body') or '본문을 아직 읽지 못했습니다.'}")

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
        ledger = getattr(self, "source_state", {}).get("delivery_ledger", [])
        today = local_now().date().isoformat()
        count = sum(row["group"] == group_of(item) and row["at"].startswith(today) for row in ledger)
        lines = [f"{item['title']}  /  {CATEGORIES[item['category']]}",
                 f"재생 예정 / 조건 (KST): {playback_description(item)}",
                 "보스 안내 후: 해당 1분 전 안내가 끝난 뒤 대기열 조건을 확인합니다. 준비/재생 지연으로 실제 시작은 늦어질 수 있습니다.",
                 f"알림 유효기간: {display_time(item['valid_from'])} ~ {display_time(item['valid_until'])}",
                 f"행사 자체 기간: {display_time(item['event_from'])} ~ {display_time(item['event_until'])}",
                 f"기간 기준: {'사용자 지정' if item['manual_period'] else '등록/수집 정보'}",
                 f"마지막 안내: {display_time(item['last_delivery'])}",
                 (f"오류 복구: {display_time(item['source_recovery']['recovered_at'])} · "
                  f"{item['source_recovery']['reason']}") if item.get('source_recovery') else "",
                 f"종료·폐기: {display_time(item.get('retired_at'))}" if history else "",
                 f"출처: {item['source_url'] or '수동 등록 / 출처 없음'}",
                 f"안내 조건: {policy_description(item)} (보스 음성 우선 · 디코 연결 필요)",
                 "유형별 실행: " + ('켜짐' if event_template_enabled(getattr(self, 'source_state', {}), item) else '꺼짐 · 기본 설정에서 변경'),
                 f"오늘 같은 정책 그룹의 재생 완료: {count}회" if item.get("policy", "once") != "once" else "",
                 f"분석 보류: {item.get('analysis_hold') or '없음'}",
                 "완료 처리: 1회 재생 성공 후 자동 폐기" if item.get("retire_after_delivery") else "",
                 f"기간 근거: {item.get('analysis_evidence') or '(없음)'}",
                 f"문장 기준: {'사용자 편집 (재수집 시 유지)' if item.get('manual_tts') else '자동 생성'}",
                 "주의: 원문 기간/자동 문장이 변경됐습니다. 편집 문장을 확인하고 저장해야 안내할 수 있습니다." if item.get("tts_review_required") else "",
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

    def _event_click(self, event):
        if self._ignore_release:
            self._ignore_release = False
            return "break"
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        key = self.tree.identify_row(event.y)
        if not key:
            return
        self.tree.selection_set(key)
        if self.tree.identify_column(event.x) == LISTEN_COLUMN:
            self._listen_selected()
        else:
            self._toggle_click(event)

    def _event_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        key = self.tree.identify_row(event.y)
        if not key:
            return
        self._ignore_release = self.tree.identify_column(event.x) in {"#1", LISTEN_COLUMN}
        self.tree.selection_set(key)
        # The first click already toggled these controls. Do not do it twice.
        if self.tree.identify_column(event.x) not in {"#1", LISTEN_COLUMN}:
            self._edit_tts()
        return "break"

    def _listen_label(self, item):
        if self._preview_key == item["id"]:
            return "■ 정지"
        return "▶ 듣기" if item.get("tts_text", "").strip() else "문장 없음"

    def _listen_selected(self):
        item = self._selected()
        if not item or not self._guard():
            return
        if self._preview_key == item["id"]:
            self._preview.stop()
            self.preview_status.set("미리듣기 정지 요청됨")
            return
        try:
            # Read current saved text, not a stale row from before a collection.
            item = self.store.snapshot()["events"].get(item["id"])
            if not item or item.get("retired_at"):
                raise NoticeError("종료·폐기되었거나 삭제된 알림입니다. 목록을 새로고침해 주세요.")
            self._preview.start(item["tts_text"])
            self._preview_key = item["id"]
            self.tree.set(item["id"], "listen", "■ 정지")
            self.preview_status.set(f"{item['title']} · 이 PC에서 미리듣기 준비 중 (Discord 송출 없음)")
        except Exception as exc:
            self.app._show_centered_messagebox("showwarning", "이 PC에서 미리듣기", str(exc), parent=self.window)

    def _poll_row_preview(self):
        self._preview_after = None
        if not self.window.winfo_exists():
            return
        if str(getattr(self.app, "schedule_server_profile_id", "") or "") != self.server_id:
            self._preview.stop()
            self.preview_status.set("서버가 변경되어 미리듣기를 중단했습니다.")
            return
        editor = getattr(self, "_tts_editor", None)
        templates = getattr(self, "_tts_templates", None)
        if ((editor is None or not editor.winfo_exists())
                and (templates is None or not templates.window.winfo_exists())):
            try:
                while True:
                    self.preview_status.set(self._preview.messages.get_nowait())
            except queue.Empty:
                pass
        if (self._preview_key and not (self._preview.worker and self._preview.worker.is_alive())
                and self._preview.process is None):
            key, self._preview_key = self._preview_key, None
            if self.tree.exists(key):
                self.tree.set(key, "listen", self._listen_label(self.events[key]))
        self._preview_after = self.window.after(100, self._poll_row_preview)

    def _close_preview(self, event):
        if event.widget != self.window:
            return
        self._preview.stop()
        if self._preview_after is not None:
            self.window.after_cancel(self._preview_after)

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

    def _form(self, title, fields, save, *, examples=None):
        if not self._guard():
            return
        win = tk.Toplevel(self.window)
        win.title(title)
        win.transient(self.window)
        win.resizable(False, False)
        title_band(win, title)
        frame = ttk.Frame(win, padding=18)
        frame.pack(fill="both", expand=True)
        variables = {}
        for row, (key, label, value) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
            variable = variables[key] = tk.StringVar(win, value=value)
            example_entry(frame, variable, (examples or {}).get(key, ''), width=54).grid(row=row, column=1, sticky="ew", pady=5)
        help_text = "날짜: YYYY-MM-DD HH:MM (한국시간) · 미확정이면 비워두기\n유효기간이 없으면 저장은 되지만 안내하지 않습니다."
        if examples:
            help_text += '\n참여독려 제목은 별 체크 보스명 또는 행사명(예: 길드던전)으로 입력하세요.\n21~24시 일정은 대표 대상을 골라 저녁 1차·2차 안내로 통합합니다.'
        ttk.Label(frame, text=help_text).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=8)
        def submit():
            if self._run(lambda: save({key: var.get().strip() for key, var in variables.items()})):
                win.destroy()
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e", pady=6)
        ttk.Button(buttons, text="저장", command=submit).pack(side="left", padx=6)
        ttk.Button(buttons, text="취소", command=win.destroy).pack(side="left")
        center_window(win, self.window)

    def _edit_tts(self):
        item = self._selected()
        if not item or not self._guard():
            return
        existing = getattr(self, "_tts_editor", None)
        if existing is not None and existing.winfo_exists():
            existing.destroy()
        self._preview.stop()
        preview = self._preview  # One preview at a time across the list and editor.
        win = tk.Toplevel(self.window)
        self._tts_editor = win
        win.title("TTS 문장 보기 / 편집")
        win.transient(self.window)
        win.geometry("760x640")
        win.minsize(600, 460)
        title_band(win, "TTS 문장 보기 / 편집", item["title"])
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="자동 생성 문장 (비교용)").pack(anchor="w", pady=(12, 0))
        original = self._text(frame, 4)
        self._set_text(original, item.get("generated_tts_text", item["tts_text"]))
        ttk.Label(frame, text="실제로 읽을 문장 · 직접 수정한 내용은 재수집해도 유지됩니다.").pack(anchor="w")
        editor = tk.Text(frame, height=7, wrap="word", undo=True, padx=8, pady=8)
        editor.insert("1.0", item["tts_text"])
        editor.pack(fill="both", expand=True, pady=6)
        ttk.Label(frame, text="빈 문장은 송출하지 않습니다. 저장은 음성 생성·재생을 실행하지 않습니다.\n원문 기간이 바뀌면 편집 문장을 재확인할 때까지 안내를 보류합니다.", wraplength=650).pack(anchor="w")
        ttk.Label(frame, text="미리듣기는 Edge 온라인 합성을 사용하며, 이 PC의 기본 오디오 장치로만 재생합니다.\nDiscord 송출·알림 완료·횟수 기록에는 반영하지 않습니다.", wraplength=690).pack(anchor="w", pady=(8, 0))
        preview_status = tk.StringVar(win, value="미리듣기 대기")
        ttk.Label(frame, textvariable=preview_status, wraplength=690).pack(anchor="w", pady=4)
        preview_buttons = ttk.Frame(frame)
        preview_buttons.pack(fill="x")
        def listen():
            if not self._guard():
                return
            try:
                preview.start(editor.get("1.0", "end-1c"))
                listen_button.configure(state="disabled")
            except Exception as exc:
                self.app._show_centered_messagebox("showwarning", "이 PC에서 미리듣기", str(exc), parent=win)
        def stop_preview():
            preview.stop()
            preview_status.set("정지 요청됨 · 생성 중이었다면 결과를 재생하지 않고 정리합니다.")
        listen_button = ttk.Button(preview_buttons, text="이 PC에서 미리듣기", command=listen)
        listen_button.pack(side="left", padx=(0, 8))
        ttk.Button(preview_buttons, text="미리듣기 정지", command=stop_preview).pack(side="left")
        poll_id = [None]
        def poll_preview():
            poll_id[0] = None
            if not win.winfo_exists():
                return
            if str(getattr(self.app, "schedule_server_profile_id", "") or "") != self.server_id:
                preview.stop()
                preview_status.set("서버가 변경되어 미리듣기를 중단했습니다.")
                listen_button.configure(state="disabled")
                return
            templates = getattr(self, "_tts_templates", None)
            if templates is not None and templates.window.winfo_exists():
                poll_id[0] = win.after(100, poll_preview)
                return
            try:
                while True:
                    preview_status.set(preview.messages.get_nowait())
            except queue.Empty:
                pass
            listen_button.configure(state="disabled" if preview.worker and preview.worker.is_alive() else "normal")
            poll_id[0] = win.after(100, poll_preview)
        def close_preview(event):
            if event.widget != win:
                return
            preview.stop()
            if poll_id[0] is not None:
                try:
                    win.after_cancel(poll_id[0])
                except tk.TclError:
                    pass
        win.bind("<Destroy>", close_preview, add="+")
        poll_id[0] = win.after(100, poll_preview)
        def save(use_generated=False):
            if not self._guard():
                return
            try:
                self.store.edit_tts(item["id"], editor.get("1.0", "end-1c"), use_generated=use_generated,
                                    expected_revision=item["revision"])
            except Exception as exc:
                self.app._show_centered_messagebox("showerror", "TTS 문장 편집", str(exc), parent=win)
                return
            self._refresh()
            win.destroy()
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(10, 0))
        for label, command in (("저장", save), ("자동 문장으로 복원", lambda: save(True)), ("취소", win.destroy)):
            ttk.Button(buttons, text=label, command=command).pack(side="left", padx=(0, 8))
        center_window(win, self.window)
        editor.focus_set()

    def _open_tts_templates(self):
        if not self._guard():
            return
        current = getattr(self, "_tts_templates", None)
        if current is not None and current.window.winfo_exists():
            current.window.lift()
            current.window.focus_set()
            return
        editor = getattr(self, "_tts_editor", None)
        if editor is not None and editor.winfo_exists():
            # Keep unsaved per-event text intact; the settings window is modal.
            self._preview.stop()
        from .notice_template_ui import NoticeTemplateWindow
        try:
            self._tts_templates = NoticeTemplateWindow(self)
        except Exception as exc:
            self.app._show_centered_messagebox("showerror", "안내 문구 기본 설정", str(exc), parent=self.window)

    def _edit_period(self):
        item = self._selected()
        if item:
            self._form("알림 유효기간 수정", [("start", "유효 시작", display_time(item["valid_from"]) if item["valid_from"] else ""),
                                               ("end", "유효 종료", display_time(item["valid_until"]) if item["valid_until"] else "")],
                       lambda values: self.store.edit_period(item["id"], values["start"], values["end"]))

    def _add(self, participation=False):
        day = local_now().strftime('%Y-%m-%d')
        examples = dict(title='길드던전', start=day + ' 18:00', end=day + ' 23:00',
                        event_start=day + ' 22:30', event_end=day + ' 23:30',
                        tts='오늘 22시 30분 길드던전이 있습니다. 많은 참여 부탁드립니다.',
                        body='22시 20분까지 집결 / 준비물 안내 (음성에는 포함 안 됨)') if participation else None
        self._form("참여 독려 등록" if participation else "수동 알림 등록", [("title", "알림 제목", ""), ("start", "알림 유효 시작", ""),
                                     ("end", "알림 유효 종료", ""), ("event_start", "행사 자체 시작 (필수)" if participation else "행사 자체 시작 (선택)", ""),
                                     ("event_end", "행사 자체 종료 (선택)", ""), ("tts", "안내 문장 (비우면 유형별 기본값)", ""),
                                     ("body", "참고 내용", "")],
                   lambda values: self._register_manual_notice(values, participation), examples=examples)

    def _register_manual_notice(self, values, participation):
        event = {"id": "manual-" + uuid.uuid4().hex, "title": values["title"],
                 "category": "participation" if participation else "general",
                 "valid_from": values["start"], "valid_until": values["end"], "event_from": values["event_start"],
                 "event_until": values["event_end"], "tts_text": values["tts"], "body": values["body"]}
        if not values["tts"].strip():
            event["tts_template"] = "participation.general" if participation else "manual.general"
            event["tts_values"] = {"알림제목": values["title"]}
            start = parse_time(values["event_start"])
            if start:
                event["tts_values"]["시작시간"] = start.strftime("%m월 %d일 %H시 %M분")
                event["tts_values"]["_start_date"] = start.date().isoformat()
        return self.store.register(event)

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
        self.rules_tree = ttk.Treeview(frame, columns=("days", "start", "end", "minutes"), show="headings", height=4,
                                      style="Notice.Treeview")
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
            self.status.set(f"{self.server_name}의 수집 / 송출 설정을 저장했습니다. 자동 송출은 디코 연결·음성 사전 준비 후 실행합니다.")
