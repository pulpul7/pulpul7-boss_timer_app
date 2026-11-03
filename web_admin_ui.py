"""Desktop-only web administrator controls; operations use the manager queue."""
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timezone, timedelta


def password_body(parent):
    canvas = tk.Canvas(parent, bg='#f8fafc', highlightthickness=0)
    bar = ttk.Scrollbar(parent, orient='vertical', command=canvas.yview)
    bar.pack(side='right', fill='y')
    canvas.pack(side='left', fill='both', expand=True)
    canvas.configure(yscrollcommand=bar.set)
    body = tk.Frame(canvas, bg='#f8fafc')
    item = canvas.create_window((0, 0), window=body, anchor='nw')
    canvas.bind('<Configure>', lambda event: canvas.itemconfigure(item, width=event.width))
    body.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
    body.scroll_canvas = canvas
    return body


def bind_password_wheel(body):
    canvas = body.scroll_canvas
    def wheel(event):
        if body.winfo_height() > canvas.winfo_height():
            canvas.yview_scroll(-1 if event.delta > 0 else 1, 'units')
        return 'break'
    def bind(widget):
        if not isinstance(widget, (ttk.Combobox, ttk.Treeview)):
            widget.bind('<MouseWheel>', wheel, add='+')
        for child in widget.winfo_children():
            bind(child)
    bind(body)
    canvas.bind('<MouseWheel>', wheel)


class WebAdminPanel:
    def __init__(self, owner, parent):
        self.owner, self.signature = owner, None
        self.candidates = []
        frame = tk.LabelFrame(parent, text='웹 전용 관리자', font=('맑은 고딕', 10, 'bold'),
                              bg='#f8fafc', fg='#1e3a8a', bd=1, relief='solid')
        frame.pack(fill='x', padx=22, pady=(6, 12))
        owner._label(frame, '일반 비밀번호로 로그인한 기기를 선택해 등록하세요. 같은 닉네임이어도 다른 기기는 별도 등록합니다.\n'
                     '웹의 색지정·관리 메뉴에만 적용됩니다. 기존 개발자·마스터 권한은 유지됩니다.',
                     anchor='w', justify='left', wraplength=620).pack(fill='x', padx=10, pady=8)
        row = tk.Frame(frame, bg='#f8fafc')
        row.pack(fill='x', padx=10, pady=(0, 6))
        self.choice = ttk.Combobox(row, state='readonly', width=43)
        self.choice.pack(side='left', fill='x', expand=True)
        owner._operation(row, '관리자 등록', self.add).pack(side='left', padx=(8, 0))
        self.list = ttk.Treeview(frame, columns=('name', 'device', 'at'), show='headings', height=3,
                                 selectmode='browse')
        for key, title, width in [('name', '등록된 관리자', 160), ('device', '기기 ID', 110), ('at', '등록 시각 (KST)', 210)]:
            self.list.heading(key, text=title)
            self.list.column(key, width=width, stretch=True)
        self.list.pack(fill='x', padx=10, pady=(0, 6))
        owner._operation(frame, '선택 관리자 해제', self.remove).pack(anchor='e', padx=10, pady=(0, 8))

    def add(self):
        index = self.choice.current()
        if 0 <= index < len(self.candidates):
            self.owner._submit_access('admin_add', self.candidates[index]['id'])
        else:
            self.owner.access_message.set('등록할 일반 로그인 기기를 선택해주세요. 목록이 비어 있으면 해당 기기에서 먼저 웹에 로그인하세요.')

    def remove(self):
        selected = self.list.selection()
        if selected:
            self.owner._submit_access('admin_remove', selected[0])
        else:
            self.owner.access_message.set('해제할 웹 관리자를 목록에서 선택해주세요.')

    def refresh(self, data):
        admins = data.get('web_admins', [])
        registered = {row['id'] for row in admins}
        rows = [row for row in data.get('visitors', []) if row.get('role') == 'member'
                and row.get('state') == '조회 중' and row['id'] not in registered]
        signature = repr((admins, [(row['id'], row.get('nickname'), row.get('browser')) for row in rows]))
        if signature == self.signature:
            return
        self.signature = signature
        index = self.choice.current()
        selected = self.candidates[index]['id'] if 0 <= index < len(self.candidates) else None
        self.candidates = rows
        self.choice.configure(values=[f"{row.get('nickname', 'guest')} · {row.get('browser', '')} · {row['id'][:8]}" for row in rows])
        self.choice.set('')
        if rows:
            self.choice.current(next((i for i, row in enumerate(rows) if row['id'] == selected), 0))
        for identity in set(self.list.get_children()) - registered:
            self.list.delete(identity)
        for row in admins:
            try:
                at = datetime.fromisoformat(row['registered_at']).astimezone(timezone(timedelta(hours=9))).strftime('%m/%d %H:%M')
            except ValueError:
                at = '—'
            values = (row['nickname'], row['id'][:8], at)
            if self.list.exists(row['id']):
                self.list.item(row['id'], values=values)
            else:
                self.list.insert('', 'end', iid=row['id'], values=values)
