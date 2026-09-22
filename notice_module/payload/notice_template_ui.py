"""All speech scenarios, including those without a currently collected notice."""
import queue
import tkinter as tk
from tkinter import ttk

from .notice_templates import TEMPLATES, EXAMPLES, template_text, render_template
from .ui_helpers import center_window, title_band


class NoticeTemplateWindow:
    def __init__(self, management):
        self.owner = management
        self.store = management.store
        state = self.store.snapshot()
        self.revision = state.get("tts_templates_revision", 0)
        self.drafts = {key: template_text(state, key) for key in TEMPLATES}
        self.saved = dict(self.drafts)
        self.enabled = {key: state.get('tts_template_enabled', {}).get(key, True) for key in TEMPLATES}
        self.saved_enabled = dict(self.enabled)
        self.current = None
        self.poll_id = None
        self.preview = management._preview
        self.preview.stop()
        self.window = win = tk.Toplevel(management.window)
        win.title("안내 문구 기본 설정 · " + management.server_name)
        win.geometry("1100x800")
        win.minsize(980, 740)
        win.configure(bg="#eff6ff")
        win.transient(management.window)
        title_band(win, "안내 문구 기본 설정", f"{management.server_name} · 실제 공지가 없어도 모든 안내 유형을 미리 설정할 수 있습니다.")
        footer = tk.Frame(win, bg="#dbeafe", padx=14, pady=10)
        footer.pack(side="bottom", fill="x")
        self.status = tk.StringVar(win, value="유형별 실행 여부와 문구를 설정합니다. 체크 해제해도 문구·알림 기록은 보존합니다. 저장 후 적용됩니다.")
        tk.Label(footer, textvariable=self.status, bg="#dbeafe", fg="#1e3a8a", anchor="w",
                 wraplength=1010, justify="left").pack(fill="x", pady=(0, 6))
        ttk.Button(footer, text="실행 설정 · 문구 저장", command=self.save).pack(side="left")
        ttk.Button(footer, text="선택 문장 기본값 복원", command=self.restore).pack(side="left", padx=8)
        ttk.Button(footer, text="닫기", command=self.close).pack(side="right")
        body = tk.Frame(win, bg="#1e40af", padx=3, pady=3)
        body.pack(fill="both", expand=True, padx=14, pady=12)
        left = tk.Frame(body, bg="#f8fafc", padx=8, pady=8)
        left.pack(side="left", fill="y")
        tk.Label(left, text=f"전체 안내 유형 · {len(TEMPLATES)}개", bg="#f8fafc", fg="#172554",
                 font=("맑은 고딕", 11, "bold")).pack(anchor="w", pady=(0, 8))
        tree_frame = ttk.Frame(left)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("enabled", "name", "setting"), show="tree headings",
                                 selectmode="browse", style="Notice.Treeview", height=16)
        self.tree.heading("#0", text="종류")
        self.tree.column("#0", width=115, minwidth=85, stretch=False)
        self.tree.heading('enabled', text='실행')
        self.tree.column('enabled', width=45, minwidth=45, stretch=False, anchor='center')
        self.tree.heading("name", text="안내 상황")
        self.tree.column("name", width=155, minwidth=120)
        self.tree.heading("setting", text="문구")
        self.tree.column("setting", width=55, minwidth=50, stretch=False)
        scroll = ttk.Scrollbar(tree_frame, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.select)
        self.tree.bind('<Button-1>', self._toggle_row)
        right = tk.Frame(body, bg="#f8fafc", padx=14, pady=10)
        right.pack(side="left", fill="both", expand=True, padx=(3, 0))
        self.heading = tk.StringVar(win)
        tk.Label(right, textvariable=self.heading, bg="#172554", fg="white", anchor="w", padx=10,
                 pady=8, font=("맑은 고딕", 11, "bold")).pack(fill="x")
        self.condition = tk.StringVar(win)
        ttk.Label(right, textvariable=self.condition, wraplength=580, justify="left").pack(fill="x", pady=8)
        self.enabled_var = tk.BooleanVar(win, value=True)
        ttk.Checkbutton(right, text='이 유형의 안내 실행 (개별 편집 문장에도 적용)',
                        variable=self.enabled_var, command=self._toggle_selected).pack(anchor='w', pady=(0, 8))
        ttk.Label(right, text="기본 문구 · 직접 편집 (빈 문장은 음성 안내하지 않음)").pack(anchor="w")
        self.editor = tk.Text(right, height=6, wrap="word", undo=True, padx=8, pady=8)
        self.editor.pack(fill="both", expand=True, pady=6)
        self.tokens = tk.Frame(right, bg="#f8fafc")
        self.tokens.pack(fill="x")
        ttk.Label(right, text="날짜: 오늘/내일 또는 월·일 · 시간: 시·분 · 일시: 날짜+시간\n같은 날 종료 날짜는 생략합니다. 아래 시간·제목은 미리듣기용 예시입니다.",
                  wraplength=580).pack(fill="x", pady=6)
        self.sample = tk.Text(right, height=4, wrap="word", state="disabled", padx=8, pady=8,
                              bg="#eff6ff", fg="#1e3a8a")
        self.sample.pack(fill="both", expand=True)
        buttons = ttk.Frame(right)
        buttons.pack(fill="x", pady=8)
        ttk.Button(buttons, text="예시 문장 확인", command=self.show_sample).pack(side="left")
        ttk.Button(buttons, text="예시 음성 듣기 (이 PC)", command=self.listen).pack(side="left", padx=6)
        ttk.Button(buttons, text="정지", command=self.stop_preview).pack(side="left")
        ttk.Label(right, text="Edge 온라인 합성 사용 · Discord에는 보내지 않으며 안내 완료/횟수에 반영하지 않습니다.\n"
                  "저장 시 기존 자동 문구도 갱신합니다. 개별 편집 문장과 완료·폐기 이력은 유지합니다.",
                  wraplength=580, justify="left").pack(fill="x", pady=(2, 6))
        self._populate()
        self.tree.selection_set(next(iter(TEMPLATES)))
        self.select()
        win.protocol("WM_DELETE_WINDOW", self.close)
        win.bind("<Destroy>", self._destroy, add="+")
        center_window(win, management.window)
        win.grab_set()
        self.poll_id = win.after(100, self._poll)

    def _message(self, title, text):
        self.owner.app._show_centered_messagebox("showwarning", title, text, parent=self.window)

    def _capture(self):
        if self.current:
            self.drafts[self.current] = self.editor.get("1.0", "end-1c").strip()
            self.enabled[self.current] = self.enabled_var.get()
            label = "미저장" if (self.drafts[self.current] != self.saved[self.current]
                               or self.enabled[self.current] != self.saved_enabled[self.current]) else (
                "기본" if self.drafts[self.current] == TEMPLATES[self.current]["text"] else "편집")
            self.tree.set(self.current, "setting", label)
            self.tree.set(self.current, 'enabled', '☑' if self.enabled[self.current] else '☐')

    def _toggle_selected(self):
        self._capture()
        self.status.set('실행 여부가 변경되었습니다. 실행 설정 · 문구 저장을 눌러 적용하세요.')

    def _toggle_row(self, event):
        key = self.tree.identify_row(event.y)
        if key and self.tree.identify_column(event.x) == '#1':
            self.tree.selection_set(key)
            self.select()
            self.enabled_var.set(not self.enabled[key])
            self._toggle_selected()
            return 'break'

    def _populate(self):
        for key, item in TEMPLATES.items():
            label = "기본" if self.drafts[key] == item["text"] else "편집"
            if self.tree.exists(key):
                self.tree.set(key, "setting", label)
                self.tree.set(key, 'enabled', '☑' if self.enabled[key] else '☐')
            else:
                self.tree.insert("", "end", iid=key, text=item["category"],
                                 values=('☑' if self.enabled[key] else '☐', item["name"], label))

    def select(self, _event=None):
        selected = self.tree.selection()
        if not selected or selected[0] == self.current:
            return
        self._capture()
        self.current = key = selected[0]
        self.enabled_var.set(self.enabled[key])
        item = TEMPLATES[key]
        self.heading.set(item["category"] + " · " + item["name"])
        self.condition.set("안내 조건: " + item["condition"])
        self.editor.delete("1.0", "end")
        self.editor.insert("1.0", self.drafts[key])
        self.editor.edit_reset()
        for widget in self.tokens.winfo_children():
            widget.destroy()
        for index, field in enumerate(item["variables"]):
            ttk.Button(self.tokens, text="{" + field + "}",
                       command=lambda name=field: self.editor.insert("insert", "{" + name + "}")).grid(
                           row=index // 4, column=index % 4, sticky='w', padx=(0, 4), pady=2)
        self.show_sample()

    def _render_sample(self):
        self._capture()
        return render_template({"tts_templates": self.drafts}, self.current, EXAMPLES)

    def show_sample(self):
        try:
            text = self._render_sample() or "(빈 문장 · 음성 안내 안 함)"
        except ValueError as exc:
            text = "문법 확인 필요: " + str(exc)
        self.owner._set_text(self.sample, "예시 · 실제 일정이 아닙니다\n" + text)

    def listen(self):
        if not self.owner._guard():
            return
        try:
            self.preview.start(self._render_sample())
            self.status.set("선택 문장의 예시 음성을 이 PC에서 준비합니다. 저장/Discord 송출은 하지 않습니다.")
        except Exception as exc:
            self._message("기본 문구 미리듣기", str(exc))

    def stop_preview(self):
        self.preview.stop()
        self.status.set("미리듣기 정지 요청됨")

    def restore(self):
        if self.current:
            self.editor.delete("1.0", "end")
            self.editor.insert("1.0", TEMPLATES[self.current]["text"])
            self.show_sample()
            self.status.set("선택한 문장을 기본값으로 돌렸습니다. 실행 설정 · 문구 저장을 눌러 반영하세요. 실행 체크는 유지합니다.")

    def save(self):
        if not self.owner._guard():
            return
        self._capture()
        try:
            self.revision = self.store.save_tts_templates(self.drafts, expected_revision=self.revision, enabled=self.enabled)
            self.saved = dict(self.drafts)
            self.saved_enabled = dict(self.enabled)
            self._populate()
            self.owner._refresh()
            self.status.set("이 서버의 실행 여부와 기본 문구를 저장했습니다. 개별 편집 문장과 완료 기록은 유지했습니다.")
        except Exception as exc:
            self._message("기본 문구 저장", str(exc))

    def close(self):
        self._capture()
        if (self.drafts != self.saved or self.enabled != self.saved_enabled) and not self.owner.app._show_centered_messagebox(
                "askyesno", "기본 문구 편집", "저장하지 않은 문구 또는 실행 설정이 있습니다. 변경 내용을 버리고 닫을까요?",
                parent=self.window, default="no"):
            return
        self.window.destroy()

    def _poll(self):
        self.poll_id = None
        if not self.window.winfo_exists():
            return
        if str(getattr(self.owner.app, "schedule_server_profile_id", "") or "") != self.owner.server_id:
            self.preview.stop()
            self.status.set("서버가 변경되어 저장과 미리듣기를 차단했습니다. 창을 다시 열어 주세요.")
            return
        try:
            while True:
                self.status.set(self.preview.messages.get_nowait())
        except queue.Empty:
            pass
        self.poll_id = self.window.after(100, self._poll)

    def _destroy(self, event):
        if event.widget == self.window:
            self.preview.stop()
            if self.poll_id is not None:
                self.window.after_cancel(self.poll_id)
