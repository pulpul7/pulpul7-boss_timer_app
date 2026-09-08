from datetime import datetime, timedelta
import unittest

from boss_timer_gui import BossTimerApp
from voice_test_scenarios import VOICE_TEST_SCENARIOS, build_voice_test_plan


class Var:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class VoiceTestScenariosTests(unittest.TestCase):
    now = datetime(2026, 9, 8, 23, 59, 50)

    def app(self, second, countdown):
        app = BossTimerApp.__new__(BossTimerApp)
        app.schedule_alarm_voice_test_second_precision_var = Var(second)
        app.schedule_alarm_voice_test_countdown_var = Var(countdown)
        app._get_schedule_reference_datetime = lambda: self.now
        # Exclude boss alias/database resolution from this test-data contract.
        app._normalize_schedule_state_item = lambda item: dict(item)
        return app

    def index(self, number):
        return next(i for i, spec in enumerate(VOICE_TEST_SCENARIOS) if spec['display_no'] == number)

    def test_every_case_respects_all_checkbox_combinations_and_keeps_schedule_untouched(self):
        for second in (False, True):
            for countdown in (False, True):
                app = self.app(second, countdown)
                original = [{'boss_name': '사용자 스케쥴'}]
                app.schedule_events = original
                for index, spec in enumerate(VOICE_TEST_SCENARIOS):
                    with self.subTest(second=second, countdown=countdown, number=spec['display_no']):
                        cases = app._build_schedule_alarm_voice_test_cases(1, index)
                        self.assertEqual(len(cases), 1)
                        case = cases[0]
                        self.assertEqual(case['settings']['countdown'], second and countdown)
                        active = [event for event in case['events'] if event['state'] != 'elapsed']
                        self.assertEqual(len(active), len(spec['events']))
                        self.assertTrue(all(event['precision'] == ('second' if second else 'minute') for event in active))
                        self.assertTrue(all(bool(event['second_precision_origin_at']) == second for event in active))
                        self.assertTrue(all(event['scheduled_at'] > self.now for event in active))
                        self.assertEqual(len(case['fixed_entries']), len(spec['fixed']))
                        self.assertIs(app.schedule_events, original)

    def test_countdown_has_full_start_notice_lead_and_mode_does_not_change_relative_gaps(self):
        for index, spec in enumerate(VOICE_TEST_SCENARIOS):
            minute = build_voice_test_plan(index, self.now, 1, False, False)
            precise = build_voice_test_plan(index, self.now, 1, True, True)
            self.assertEqual(minute['events'], precise['events'])
            normals = [seconds for _name, seconds, invasion in spec['events'] if not invasion]
            if normals:
                earliest = precise['anchor'] + timedelta(seconds=min(normals))
                # 20초 시작 안내를 안정적으로 앞에 둘 수 있는 최소 여유.
                self.assertGreaterEqual((earliest - self.now).total_seconds(), 21)

    def test_legacy_9_4_gaps_and_invasion_identity_survive(self):
        app = self.app(True, True)
        case = app._build_schedule_alarm_voice_test_cases(1, self.index('9-4'))[0]
        offsets = sorted(int((event['scheduled_at'] - case['target_at']).total_seconds()) for event in case['events'])
        self.assertEqual(offsets, [0, 2, 60, 60, 60, 61])
        mixed = app._build_schedule_alarm_voice_test_cases(1, self.index('9-1'))[0]
        self.assertEqual(sum(event['is_invasion'] for event in mixed['events']), 4)
        for event in mixed['events']:
            if event['is_invasion']:
                self.assertEqual(event['display_name'].count('침공'), 1)
        self.assertGreaterEqual((mixed['finish_at'] - max(e['scheduled_at'] for e in mixed['events'])).total_seconds(), 20)

    def test_all_shifted_cases_have_sufficient_playback_tail(self):
        for index, spec in enumerate(VOICE_TEST_SCENARIOS):
            plan = build_voice_test_plan(index, self.now, 120, True, True)
            cues = [s for _, s, _ in spec['events'] if s <= spec['watch']]
            cues += [s - offset for _, s in spec['fixed'] for offset in spec['fixed_offsets'] if 0 <= s - offset <= spec['watch']]
            if spec['fixed_due']:
                cues += [s for _, s in spec['fixed']]
            if cues:
                last_cue = plan['anchor'] + timedelta(seconds=max(cues))
                # 짧은 단일 테스트는 마지막 재생 뒤 5초만 기다리고 원본을
                # 복원한다. 긴 고정/복합 테스트는 각 시나리오가 별도 watch를 준다.
                self.assertGreaterEqual((plan['finish_at'] - last_cue).total_seconds(), 5, spec['title'])

    def test_stress_contains_events_during_countdown_and_both_notice_types(self):
        spec = VOICE_TEST_SCENARIOS[self.index('15')]
        self.assertTrue(any(-15 < seconds < 0 and invasion for _, seconds, invasion in spec['events']))
        self.assertTrue(any(0 < seconds - 60 <= 15 and not invasion for _, seconds, invasion in spec['events']))
        self.assertTrue(any(0 < seconds - 300 <= 15 and invasion for _, seconds, invasion in spec['events']))
        self.assertEqual(len(spec['fixed']), 2)
        self.assertEqual(spec['fixed'][0][1], spec['fixed'][1][1])
        many = VOICE_TEST_SCENARIOS[self.index('16')]
        self.assertEqual(len(many['events']), 14)
        self.assertEqual(sum(not invasion for _, _, invasion in many['events']), 12)
        self.assertEqual(len({seconds for _, seconds, _ in many['events']}), 1)

    def test_fixed_boss_examples_use_real_name_and_include_next_boss_case(self):
        specs = {spec['display_no']: spec for spec in VOICE_TEST_SCENARIOS}
        self.assertIn('11-1', specs)
        self.assertIn('핏빛고블린', specs['11']['fixed'][1][0])
        self.assertFalse(any('빛빛고블린' in name for spec in VOICE_TEST_SCENARIOS for name, _seconds in spec['fixed']))
        app = self.app(True, True)
        case = app._build_schedule_alarm_voice_test_cases(1, self.index('12'))[0]
        self.assertNotIn('recording_preferred', case['settings'])

    def test_two_fixed_bosses_are_not_abbreviated(self):
        app = BossTimerApp.__new__(BossTimerApp)
        self.assertEqual(
            app._summarize_schedule_alarm_group_names(['지옥성채 정예', '핏빛고블린']),
            ('지옥성채 정예 핏빛고블린', 0),
        )
        self.assertEqual(
            app._summarize_schedule_alarm_group_names(['지옥성채 정예', '핏빛고블린', '월드보스']),
            ('지옥성채 정예 외 2개', 2),
        )

    def test_one_minute_tts_phrase_uses_a_single_korean_word(self):
        app = BossTimerApp.__new__(BossTimerApp)
        self.assertEqual(app._format_schedule_alarm_remaining_speech(60), '일분')

    def test_no_duplicate_schedules_and_every_visible_row_is_runnable(self):
        keys = [(s['events'], s['fixed'], s['normal_offsets'], s['fixed_offsets'], s['fixed_due']) for s in VOICE_TEST_SCENARIOS]
        self.assertEqual(len(set(keys)), len(keys))
        app = self.app(True, False)
        self.assertEqual(len(app._get_schedule_alarm_voice_rule_rows()), len(VOICE_TEST_SCENARIOS))
        self.assertEqual(len(app._get_schedule_alarm_voice_rule_detail_texts()), len(VOICE_TEST_SCENARIOS))
        for i, spec in enumerate(VOICE_TEST_SCENARIOS):
            self.assertEqual(app._get_schedule_alarm_voice_rule_display_number(i), spec['display_no'])

    def test_runtime_settings_follow_captured_mode_even_if_ui_changes(self):
        app = self.app(True, False)
        case = app._build_schedule_alarm_voice_test_cases(1, self.index('9-4'))[0]
        app.schedule_alarm_voice_test_countdown_var.set(True)
        for name in ('schedule_alarm_master_var', 'schedule_alarm_countdown_enabled_var',
                     'schedule_alarm_countdown_start_var', 'schedule_second_precision_expire_hours_var',
                     'schedule_fixed_boss_skip_due_time_var'):
            setattr(app, name, Var())
        app.schedule_alarm_ai_recording_preferred_var = Var(True)
        app._apply_schedule_alarm_voice_test_settings_for_case(case)
        self.assertFalse(app.schedule_alarm_countdown_enabled_var.get())
        self.assertTrue(app.schedule_alarm_ai_recording_preferred_var.get())

    def test_unchecking_precision_clears_countdown(self):
        app = self.app(False, True)
        app.schedule_alarm_voice_test_status_var = Var()
        app._on_schedule_alarm_voice_test_mode_changed()
        self.assertFalse(app.schedule_alarm_voice_test_countdown_var.get())


if __name__ == '__main__':
    unittest.main()
