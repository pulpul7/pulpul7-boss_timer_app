"""Desktop-only font catalog controls; public pages cannot edit deployment settings."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, ttk

from web_font_library import WebFontLibrary


class WebFontPanel:
    def __init__(self, owner, frame):
        self.owner = owner
        self.library = WebFontLibrary(owner.manager.root)
        self.rows = []
        tk.Label(frame, text='웹 폰트 · 히든메뉴', font=('맑은 고딕', 11, 'bold'),
                 bg='#f8fafc', fg='#0f172a').pack(anchor='w', pady=(4, 8))
        owner._label(frame, '번호는 유지됩니다. 웹에서는 선택한 폰트만 불러오며 PC 폰트 설치는 하지 않습니다.',
                     anchor='w', wraplength=650, justify='left').pack(fill='x')
        self.list = tk.Listbox(frame, height=13, font=owner.font, exportselection=False,
                              bg='#fff', fg='#0f172a', selectbackground='#1e3a8a', selectforeground='#fff')
        self.list.pack(fill='both', expand=True, pady=10)
        row = tk.Frame(frame, bg='#f8fafc');row.pack(fill='x')
        owner._label(row, '기본 폰트').pack(side='left', padx=(0, 8))
        self.default = ttk.Combobox(row, state='readonly', width=30)
        self.default.pack(side='left', padx=(0, 8))
        owner._button(row, '기본값 저장', self.save).pack(side='left')
        actions = tk.Frame(frame, bg='#f8fafc');actions.pack(fill='x', pady=10)
        for text, command in (('추가', self.add), ('삭제', self.delete), ('배포용 저장', self.export)):
            owner._button(actions, text, command).pack(side='left', padx=(0, 8))
        self.message = tk.StringVar(value='')
        tk.Label(frame, textvariable=self.message, bg='#f8fafc', fg='#475569', font=owner.font,
                 anchor='w', justify='left', wraplength=650).pack(fill='x')
        owner._label(frame, '배포용 저장: 소스의 web_assets/fonts 폴더에 목록·기본값·폰트를 저장한 뒤 직접 빌드하세요.\n'
                           '일반 사용자는 배포된 폰트를 삭제할 수 없으며, 직접 추가한 폰트만 브라우저에 보관합니다.',
                     anchor='w', justify='left', wraplength=650).pack(fill='x', pady=(8, 0))
        self.refresh()

    def refresh(self):
        try:
            data = self.library.public_catalog();self.rows = data['fonts']
            labels = ['00. 기기 기본 폰트'] + [f"{row['number']:02}. {row['name']} · {row['provider']}" for row in self.rows]
            self.ids = ['system'] + [row['id'] for row in self.rows]
            self.list.delete(0, 'end')
            for label in labels:
                self.list.insert('end', label)
            self.default.configure(values=labels)
            self.default.current(self.ids.index(data['default']))
            self.message.set(f"공통 폰트 {len(self.rows)}개 · 기본값: {labels[self.default.current()]}")
        except Exception as exc:
            self.message.set('폰트 목록을 읽지 못했습니다. web_assets/fonts 폴더를 확인해주세요.')
            self.owner.manager._log('web_font_catalog_failed', error_type=type(exc).__name__)

    def run(self, operation, action):
        try:
            action();self.refresh()
            self.owner.manager._log('web_font_settings_changed', operation=operation)
            return True
        except Exception as exc:
            self.owner.messagebox('showerror', '웹 폰트', str(exc) if isinstance(exc, ValueError) else
                                  '폰트 설정을 저장하지 못했습니다. 파일 권한을 확인해주세요.', parent=self.owner.window)
            self.owner.manager._log('web_font_settings_failed', operation=operation, error_type=type(exc).__name__)
            return False

    def save(self):
        index = self.default.current()
        if index >= 0:
            self.run('default', lambda: self.library.set_default(self.ids[index]))

    def add(self):
        path = filedialog.askopenfilename(parent=self.owner.window, title='공통 웹 폰트 추가',
                                          filetypes=[('폰트', '*.woff2 *.woff *.ttf *.otf')])
        if path:
            self.run('add', lambda: self.library.add(path))

    def delete(self):
        selection = self.list.curselection()
        if selection:
            self.run('delete', lambda: self.library.delete(self.ids[selection[0]]))

    def export(self):
        base = Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
        folder = filedialog.askdirectory(parent=self.owner.window, title='배포할 web_assets/fonts 폴더 선택',
                                         initialdir=str(base / 'web_assets' / 'fonts'), mustexist=True)
        if folder:
            if self.run('export', lambda: self.library.export(folder)):
                self.message.set('배포용 저장이 완료되었습니다. 선택한 web_assets 폴더를 포함해 직접 빌드해주세요.')
