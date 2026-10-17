"""Master-only distribution checklist. Does not start compilers or services."""
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog

from distribution_catalog import OPTIONS
from distribution_publish import publish_selection, selection_at
from distribution_defaults import json_bytes
from runtime_storage import atomic_write


class DistributionSettingsWindow:
    def __init__(self, app, data_root, project_hint):
        if not app._is_master_developer_mode_enabled():
            raise PermissionError('마스터 관리자 전용 기능입니다.')
        self.app=app;self.preference=Path(data_root)/'distribution_settings_ui.json'
        parent=app.schedule_window
        self.window=tk.Toplevel(parent);self.window.withdraw()
        self.window.title('배포설정 · 마스터 관리자');self.window.configure(bg='#eef3fb')
        self.window.transient(parent);self.window.resizable(True,True);self.window.minsize(680,510)
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.window.bind('<Escape>',lambda _e:self.close())
        hint=Path(project_hint)
        try:
            saved=json.loads(self.preference.read_text(encoding='utf-8'))
        except (OSError,ValueError):saved={}
        project=str(hint) if (hint/'boss_timer_gui.spec').is_file() else str(saved.get('project',''))
        self.project=tk.StringVar(value=project);self.status=tk.StringVar()
        self.variables={key:tk.BooleanVar(value=False) for key,*_ in OPTIONS}
        title=tk.Frame(self.window,bg='#173c69',padx=20,pady=14);title.pack(fill='x')
        title_row=tk.Frame(title,bg='#173c69');title_row.pack(fill='x')
        tk.Label(title_row,text='배포설정',font=app.header_font,bg='#173c69',fg='white',anchor='w').pack(side='left',fill='x',expand=True)
        self.rollback_button=tk.Button(title_row,text='롤백값 갱신',font=app.button_font,
                                       bg='#f6d99b',fg='#4c3511',activebackground='#ffe7b8',
                                       command=self.refresh_rollback,cursor='hand2')
        self.rollback_button.pack(side='right',padx=(12,0))
        tk.Label(title,text='체크한 설정과 알리미 실행 정책을 다음 배포에 적용합니다.',font=app.label_font,bg='#173c69',fg='#dfebff',anchor='w').pack(fill='x',pady=(5,0))
        tk.Label(title,text='롤백값 갱신: 현재 서버 설정을 새 시즌 시작의 설정롤백 기준으로 저장합니다.',
                 font=app.percent_font,bg='#173c69',fg='#dfebff',anchor='w',justify='left',wraplength=700).pack(fill='x',pady=(5,0))
        path_row=tk.Frame(self.window,bg='#eef3fb',padx=16,pady=12);path_row.pack(fill='x')
        tk.Label(path_row,text='빌드 원본 폴더',font=app.label_font,bg='#eef3fb',fg='#23364e').pack(anchor='w')
        entry=tk.Entry(path_row,textvariable=self.project,font=app.percent_font,state='readonly',readonlybackground='white')
        entry.pack(side='left',fill='x',expand=True,pady=(5,0))
        tk.Button(path_row,text='폴더 선택',font=app.percent_font,command=self.choose,cursor='hand2').pack(side='left',padx=(8,0),pady=(5,0))
        footer=tk.Frame(self.window,bg='#dde7f4',padx=16,pady=12);footer.pack(side='bottom',fill='x')
        tk.Label(footer,textvariable=self.status,font=app.percent_font,bg='#dde7f4',fg='#173c69',anchor='w',justify='left',wraplength=660).pack(fill='x')
        actions=tk.Frame(footer,bg='#dde7f4');actions.pack(fill='x',pady=(10,0))
        tk.Button(actions,text='전체 선택',font=app.percent_font,command=lambda:self.select(True)).pack(side='left')
        tk.Button(actions,text='전체 해제',font=app.percent_font,command=lambda:self.select(False)).pack(side='left',padx=6)
        tk.Button(actions,text='닫기',font=app.button_font,command=self.close).pack(side='right')
        self.save_button=tk.Button(actions,text='현재 설정으로 저장',font=app.button_font,bg='#1d4ed8',fg='white',activebackground='#1e40af',activeforeground='white',command=self.save,cursor='hand2')
        self.save_button.pack(side='right',padx=8)
        outer=tk.Frame(self.window,bg='#eef3fb');outer.pack(fill='both',expand=True,padx=16,pady=(0,8))
        canvas=tk.Canvas(outer,bg='#eef3fb',highlightthickness=0)
        scroll=tk.Scrollbar(outer,orient='vertical',command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        body=tk.Frame(canvas,bg='#eef3fb');item=canvas.create_window(0,0,window=body,anchor='nw')
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(item,width=e.width))
        body.bind('<Configure>',lambda _e:canvas.configure(scrollregion=canvas.bbox('all')))
        current=None
        for key,section,label,name,description in OPTIONS:
            if section!=current:
                current=section
                tk.Label(body,text=section,font=app.label_font,bg='#ccdcef',fg='#173c69',anchor='w',padx=10,pady=6).pack(fill='x',pady=(8,4))
            row=tk.Frame(body,bg='white',highlightbackground='#d5dfec',highlightthickness=1,padx=8,pady=5);row.pack(fill='x',pady=2)
            tk.Checkbutton(row,text=label,variable=self.variables[key],command=self.changed,font=app.label_font,bg='white',activebackground='#eef5ff',fg='#142d4d',selectcolor='#dbeafe',anchor='w',cursor='hand2').pack(fill='x')
            tk.Label(row,text=name+'\n'+description,font=app.percent_font,bg='white',fg='#475569',anchor='w',justify='left',padx=25,wraplength=620).pack(fill='x')
        tk.Label(body,text='접속 비밀번호·토큰·실제 스케줄·이력·개인 음성은 이 목록에 포함하지 않습니다.\n‘알리미 실행 코드’는 개인 설정을 초기화하지 않고 배포본 코드를 선택합니다.\n각 설정 창에서 수정을 완료한 값이 저장됩니다. 빌드와 배포는 실행하지 않습니다.',font=app.percent_font,bg='#eef3fb',fg='#475569',justify='left',anchor='w',wraplength=640,pady=10).pack(fill='x')
        def wheel(e):
            canvas.yview_scroll(-int(e.delta/120),'units');return 'break'
        def bind(widget):
            widget.bind('<MouseWheel>',wheel)
            for child in widget.winfo_children():bind(child)
        bind(body);canvas.bind('<MouseWheel>',wheel)
        self.load()
        width=min(800,self.window.winfo_screenwidth()-60);height=min(800,self.window.winfo_screenheight()-100)
        app._center_window_over_parent(self.window,parent,width,height)
        self.window.deiconify();self.window.lift()

    def load(self):
        try:
            selected=selection_at(self.project.get()) if self.project.get() else set()
            for key,var in self.variables.items():var.set(key in selected)
            self.changed()
        except (OSError,ValueError,TypeError) as exc:self.status.set('기존 배포설정을 읽지 못했습니다: '+str(exc))

    def changed(self):
        count=sum(var.get() for var in self.variables.values())
        self.status.set(f'{count}개 파일 선택 · 받는 PC에서 적용 전 백업 후 1회 덮어씁니다.' if count else '선택 없음 · 저장하면 이번 배포의 덮어쓰기를 해제합니다.')
        self.save_button.configure(state='normal' if self.project.get() else 'disabled')

    def select(self,value):
        for var in self.variables.values():var.set(value)
        self.changed()

    def choose(self):
        chosen=filedialog.askdirectory(parent=self.window,title='boss_timer_gui.spec가 있는 빌드 원본 폴더 선택',initialdir=self.project.get() or None)
        if chosen:
            if not (Path(chosen)/'boss_timer_gui.spec').is_file():
                self.status.set('boss_timer_gui.spec가 있는 폴더를 선택해주세요.');return
            self.project.set(chosen);self.load()

    def save(self):
        if not self.app._is_master_developer_mode_enabled():
            self.close();return
        selected={key for key,var in self.variables.items() if var.get()}
        self.save_button.configure(state='disabled');self.window.update_idletasks()
        try:
            if selected:
                self.app._save_settings()
            result=publish_selection(self.project.get(),selected,self.app._get_distribution_settings_sources())
            try:atomic_write(self.preference,json_bytes(dict(project=self.project.get())))
            except OSError:pass
            self.status.set(f'{result["count"]}개 파일 저장 완료 · 다음 수동 빌드에 포함됩니다. 기존 배포 원본은 백업했습니다.'
                            if selected else '덮어쓰기 해제 저장 완료 · 다음 배포는 기존 AppData 설정을 유지합니다.')
        except Exception as exc:
            self.status.set('저장하지 못했습니다: '+str(exc))
        finally:self.save_button.configure(state='normal')

    def refresh_rollback(self):
        if not self.app._is_master_developer_mode_enabled():
            self.close();return
        self.rollback_button.configure(state='disabled')
        self.save_button.configure(state='disabled');self.window.update_idletasks()
        try:
            success,message=self.app._refresh_settings_rollback_baseline()
            self.status.set(message)
            if not success:
                self.app._show_centered_messagebox('showerror','롤백값 갱신',message,parent=self.window)
        finally:
            self.rollback_button.configure(state='normal')
            self.save_button.configure(state='normal' if self.project.get() else 'disabled')

    def close(self):
        self.app.distribution_settings_window=None
        self.window.destroy()
