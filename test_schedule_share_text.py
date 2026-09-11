from datetime import datetime,timedelta
import unittest
from unittest.mock import Mock,patch
from boss_timer_gui import BossTimerApp
from schedule_share_text import render_share_text
import test_edge_tts_voice as existing_tests


class Variable:
    def __init__(self,value): self.value=value
    def get(self): return self.value
    def set(self,value): self.value=value


class ShareTextTests(unittest.TestCase):
    def setUp(self): self.now=datetime(2026,9,11,12,0)

    def test_only_times_and_names_with_full_precision(self):
        rows=[dict(scheduled_at=self.now+timedelta(days=1,seconds=.551234),boss_text='파르바'),
              dict(scheduled_at=self.now-timedelta(hours=1),boss_text='티르')]
        text=render_share_text(rows,self.now)
        self.assertEqual(text,'12:00:00.551234 파르바\n11:00:00 티르')
        app=existing_tests.DiscordScheduleInputTests._parser_app(self.now)
        items,ignored=app._parse_schedule_input_lines(text,reference_datetime=self.now,allow_past_clock_seeds=True)
        self.assertEqual(ignored,0)
        self.assertEqual(items[0]['clock_microsecond'],551234)

    def test_informational_rows_cannot_become_bosses_or_controls(self):
        rows=[dict(kind='date_header',date_text='오늘'),
              dict(kind='break',scheduled_at=self.now,effective_end_at=self.now+timedelta(hours=1),break_display_text='휴식\n12:00 정기점검'),
              dict(kind='maintenance',scheduled_at=self.now,boss_text='정기점검'),
              dict(kind='event',is_fixed=True,scheduled_at=self.now,boss_text='고정보스')]
        app=existing_tests.DiscordScheduleInputTests._parser_app(self.now)
        items,ignored=app._parse_schedule_input_lines(render_share_text(rows,self.now),reference_datetime=self.now)
        self.assertEqual((items,ignored),([],0))
        self.assertEqual(render_share_text(rows,self.now),'')

    def test_invasion_prefix_is_preserved(self):
        text=render_share_text([dict(scheduled_at=self.now,boss_text='파르바',is_invasion=True)],self.now)
        self.assertEqual(text,'12:00:00 침공 파르바')

    def test_minute_only_input_is_not_promoted_to_confirmed_seconds(self):
        rows=[dict(scheduled_at=self.now,boss_text='파르바',input_clock_text='12:00'),
              dict(scheduled_at=self.now,boss_text='야른',input_clock_text='12:00:00')]
        text=render_share_text(rows,self.now)
        app=existing_tests.DiscordScheduleInputTests._parser_app(self.now)
        items,ignored=app._parse_schedule_input_lines(text,reference_datetime=self.now)
        self.assertEqual(ignored,0)
        self.assertEqual([item['precision'] for item in items],['minute','second'])

    def test_repeated_12_hour_and_24_hour_bosses_are_all_included(self):
        start=self.now.replace(hour=1,minute=2,second=3,microsecond=551234)
        rows=[dict(scheduled_at=start,boss_text='수드리'),
              dict(scheduled_at=start+timedelta(hours=1),boss_text='신마라'),
              dict(scheduled_at=start+timedelta(hours=12),boss_text='수드리'),
              dict(scheduled_at=start+timedelta(hours=24),boss_text='수드리'),
              dict(scheduled_at=start+timedelta(hours=25),boss_text='신마라')]
        self.assertEqual(render_share_text(rows,self.now),
                         '01:02:03.551234 수드리\n02:02:03.551234 신마라\n13:02:03.551234 수드리\n01:02:03.551234 수드리\n02:02:03.551234 신마라')
        self.assertEqual(len(rows),5)

    def test_duplicate_inclusion_preserves_display_order(self):
        rows=[dict(scheduled_at=self.now+timedelta(hours=2),boss_text='파르바'),
              dict(scheduled_at=self.now+timedelta(hours=1),boss_text='야른'),
              dict(scheduled_at=self.now+timedelta(hours=3),boss_text='파르바')]
        self.assertEqual(render_share_text(rows,self.now),'14:00:00 파르바\n13:00:00 야른\n15:00:00 파르바')

    def test_normal_and_invasion_remain_separate_chains(self):
        rows=[dict(scheduled_at=self.now,boss_text='파르바'),
              dict(scheduled_at=self.now,boss_text='파르바',is_invasion=True),
              dict(scheduled_at=self.now+timedelta(hours=12),boss_text='파르바',is_invasion=True)]
        self.assertEqual(render_share_text(rows,self.now),'12:00:00 파르바\n12:00:00 침공 파르바\n00:00:00 침공 파르바')

    def test_copy_sort_default_unchanged_txt_can_disable(self):
        app=object.__new__(BossTimerApp)
        app._get_schedule_reference_datetime=lambda:self.now
        rows=[dict(scheduled_at=self.now+timedelta(hours=2),boss_text='late'),
              dict(scheduled_at=self.now+timedelta(hours=1),boss_text='early')]
        app._collect_schedule_share_base_rows=lambda *a,**k:rows
        app._get_schedule_share_maintenance_rows=lambda *a,**k:[]
        app._get_schedule_share_row_sort_key=lambda r:r['scheduled_at']
        app._get_weekday_label=lambda dt:'금'
        def names(sorted_value):
            kwargs={} if sorted_value else {'sort_by_time':False}
            result,_,_=app._collect_schedule_share_rows(self.now,self.now+timedelta(days=1),**kwargs)
            return [r['boss_text'] for r in result if 'boss_text' in r]
        self.assertEqual(names(True),['early','late'])
        self.assertEqual(names(False),['late','early'])
        self.assertEqual([r['boss_text'] for r in rows],['late','early'])

    def ui_app(self):
        app=object.__new__(BossTimerApp)
        app.root=Mock(); app.schedule_input_window=Mock(); app.schedule_input_text=Mock()
        app.schedule_status_var=Variable(''); app.schedule_input_status_var=Variable('')
        app.schedule_input_guide_var=Variable(''); app.schedule_input_window_open=False
        app.schedule_input_ocr_sort_by_time_enabled=True
        app.schedule_input_ocr_sort_by_time_var=Variable(True)
        app.schedule_input_hide_fraction_var=Variable(False)
        app._set_schedule_input_past_generation_enabled=Mock()
        app._get_schedule_reference_datetime=lambda:self.now
        app._get_schedule_share_default_range=lambda:dict(start_datetime=self.now,end_datetime=self.now+timedelta(days=1),include_break_rows=True,exclude_elapsed=True)
        app._collect_schedule_share_rows=Mock(return_value=([dict(scheduled_at=self.now,boss_text='파르바')],self.now,self.now))
        app._open_schedule_input_window_normal=Mock()
        app._widget_available=lambda widget:widget is not None
        app._configure_schedule_input_window_mode=Mock()
        app._clear_schedule_input_ocr_results=Mock()
        app._close_schedule_input_ocr_addon_window=Mock()
        app._update_schedule_input_apply_state=Mock()
        return app

    def test_button_uses_parent_trigger_copies_and_temporarily_disables_sort(self):
        app=self.ui_app(); app._open_schedule_share_text()
        app._open_schedule_input_window_normal.assert_called_once()
        text=app.root.clipboard_append.call_args.args[0]
        app.schedule_input_text.insert.assert_called_once_with('1.0',text)
        self.assertFalse(app.schedule_input_ocr_sort_by_time_var.get())
        self.assertTrue(app.schedule_input_hide_fraction_var.get())
        self.assertTrue(app._collect_schedule_share_rows.call_args.kwargs['exclude_elapsed'])
        self.assertTrue(app._collect_schedule_share_rows.call_args.kwargs['include_break_rows'])
        self.assertFalse(app._collect_schedule_share_rows.call_args.kwargs['sort_by_time'])
        app._reset_schedule_input_mode_state()
        self.assertTrue(app.schedule_input_ocr_sort_by_time_var.get())
        self.assertTrue(app.schedule_input_ocr_sort_by_time_enabled)

    def test_existing_draft_is_preserved_if_replace_cancelled(self):
        app=self.ui_app(); app.schedule_input_window_open=True
        app._get_schedule_input_raw_text=lambda:'my draft'
        with patch.object(app,'_show_centered_messagebox',return_value=False) as ask:
            app._open_schedule_share_text()
        self.assertEqual(ask.call_args.args[0],'askyesno')
        self.assertIs(ask.call_args.kwargs['parent'],app.schedule_input_window)
        app.root.clipboard_append.assert_called_once()
        app.schedule_input_text.delete.assert_not_called()
        app._open_schedule_input_window_normal.assert_not_called()

    def test_busy_capture_does_not_overwrite_input_or_clipboard(self):
        app=self.ui_app(); app.schedule_input_ocr_worker_active=True
        app._open_schedule_share_text()
        app.root.clipboard_clear.assert_not_called()
        app.schedule_input_text.delete.assert_not_called()


if __name__=='__main__': unittest.main()
