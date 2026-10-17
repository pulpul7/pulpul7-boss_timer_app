"""Post-maintenance input-completion notice, without audio or network access."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from notice_module.payload.notice_management import KST, NoticeStore
from notice_module.payload.notice_schedule import synchronize_schedule
from notice_module.payload.notice_schedule_update import MESSAGE, PREFIX, opening_cycle


class ScheduleUpdateTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='notice-web-schedule-unit-')
        self.addCleanup(temp.cleanup)
        self.now = datetime(2026, 10, 17, 12, tzinfo=KST)
        self.store = NoticeStore(Path(temp.name), 'test-server', clock=lambda: self.now)
        settings = self.store.snapshot()['settings']
        settings['output_enabled'] = True
        settings['categories']['participation'] = False
        self.store.configure(settings)
        self.snapshot = dict(server_id='test-server', season='18', events=[],
                             maintenance_start='2026-10-17T08:00:00+09:00',
                             server_open='2026-10-17T11:00:00+09:00',
                             regular_maintenance='2026-10-21T08:00:00+09:00',
                             input_boss_names=['A', 'B'], input_entries=[], control_events=[], web_schedule_published=True)

    def fill(self):
        self.snapshot['input_entries'] = [dict(boss_name=name, state='scheduled',
            created_at='2026-10-17T11:10:00+09:00') for name in self.snapshot['input_boss_names']]

    def notices(self):
        return [e for e in self.store.snapshot()['events'].values() if e['id'].startswith(PREFIX)]

    def article(self, title, start='2026-10-17T08:00:00+09:00', end='2026-10-17T11:30:00+09:00', **facts):
        return dict(category='maintenance', title=title, analysis={'windows': [dict(
            kind='maintenance', start=start, end=end, **facts)]})

    def test_waits_for_every_enabled_boss_and_does_not_modify_snapshot(self):
        self.assertFalse(synchronize_schedule(self.store, self.snapshot))
        self.fill()
        self.snapshot['input_entries'].pop()
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(self.notices(), [])
        self.fill()
        before = deepcopy(self.snapshot)
        self.assertTrue(synchronize_schedule(self.store, self.snapshot))
        event, = self.notices()
        self.assertEqual(event['tts_text'], MESSAGE)
        self.assertEqual(event['policy'], 'once')
        self.assertEqual(event['trigger'], 'immediate')
        self.assertTrue(event['retire_after_delivery'])
        self.assertIsNotNone(self.store.delivery_token(event['id']))
        self.assertEqual(self.snapshot, before)

    def test_old_future_recurrence_and_invasion_do_not_prove_new_input(self):
        self.fill()
        row = self.snapshot['input_entries'][1]
        row.update(created_at='2026-10-16T23:00:00+09:00', scheduled_at='2026-10-18T15:00:00+09:00')
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(self.notices(), [])
        row['created_at'] = '2026-10-17T11:10:00+09:00'
        row['is_invasion'] = True
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(self.notices(), [])
        row.pop('is_invasion')
        row['state'] = 'active'
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(len(self.notices()), 1)

    def test_missing_host_facts_or_empty_catalog_never_claims_completion(self):
        self.fill()
        for changes in [dict(input_boss_names=[]), dict(input_entries=[]), dict(input_entries=[dict(boss_name='A')])]:
            synchronize_schedule(self.store, dict(self.snapshot, **changes))
            self.assertEqual(self.notices(), [])

    def test_not_before_open_and_no_repeated_revision_when_inputs_change(self):
        self.fill()
        self.now = self.now.replace(hour=10)
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(self.notices(), [])
        self.now = self.now.replace(hour=12)
        synchronize_schedule(self.store, self.snapshot)
        before, = self.notices()
        self.snapshot['input_entries'][0]['created_at'] = self.now.isoformat()
        self.assertFalse(synchronize_schedule(self.store, self.snapshot))
        self.assertEqual(self.notices()[0]['revision'], before['revision'])

    def test_complete_then_retire_and_never_recreate_after_restart(self):
        self.fill()
        synchronize_schedule(self.store, self.snapshot)
        event, = self.notices()
        token = self.store.delivery_token(event['id'])
        # Obtaining/queuing a token is not a delivery completion.
        self.assertFalse(self.notices()[0].get('retired_at'))
        self.assertTrue(self.store.complete_delivery(token))
        self.assertIsNone(self.store.delivery_token(event['id']))
        self.assertTrue(self.notices()[0]['retired_at'])
        self.store = NoticeStore(self.store.root, 'test-server', clock=lambda: self.now)
        self.assertFalse(synchronize_schedule(self.store, self.snapshot))
        self.assertEqual(len(self.notices()), 1)
        self.assertTrue(self.notices()[0]['retired_at'])

    def test_removing_input_blocks_pending_delivery_and_restore_releases_hold(self):
        self.fill()
        synchronize_schedule(self.store, self.snapshot)
        event, = self.notices()
        token = self.store.delivery_token(event['id'])
        self.snapshot['input_entries'].pop()
        synchronize_schedule(self.store, self.snapshot)
        self.assertIsNone(self.store.delivery_token(event['id']))
        self.assertFalse(self.store.complete_delivery(token))
        self.fill()
        synchronize_schedule(self.store, self.snapshot)
        self.assertIsNotNone(self.store.delivery_token(event['id']))

    def test_all_maintenance_types_and_extensions_use_final_opening(self):
        self.fill()
        for title in ['정기점검 안내', '임시 점검 안내', '긴급 점검 안내']:
            state = self.store.snapshot()
            state['articles'] = {'base': self.article(title),
                                 'extended': self.article(title + ' 연장', end='2026-10-17T12:30:00+09:00')}
            first, opened, end = opening_cycle(self.snapshot, state, self.now)
            self.assertEqual(first.hour, 8)
            self.assertEqual((opened.hour, opened.minute), (12, 30))
            with self.store._transaction() as saved:
                saved['articles'] = state['articles']
            synchronize_schedule(self.store, self.snapshot)
            self.assertEqual(self.notices(), [])
        self.now = self.now.replace(hour=13)
        self.snapshot['input_entries'] = [dict(boss_name=name, created_at=self.now.isoformat()) for name in ['A', 'B']]
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(len(self.notices()), 1)

    def test_conflicting_or_unknown_maintenance_blocks_local_fallback(self):
        state = self.store.snapshot()
        state['articles'] = {'base': self.article('점검'), 'other': self.article('점검', end='2026-10-17T11:45:00+09:00')}
        self.assertIsNone(opening_cycle(self.snapshot, state, self.now))
        state['articles'] = {'unknown': self.article('임시점검', end=None, issue='종료 시각 불명')}
        self.assertIsNone(opening_cycle(self.snapshot, state, self.now))

    def test_regular_open_control_does_not_bypass_extension_or_change_cycle_id(self):
        state = self.store.snapshot()
        self.snapshot['control_events'] = [dict(control_type='server_open', scheduled_at=self.snapshot['server_open'])]
        state['articles'] = {'extension': self.article('점검 연장', end='2026-10-17T13:00:00+09:00')}
        first, opened, end = opening_cycle(self.snapshot, state, self.now)
        self.assertEqual(first.hour, 8)
        self.assertEqual(opened.hour, 13)

    def test_second_open_after_temporary_maintenance_creates_new_one_shot(self):
        self.fill()
        synchronize_schedule(self.store, self.snapshot)
        event, = self.notices()
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token(event['id'])))
        self.now = self.now.replace(hour=16)
        # Intentionally unordered controls, like restored/imported data.
        self.snapshot['control_events'] = [dict(control_type='server_open', scheduled_at='2026-10-17T15:00:00+09:00'),
            dict(control_type='temporary_maintenance', scheduled_at='2026-10-17T14:00:00+09:00')]
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(len(self.notices()), 1)  # Previous input cannot satisfy the new cycle.
        for row in self.snapshot['input_entries']:
            row['created_at'] = self.now.isoformat()
        synchronize_schedule(self.store, self.snapshot)
        newer = [e for e in self.notices() if not e.get('retired_at')]
        self.assertEqual(len(newer), 1)
        self.assertNotEqual(newer[0]['id'], event['id'])

    def test_start_only_and_future_open_hold_old_pending_without_discarding_it(self):
        self.fill()
        synchronize_schedule(self.store, self.snapshot)
        event, = self.notices()
        with self.store._transaction() as state:
            state['articles'] = {'extension': self.article('점검 연장', end='2026-10-17T13:00:00+09:00')}
        synchronize_schedule(self.store, self.snapshot)
        self.assertIsNone(self.store.delivery_token(event['id']))
        self.assertFalse(self.notices()[0].get('retired_at'))
        self.now = self.now.replace(hour=14)
        for row in self.snapshot['input_entries']:
            row['created_at'] = self.now.isoformat()
        synchronize_schedule(self.store, self.snapshot)
        self.assertIsNotNone(self.store.delivery_token(event['id']))

    def test_switches_and_wrong_server_are_preserved(self):
        self.fill()
        settings = self.store.snapshot()['settings']
        settings['categories']['maintenance'] = False
        self.store.configure(settings)
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(self.notices(), [])
        settings['categories']['maintenance'] = True
        settings['output_enabled'] = False
        self.store.configure(settings)
        synchronize_schedule(self.store, self.snapshot)
        event, = self.notices()
        self.assertIsNone(self.store.delivery_token(event['id']))
        self.assertFalse(synchronize_schedule(self.store, dict(self.snapshot, server_id='another')))

    def test_waits_for_web_receipt_without_using_a_fixed_delay(self):
        self.fill()
        self.snapshot['web_schedule_published'] = False
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(self.notices(), [])
        self.now += timedelta(minutes=10)
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(self.notices(), [])
        self.snapshot['web_schedule_published'] = True
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(len(self.notices()), 1)

    def test_real_audio_controller_retries_failure_and_retires_only_after_eof(self):
        from notice_module.payload.notice_audio import NoticeAudioController
        from test_notice_audio import FakeTransport
        self.fill()
        synchronize_schedule(self.store, self.snapshot)
        seconds = [0.0]
        transport = FakeTransport()
        controller = NoticeAudioController(self.store, transport, clock=lambda: seconds[0])
        self.addCleanup(controller.close)
        controller.step(boss_busy=False)
        handle = controller.active['handle']
        transport.states[handle] = 'failed'
        controller.step(boss_busy=False)
        self.assertFalse(self.notices()[0].get('retired_at'))
        seconds[0] = 31.0
        controller.step(boss_busy=False)
        handle = controller.active['handle']
        transport.states[handle] = 'ready'
        controller.step(boss_busy=False)
        self.assertFalse(self.notices()[0].get('retired_at'))
        transport.states[handle] = 'completed'
        controller.step(boss_busy=False)
        self.assertTrue(self.notices()[0]['retired_at'])
        controller.step(boss_busy=False)
        self.assertIsNone(controller.active)
        self.assertEqual(sum(call[0] == 'prepare' for call in transport.calls), 2)


if __name__ == '__main__':
    unittest.main()
