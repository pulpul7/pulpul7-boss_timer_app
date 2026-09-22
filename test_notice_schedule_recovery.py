"""Reproduce the Skadi 03:05 -> 03:08 bug without touching live data/audio."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from notice_module.payload.notice_management import KST, NoticeStore
from notice_module.payload.notice_schedule import synchronize_schedule


class ScheduleRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='notice-recovery-unit-')
        self.addCleanup(temp.cleanup)
        self.now = datetime(2026, 9, 21, 20, 16, 33, tzinfo=KST)
        self.store = NoticeStore(Path(temp.name), '9', clock=lambda: self.now)
        settings = self.store.snapshot()['settings']
        settings['output_enabled'] = True
        self.store.configure(settings)
        self.key = 'schedule/15/2026-09-21/overnight'
        self.snapshot = dict(server_id='9', season='15', events=[dict(boss_name='스카디',
            scheduled_at='2026-09-22T03:05:00+09:00', precision='minute', star=True,
            area='바나하임', state='scheduled')])
        synchronize_schedule(self.store, self.snapshot)

    def collect_empty(self):
        token, _ = self.store.claim_collection(manual=True)
        self.assertTrue(self.store.finish_collection(token, [], {}, {}))

    def old_bug(self, *, legacy=True):
        self.now = self.now.replace(minute=20, second=9)
        with self.store._transaction() as state:
            self.store._retire(state, state['events'][self.key], '공지 해제', self.now)
            if legacy:
                state['events'][self.key].pop('enabled_before_retirement')
                state['suppressed'].pop(self.key)  # Original collector also removed this tombstone.

    def test_cafe_collection_keeps_schedule_but_retires_real_removed_article(self):
        self.store.register(dict(id='cafe', title='공지', source_key='CT9G/123/time-v1/test',
            valid_from=self.now, valid_until=self.now + timedelta(hours=1)))
        before = self.store.snapshot()['events'][self.key]
        self.collect_empty()
        state = self.store.snapshot()
        self.assertEqual(state['events'][self.key], before)
        self.assertEqual(state['events']['cafe']['retired_reason'], '공지 해제')

    def test_recovers_exact_report_with_latest_0308_and_is_idempotent(self):
        self.old_bug()
        self.now = self.now.replace(minute=47, second=26)
        self.snapshot['events'][0]['scheduled_at'] = '2026-09-22T03:08:00+09:00'
        self.assertTrue(synchronize_schedule(self.store, self.snapshot))
        state = self.store.snapshot()
        event = state['events'][self.key]
        self.assertNotIn('retired_at', event)
        self.assertTrue(event['enabled'])
        self.assertFalse(event['analysis_hold'])
        self.assertIn('3시 8분 스카디', event['tts_text'])
        self.assertNotIn('3시 5분', event['tts_text'])
        self.assertEqual(event['event_from'], '2026-09-22T03:08:00+09:00')
        self.assertIn('source_recovery', event)
        self.assertIsNone(event['last_delivery'])
        self.assertFalse(synchronize_schedule(self.store, self.snapshot))
        self.assertEqual(self.store.snapshot(), state)
        self.collect_empty()
        self.assertEqual(self.store.snapshot()['events'][self.key], event)

    def test_manual_discard_and_tombstone_survive_collection_and_history_deletion(self):
        self.store.discard(self.key)
        self.collect_empty()
        self.assertTrue(self.store.snapshot()['suppressed'][self.key])
        self.assertFalse(synchronize_schedule(self.store, self.snapshot))
        self.assertEqual(self.store.snapshot()['events'][self.key]['retired_reason'], '사용자 폐기')
        self.store.delete_history([self.key])
        self.collect_empty()
        synchronize_schedule(self.store, self.snapshot)
        self.assertNotIn(self.key, self.store.snapshot()['events'])

    def test_expired_original_window_is_not_restored(self):
        self.store.edit_period(self.key, self.now - timedelta(hours=1), self.now + timedelta(minutes=1))
        self.old_bug()
        synchronize_schedule(self.store, self.snapshot)
        self.assertIn('retired_at', self.store.snapshot()['events'][self.key])

    def test_expired_replacement_window_is_not_restored(self):
        self.old_bug()
        self.now = self.now.replace(hour=23)
        synchronize_schedule(self.store, self.snapshot)
        self.assertIn('retired_at', self.store.snapshot()['events'][self.key])

    def test_missing_or_unstarred_target_is_not_restored(self):
        self.old_bug()
        self.snapshot['events'][0]['star'] = False
        synchronize_schedule(self.store, self.snapshot)
        self.assertIn('retired_at', self.store.snapshot()['events'][self.key])
        self.snapshot['events'] = []
        synchronize_schedule(self.store, self.snapshot)
        self.assertIn('retired_at', self.store.snapshot()['events'][self.key])

    def test_recorded_disable_manual_text_and_period_are_preserved(self):
        self.store.set_enabled(self.key, False)
        self.store.edit_tts(self.key, '직접 편집한 안내')
        self.store.edit_period(self.key, self.now, self.now.replace(hour=22))
        self.old_bug(legacy=False)
        self.snapshot['events'][0]['scheduled_at'] = '2026-09-22T03:08:00+09:00'
        synchronize_schedule(self.store, self.snapshot)
        event = self.store.snapshot()['events'][self.key]
        self.assertNotIn('retired_at', event)
        self.assertFalse(event['enabled'])
        self.assertEqual(event['tts_text'], '직접 편집한 안내')
        self.assertTrue(event['manual_period'])
        self.assertTrue(event['tts_review_required'])
        self.assertTrue(event['valid_until'].startswith('2026-09-21T22:16:33'))

    def test_recovery_keeps_quota_ledger_and_switches(self):
        self.old_bug()
        ledger = [dict(at=self.now.isoformat(), group='participation', event_id=self.key,
                       opportunity_id='completed-before-bug')]
        with self.store._transaction() as state:
            state['delivery_ledger'] = deepcopy(ledger)
            state['tts_template_enabled'] = {'participation.dawn': False}
        synchronize_schedule(self.store, self.snapshot)
        state = self.store.snapshot()
        self.assertEqual(state['delivery_ledger'], ledger)
        self.assertFalse(state['tts_template_enabled']['participation.dawn'])
        self.assertEqual(self.store.preparation_candidates(), [])

    def test_wrong_server_or_season_does_not_restore_original(self):
        self.old_bug()
        synchronize_schedule(self.store, dict(self.snapshot, server_id='8'))
        self.assertIn('retired_at', self.store.snapshot()['events'][self.key])
        synchronize_schedule(self.store, dict(self.snapshot, season='16'))
        self.assertIn('retired_at', self.store.snapshot()['events'][self.key])


if __name__ == '__main__':
    unittest.main()
