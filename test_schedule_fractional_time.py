"""Fractional timestamps must survive every schedule data boundary."""
from datetime import datetime,timedelta
import json
import unittest

from boss_timer_gui import BossTimerApp
from boss_timer_discord_bot import is_second_confirmed,parse_datetime,deserialize,ScheduleReader
from schedule_precision import clock_text,normalize_capture_rate
from precision_time_tracker import PrecisionConfig
import test_edge_tts_voice as existing_tests


class FractionalScheduleTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,9,10,16,0,0,120000)
        self.app=existing_tests.DiscordScheduleInputTests._parser_app(self.now)

    def test_colon_and_compact_input_are_equivalent(self):
        for text in ('16:43:02.55 파르바','164302.55 파르바'):
            item=self.app._parse_schedule_input_line(text)
            self.assertEqual(item['clock_microsecond'],550000)
            self.assertEqual(item['precision'],'second')
            self.assertEqual(self.app._resolve_schedule_seed_datetime(item,self.now),datetime(2026,9,10,16,43,2,550000))

    def test_uncertain_dialog_includes_new_bosses_and_minute_values(self):
        items=[dict(raw_key='active',state='active'),
               dict(raw_key='day',state='scheduled',mode='duration',remaining_seconds=90000),
               dict(raw_key='minute',state='scheduled',mode='clock',precision='minute'),
               dict(raw_key='exact',state='scheduled',mode='clock',precision='second',clock_microsecond=550000)]
        self.app.schedule_events=[]
        self.app.schedule_active_entries=[]
        found=self.app._collect_schedule_uncertain_overwrite_items(items)
        self.assertEqual({x['raw_key'] for x in found},{'active','day','minute'})

    def test_uncertain_ocr_warning_survives_text_but_not_manual_time_edit(self):
        line='16:43:02.55 파르바'
        self.app.schedule_input_ocr_mode_render_texts={'ocr':line}
        self.app.schedule_input_ocr_mode_line_severities={'ocr':{1:'warn'}}
        parsed=self.app._parse_schedule_input_line(line)
        found=self.app._collect_schedule_uncertain_overwrite_items([parsed])
        self.assertEqual(len(found),1)
        self.assertIn('OCR 경고',found[0]['input_uncertainty_reason'])
        self.assertNotIn('input_uncertainty_reason',parsed)
        edited=self.app._parse_schedule_input_line('16:43:03.55 파르바')
        self.assertEqual(self.app._collect_schedule_uncertain_overwrite_items([edited]),[])
        self.app.schedule_input_ocr_mode_line_severities={'ocr':{1:'normal'}}
        self.assertEqual(self.app._collect_schedule_uncertain_overwrite_items([parsed]),[])

    def test_six_digit_capture_is_not_reduced_to_centiseconds(self):
        exact=datetime(2026,9,10,16,43,1,508488)
        text=clock_text(exact)+' 파르바'
        item=self.app._parse_schedule_input_line(text)
        self.assertEqual(self.app._resolve_schedule_seed_datetime(item,self.now),exact)

    def test_clock_validation_and_midnight(self):
        self.assertIsNone(self.app._parse_schedule_input_line('16:43:02.1234567 파르바'))
        self.assertIsNone(self.app._parse_schedule_input_line('16:43:60.55 파르바'))
        item=self.app._parse_schedule_input_line('00:00:00.01 파르바')
        self.app._normalize_schedule_clock_items([item],reference_datetime=datetime(2026,9,10,23,59,59,990000))
        self.assertEqual(self.app._resolve_schedule_seed_datetime(item,self.now),datetime(2026,9,11,0,0,0,10000))

    def test_repeated_generation_and_storage_and_restore_keep_precision(self):
        app=self.app
        app._is_schedule_invasion_item=lambda item:False
        app._get_schedule_respawn_seconds_for_state_item=lambda item:3600
        app._is_schedule_boss_metric_schedule_duration_enabled=lambda:False
        app._apply_schedule_second_precision_offset_to_datetime=lambda value,item:value
        item=app._parse_schedule_input_line('164302.55 파르바')
        events,active=app._build_schedule_entries_for_item(item,self.now,self.now,self.now+timedelta(hours=3))
        self.assertEqual(len(events),3)
        self.assertTrue(all(e['scheduled_at'].microsecond==550000 and e['cycle_anchor_at'].microsecond==550000 for e in events))
        wire=json.loads(json.dumps(app._serialize_schedule_state_value(events)))
        restored=app._deserialize_schedule_state_value(wire)
        self.assertEqual(events,restored)
        self.assertEqual(parse_datetime(deserialize(wire[0]['scheduled_at'])),events[0]['scheduled_at'])

    def test_metric_recurrence_preserves_fraction(self):
        app=self.app
        app._is_schedule_invasion_item=lambda item:False
        app._get_schedule_respawn_seconds_for_state_item=lambda item:3600
        app._is_schedule_boss_metric_schedule_duration_enabled=lambda:True
        app._get_schedule_boss_metric_effective_duration_seconds=lambda item:30
        app._get_schedule_metric_duration_offset_direction=lambda direction,cut_chain:direction
        app._apply_schedule_second_precision_offset_to_datetime=lambda value,item:value
        item=app._parse_schedule_input_line('164302.508488 파르바')
        events,_=app._build_schedule_entries_for_item(item,self.now,self.now,self.now+timedelta(hours=3))
        self.assertTrue(all(e['scheduled_at'].microsecond==508488 for e in events))

    def test_cut_token_and_cut_state_keep_precision(self):
        exact=datetime(2026,9,10,15,59,59,550000)
        token=self.app._get_schedule_cut_token_from_datetime(exact,'second')
        self.assertEqual(token,'155959.55')
        self.assertEqual(self.app._get_schedule_cut_datetime({'scheduled_at':exact},token),exact)
        self.assertEqual(self.app._get_schedule_event_cut_datetime({'cut_at':exact}),exact)

    def test_cut_in_same_second_uses_fraction_to_select_the_day(self):
        item=self.app._parse_schedule_input_line('16:00:00.55 파르바 컷')
        exact=self.app._resolve_schedule_seed_datetime(item,self.now)
        self.assertEqual(exact,datetime(2026,9,9,16,0,0,550000))
        self.assertIsNone(self.app._parse_schedule_input_line('0일 16:00:00.55 파르바 컷'))

    def test_alarm_deadline_and_discord_confirmation_keep_fraction(self):
        exact=datetime(2026,9,10,16,43,0,550000)
        deadlines=self.app._get_schedule_alarm_second_precision_due_seconds([dict(second_precision=True,scheduled_at=exact)])
        self.assertEqual(deadlines,[exact])
        self.assertTrue(is_second_confirmed({'scheduled_at':exact.isoformat()}))
        self.assertEqual(parse_datetime(exact.isoformat()),exact)

    def test_discord_jobs_do_not_collapse_distinct_fractional_targets(self):
        first=(datetime.now()+timedelta(minutes=2)).replace(microsecond=120000)
        second=first.replace(microsecond=950000)
        events=[dict(scheduled_at=t,state='scheduled',boss_name=f'boss{i}',display_name=f'boss{i}',precision='second')
                for i,t in enumerate((first,second))]
        jobs=ScheduleReader()._build_schedule_event_jobs({'schedule_events':events},{'common_offsets':[60]})
        self.assertEqual({j.target_at for j in jobs},{first,second})

    def test_edit_text_retains_fraction_even_after_precision_label_expires(self):
        exact=datetime(2026,9,10,16,43,0,550000)
        text=self.app._format_schedule_clock_text_for_input(exact,'minute')
        self.assertEqual(text,'16:43:00.55')
        self.assertEqual(self.app._get_schedule_cut_token_from_datetime(exact,'minute'),'164300.55')

    def test_rates_and_buffer_duration(self):
        for rate in range(2,11):
            config=PrecisionConfig.from_rate(rate)
            self.assertAlmostEqual(config.interval*rate,1)
            self.assertGreaterEqual(config.buffer_samples*config.interval,15)
        self.assertEqual(normalize_capture_rate(None),5)
        self.assertEqual(normalize_capture_rate(1),2)
        self.assertEqual(normalize_capture_rate(360),10)
        self.assertEqual(PrecisionConfig.from_rate(2).interval,.5)
        self.assertEqual(PrecisionConfig().interval,.2)
        self.assertEqual(PrecisionConfig().buffer_samples,80)


if __name__=='__main__':
    unittest.main()
