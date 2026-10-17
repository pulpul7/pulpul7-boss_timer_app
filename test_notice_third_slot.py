"""Third announcement timing, completed representative, ranges and quotas."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from notice_module.payload.notice_management import NoticeStore, KST
from notice_module.payload.notice_schedule import synchronize_schedule, schedule_notices
from notice_module.payload.notice_templates import render_template


class ThirdSlotTests(unittest.TestCase):
    def setUp(self):
        neutral = patch('notice_module.payload.notice_defaults.bundled_preferences', return_value=None)
        neutral.start()
        self.addCleanup(neutral.stop)
        temp = tempfile.TemporaryDirectory(prefix='notice-third-slot-unit-')
        self.addCleanup(temp.cleanup)
        self.now = datetime(2026, 10, 17, 18, tzinfo=KST)
        self.day = self.now.replace(hour=0)
        self.store = NoticeStore(Path(temp.name), 'server', clock=lambda: self.now)
        settings = self.store.snapshot()['settings']
        settings['output_enabled'] = True
        self.store.configure(settings)
        self.snapshot = dict(server_id='server', season='18', events=[
            self.row('공성전', 22, fixed=True), self.row('히로킨', 23, 30, area='니플하임', star=True),
            self.row('티르', 1, 30, tomorrow=True, area='바나하임', star=True),
            self.row('새벽시작', 1, 30, second=1, tomorrow=True, area='니플하임', star=True)])

    def row(self, name, hour, minute=0, *, tomorrow=False, second=0, **extra):
        at = self.day.replace(hour=hour, minute=minute, second=second) + timedelta(days=int(tomorrow))
        return dict(dict(boss_name=name, scheduled_at=at.isoformat(), precision='second'), **extra)

    def refresh(self):
        return synchronize_schedule(self.store, self.snapshot)

    def event(self, slot):
        return max((row for row in self.store.snapshot()['events'].values()
                    if row.get('tts_values', {}).get('_slot') == slot), key=lambda row: row['id'])

    def announce_first(self):
        self.refresh()
        event = self.event('evening_first')
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token(event['id'])))
        self.refresh()

    def test_needs_actual_first_or_second_completion_not_just_registration(self):
        self.refresh()
        self.assertFalse(any(e.get('tts_values', {}).get('_slot') == 'evening_third'
                             for e in self.store.snapshot()['events'].values()))
        self.announce_first()
        third = self.event('evening_third')
        self.assertEqual(third['valid_from'], self.day.replace(hour=22).isoformat())
        self.assertIsNone(self.store.delivery_token(third['id']))
        self.assertEqual(third['tts_text'], '오늘 남은 주요보스 히로킨, 티르가 남았습니다.')

    def test_start_trigger_all_remaining_to_0130_and_dawn_starts_013001(self):
        self.announce_first()
        self.now = self.day.replace(hour=22)
        original = deepcopy(self.snapshot)
        self.refresh()
        third = self.event('evening_third')
        self.assertEqual([row['boss_name'] for row in third['tts_values']['_remaining_entries']], ['히로킨', '티르'])
        dawn = self.event('overnight_once')
        self.assertEqual([row['name'] for row in dawn['tts_values']['_boss_entries']], ['새벽시작'])
        token = self.store.delivery_token(third['id'])
        self.assertIsNotNone(token)
        self.assertTrue(self.store.complete_delivery(token))
        self.assertTrue(self.store.snapshot()['events'][third['id']]['retired_at'])
        self.assertEqual(self.snapshot, original)
        self.refresh()
        self.assertIsNone(self.store.delivery_token(third['id']))

    def test_second_completion_supersedes_first_anchor_and_invalidates_old_plan(self):
        self.announce_first()
        self.snapshot['events'] = self.snapshot['events'][1:]
        self.now = self.day.replace(hour=20)
        self.refresh()
        second = self.event('evening_second')
        context = dict(id='world', server_id='server', at=self.now.isoformat(), phase='one_minute_complete',
                       kind='fixed', fixed_kind='world_boss', scheduled_at=self.now.isoformat())
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token(second['id'], opportunity=context)))
        self.refresh()
        third = self.event('evening_third')
        self.assertEqual(third['valid_from'], self.day.replace(hour=23, minute=30).isoformat())
        self.assertEqual(third['tts_text'], '오늘 남은 주요보스 티르가 남았습니다.')

    def test_completion_receipt_prevents_anchor_drifting_with_current_source(self):
        self.announce_first()
        first = self.event('evening_first')
        with self.store._transaction() as state:
            source = state['events'][first['id']]
            source['event_from'] = self.day.replace(hour=23, minute=30).isoformat()
            source['tts_values']['_representative'] = dict(boss_name='히로킨', scheduled_at=source['event_from'])
        self.refresh()
        self.assertEqual(self.event('evening_third')['valid_from'], self.day.replace(hour=22).isoformat())

    def test_older_completion_record_can_use_retained_representative_entry(self):
        self.announce_first()
        first = self.event('evening_first')
        with self.store._transaction() as state:
            state['events'][first['id']]['tts_values'].pop('_representative')
            for receipt in state['delivery_ledger']:
                receipt.pop('representative', None)
        self.refresh()
        self.assertEqual(self.event('evening_third')['valid_from'], self.day.replace(hour=22).isoformat())

    def test_third_completion_is_independent_of_first_second_daily_quota(self):
        self.announce_first()
        self.now = self.day.replace(hour=20)
        self.refresh()
        second = self.event('evening_second')
        context = dict(id='world', server_id='server', at=self.now.isoformat(), phase='one_minute_complete',
                       kind='fixed', fixed_kind='world_boss', scheduled_at=self.now.isoformat())
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token(second['id'], opportunity=context)))
        self.refresh()
        self.now = self.day.replace(hour=22)
        third = self.event('evening_third')
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token(third['id'])))
        ledger = self.store.snapshot()['delivery_ledger']
        self.assertEqual([r['slot'] for r in ledger], ['evening_first', 'evening_second', 'evening_third'])
        self.assertEqual(ledger[-1]['group'], 'participation_remaining')

    def test_midnight_representative_keeps_previous_cycle_and_no_restart_replay(self):
        self.snapshot['events'][0] = self.row('공성전', 0, tomorrow=True, fixed=True)
        self.announce_first()
        self.now = self.day + timedelta(days=1)
        self.refresh()
        third = self.event('evening_third')
        self.assertIn('2026-10-17/evening_third', third['id'])
        token = self.store.delivery_token(third['id'])
        self.assertTrue(self.store.complete_delivery(token))
        self.store = NoticeStore(self.store.root, 'server', clock=lambda: self.now)
        self.refresh()
        self.assertIsNone(self.store.delivery_token(third['id']))
        self.now = self.now.replace(hour=18)
        self.day = self.now.replace(hour=0)
        self.snapshot['events'] = [self.row('공성전', 22, fixed=True),
                                   self.row('히로킨', 23, 30, star=True, area='니플하임')]
        self.announce_first()
        new_key = f"schedule/18/{self.day.date().isoformat()}/evening_third"
        self.assertIn(new_key, [e['id'] for e in self.store.preparation_candidates()])

    def test_no_late_replay_or_empty_remaining_list(self):
        self.announce_first()
        self.now = self.day.replace(hour=22, minute=2)
        self.refresh()
        third = self.event('evening_third')
        self.assertIsNone(self.store.delivery_token(third['id']))
        self.assertFalse(any(e.get('tts_values', {}).get('_slot') == 'evening_third'
                             for e in schedule_notices(self.snapshot, self.store.snapshot(), self.now)))

    def test_empty_remaining_list_has_no_third_notice(self):
        self.snapshot['events'] = self.snapshot['events'][:1]
        self.announce_first()
        self.assertFalse(any(e.get('tts_values', {}).get('_slot') == 'evening_third'
                             for e in self.store.snapshot()['events'].values()))

    def test_elapsed_and_excluded_rows_are_never_spoken(self):
        self.snapshot['events'].extend([
            self.row('일반', 23, area='니플하임'), self.row('침공', 23, star=True, is_invasion=True),
            self.row('컷', 23, star=True, cut_applied=True), self.row('삭제', 23, star=True, state='deleted'),
            self.row('불확정', 23, star=True, precision='hour')])
        self.announce_first()
        entries = self.event('evening_third')['tts_values']['_remaining_entries']
        self.assertEqual([r['boss_name'] for r in entries], ['히로킨', '티르'])
        text = render_template({}, 'participation.remaining', {'_remaining_entries': entries},
                               now=self.day.replace(hour=23, minute=45))
        self.assertEqual(text, '오늘 남은 주요보스 티르가 남았습니다.')

    def test_no_additional_weekday_morning_rule_was_added(self):
        # Sunday evening -> Monday morning 7-chapter/absolute/8-chapter bosses.
        self.now = datetime(2026, 10, 18, 18, tzinfo=KST)
        self.snapshot['events'] = [dict(boss_name='아침', scheduled_at='2026-10-19T07:00:00+09:00',
                                       precision='second', area='니플하임', absolute=True, star=True)]
        planned = schedule_notices(self.snapshot, self.store.snapshot(), self.now)
        self.assertFalse(any(e.get('tts_template') == 'participation.morning' for e in planned))

    def test_0130_seconds_are_covered_without_overlap_or_missing_fraction(self):
        self.snapshot['events'].append(dict(self.row('소수초', 1, 30, tomorrow=True, star=True, area='니플하임'),
                                            scheduled_at='2026-10-18T01:30:00.999999+09:00'))
        self.announce_first()
        third = self.event('evening_third')
        self.assertIn('소수초', [r['boss_name'] for r in third['tts_values']['_remaining_entries']])
        dawn = self.event('overnight_once')
        self.assertNotIn('소수초', [r['name'] for r in dawn['tts_values']['_boss_entries']])


if __name__ == '__main__':
    unittest.main()
