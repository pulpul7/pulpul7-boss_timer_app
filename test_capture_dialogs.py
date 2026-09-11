"""Tk-only layout checks; never constructs the app or touches user settings."""
import unittest
import tkinter as tk
from unittest.mock import Mock
from boss_timer_gui import BossTimerApp
from precision_capture_widgets import CaptureProgress


class DialogTests(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk()
        self.root.geometry('700x500+100+100')
        self.root.update()
        self.app=object.__new__(BossTimerApp)
        self.app.root=self.root
        self.app.current_font_family='맑은 고딕'
        self.app.button_font=('맑은 고딕',10,'bold')
        self.app.percent_font=('맑은 고딕',9)
        self.app._get_centered_messagebox_parent=lambda parent:self.root
        self.app._widget_available=lambda widget:widget is not None and bool(widget.winfo_exists())

    def tearDown(self):
        self.root.destroy()

    def test_custom_confirm_is_centered_scrollable_and_default_no(self):
        checked=[]
        def inspect_and_close():
            window=next(w for w in self.root.winfo_children() if isinstance(w,tk.Toplevel))
            window.update_idletasks()
            expected_x=self.root.winfo_rootx()+(self.root.winfo_width()-window.winfo_width())//2
            expected_y=self.root.winfo_rooty()+(self.root.winfo_height()-window.winfo_height())//2
            checked.append((abs(window.winfo_x()-expected_x)<3,abs(window.winfo_y()-expected_y)<3,
                            any(isinstance(w,tk.Scrollbar) for w in window.winfo_children())))
            window.event_generate('<Return>')
        self.root.after(150,inspect_and_close)
        result=self.app._show_centered_messagebox('askyesno','미확정 보스 재시도',
              '다음 보스만 다시 측정합니다.\n\n'+'\n'.join(['• 헤르모드']*8)+'\n\n정상 결과는 유지됩니다.',
              parent=self.root,default='no')
        self.assertFalse(result)
        self.assertEqual(checked,[(True,True,True)])

    def test_progress_widgets_fit_and_cancel_works(self):
        cancel=Mock()
        progress=CaptureProgress(self.root,{'left':0,'top':0},cancel,retry_names=['헤르모드'],rate=5)
        progress.update(23,1,1,0,65)
        window=progress.window
        window.update()
        self.assertEqual((window.winfo_x(),window.winfo_y()),(570,6))
        for widget in window.winfo_children():
            self.assertLessEqual(widget.winfo_x()+widget.winfo_width(),window.winfo_width())
            self.assertLessEqual(widget.winfo_y()+widget.winfo_height(),window.winfo_height())
        self.assertIn('남은추적 1개',progress.status.cget('text'))
        next(w for w in window.winfo_children() if isinstance(w,tk.Button)).invoke()
        cancel.assert_called_once()
        window.destroy()


if __name__=='__main__': unittest.main()
