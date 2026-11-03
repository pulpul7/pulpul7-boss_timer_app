"""Dawn/morning selection and one-output completion, with no live output."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from notice_module.payload.notice_management import NoticeStore, KST
from notice_module.payload.notice_schedule import schedule_notices, synchronize_schedule
from notice_module.payload.notice_templates import TEMPLATES


class SchedulePairingTests(unittest.TestCase):
    def setUp(self):
        defaults = patch('notice_module.payload.notice_defaults.bundled_preferences', return_value=None)
        defaults.start()
        self.addCleanup(defaults.stop)
        temporary = tempfile.TemporaryDirectory(prefix='notice-pair-unit-')
        self.addCleanup(temporary.cleanup)
        self.now = datetime(2026, 11, 3, 21, tzinfo=KST)
        self.store = NoticeStore(Path(temporary.name), 'server', clock=lambda: self.now)
        settings = self.store.snapshot()['settings']
        settings['output_enabled'] = True
        self.store.configure(settings)
        self.snapshot = dict(server_id='server', season='18', events=[
            self.row('최하층강글', 4, 52, area='던전'),
            self.row('히로킨', 6, 33, area='니플하임'),
            self.row('스카디', 6, 35, area='바나하임'),
            self.row('헤이드', 22, 32, tomorrow=False, area='니플하임')])

    def row(self, name, hour, minute=0, *, tomorrow=True, second=0, **facts):
        at = self.now.replace(hour=hour, minute=minute, second=second) + timedelta(days=int(tomorrow))
        return dict(boss_name=name, scheduled_at=at.isoformat(), precision='second', star=True, **facts)

    def refresh(self):
        synchronize_schedule(self.store, self.snapshot)
        return self.store.snapshot()

    def event(self, suffix):
        return next(event for event in self.store.snapshot()['events'].values() if event['id'].endswith('/' + suffix))

    def context(self, identity='dawn-trigger'):
        return dict(id=identity, server_id='server', at=self.now.isoformat(), kind='boss', star=True,
                    boss_name='헤이드', chapter=7, phase='one_minute_complete',
                    scheduled_at=self.now.replace(hour=22, minute=32).isoformat())

    def test_two_records_one_prepared_text_and_both_times_preserved(self):
        before = deepcopy(self.snapshot)
        self.refresh()
        dawn, morning = self.event('overnight'), self.event('morning')
        self.assertEqual(dawn['tts_text'],
            '내일 새벽 4시 52분 최하층강글, 아침 6시 33분 히로킨, 스카디 일정이 있습니다. 많은 참여 부탁드립니다.')
        self.assertNotIn('최하층강글', morning['tts_text'])
        self.assertEqual(len(dawn['tts_values']['_boss_entries']), 1)
        self.assertEqual(len(morning['tts_values']['_boss_entries']), 2)
        requests = self.store.preparation_candidates()
        self.assertEqual([request['id'] for request in requests], [dawn['id']])
        self.assertEqual(self.snapshot, before)
        state = self.store.snapshot()
        self.assertFalse(synchronize_schedule(self.store, self.snapshot))
        self.assertEqual(self.store.snapshot(), state)

    def test_confirmed_completion_atomically_consumes_both_and_survives_reload(self):
        self.refresh()
        self.now = self.now.replace(hour=22, minute=31)
        dawn, morning = self.event('overnight'), self.event('morning')
        self.assertIsNone(self.store.delivery_token(morning['id'], opportunity=self.context()))
        token = self.store.delivery_token(dawn['id'], opportunity=self.context())
        self.assertIsNotNone(token)
        self.assertFalse(self.store.snapshot()['events'][morning['id']].get('last_delivery'))
        self.assertTrue(self.store.complete_delivery(token))
        state = self.store.snapshot()
        self.assertEqual({row['event_id'] for row in state['delivery_ledger']}, {dawn['id'], morning['id']})
        self.assertTrue(state['events'][morning['id']]['retired_at'])
        self.assertTrue(state['events'][dawn['id']]['retired_at'])
        combined_text = state['events'][dawn['id']]['tts_text']
        self.store = NoticeStore(self.store.root, 'server', clock=lambda: self.now)
        self.refresh()
        self.assertIsNone(self.store.delivery_token(dawn['id'], opportunity=self.context('retry')))
        self.assertIsNone(self.store.delivery_token(morning['id'], opportunity=self.context('retry')))
        self.assertEqual(len(self.store.snapshot()['delivery_ledger']), 2)
        self.assertEqual(self.event('overnight')['tts_text'], combined_text)

    def test_one_audio_play_then_no_morning_replay_at_the_next_boss(self):
        from notice_module.payload.notice_audio import NoticeAudioController
        from test_notice_audio import FakeTransport
        self.refresh()
        self.now = self.now.replace(hour=22, minute=31)
        transport = FakeTransport()
        controller = NoticeAudioController(self.store, transport)
        self.addCleanup(controller.close)
        controller.step(boss_busy=False, opportunity=self.context())
        handle = controller.active['handle']
        transport.states[handle] = 'ready'
        controller.step(boss_busy=False, opportunity=self.context())
        self.assertIsNone(self.event('morning')['last_delivery'])
        transport.states[handle] = 'completed'
        controller.step(boss_busy=False, opportunity=self.context())
        self.now = self.now.replace(minute=33)
        controller.step(boss_busy=False, opportunity=self.context('next-boss'))
        self.assertIsNone(controller.active)
        self.assertEqual(sum(action == 'prepare' for action, _ in transport.calls), 1)
        self.assertEqual(sum(action == 'play' for action, _ in transport.calls), 1)
        self.assertIsNotNone(self.event('morning')['last_delivery'])

    def test_existing_morning_quota_is_not_bypassed_by_pairing(self):
        with self.store._transaction() as state:
            state['delivery_ledger'] = [dict(group='participation', event_id=f'old-{i}', opportunity_id=f'old-{i}',
                at=(self.now - timedelta(hours=3-i)).isoformat()) for i in range(2)]
        self.refresh()
        self.assertNotIn('_morning_pair', self.event('overnight')['tts_values'])

    def test_23_timer_fallback_includes_morning_and_does_not_expire_it_early(self):
        self.snapshot['events'] = self.snapshot['events'][:-1]
        self.refresh()
        self.now = self.now.replace(hour=23)
        self.refresh()
        dawn, morning = self.event('overnight'), self.event('morning')
        self.assertIn('아침 6시 33분', dawn['tts_text'])
        self.assertFalse(morning.get('retired_at'))
        token = self.store.delivery_token(dawn['id'])
        self.assertIsNotNone(token)
        self.assertTrue(self.store.complete_delivery(token))
        self.assertTrue(self.store.snapshot()['events'][morning['id']]['last_delivery'])

    def test_disabling_either_scenario_preserves_the_other_as_standalone(self):
        for disabled, suffix in [('participation.morning', 'overnight'), ('participation.dawn', 'morning')]:
            with self.subTest(disabled=disabled):
                self.refresh()
                state = self.store.snapshot()
                flags = dict.fromkeys(TEMPLATES, True)
                flags[disabled] = False
                self.store.save_tts_templates({key: item['text'] for key, item in TEMPLATES.items()},
                                              expected_revision=state.get('tts_templates_revision', 0), enabled=flags)
                self.refresh()
                event = self.event(suffix)
                self.assertNotIn('_morning_pair', event['tts_values'])
                self.assertNotIn('_paired_dawn', event['tts_values'])
                self.now = self.now.replace(hour=22, minute=31)
                self.assertIsNotNone(self.store.delivery_token(event['id'], opportunity=self.context()))

    def test_individual_disable_invalidates_old_bundle_token(self):
        self.refresh()
        self.now = self.now.replace(hour=22, minute=31)
        dawn, morning = self.event('overnight'), self.event('morning')
        token = self.store.delivery_token(dawn['id'], opportunity=self.context())
        self.store.set_enabled(morning['id'], False)
        self.assertFalse(self.store.complete_delivery(token))
        self.refresh()
        dawn = self.event('overnight')
        self.assertNotIn('아침', dawn['tts_text'])
        self.assertIsNotNone(self.store.delivery_token(dawn['id'], opportunity=self.context()))

    def test_editing_morning_during_playback_cannot_acknowledge_stale_bundle(self):
        self.refresh()
        self.now = self.now.replace(hour=22, minute=31)
        dawn, morning = self.event('overnight'), self.event('morning')
        token = self.store.delivery_token(dawn['id'], opportunity=self.context())
        self.store.edit_tts(morning['id'], '개인 아침 안내')
        self.assertFalse(self.store.complete_delivery(token))
        self.assertEqual(self.store.snapshot().get('delivery_ledger', []), [])
        self.refresh()
        self.assertEqual(self.event('morning')['tts_text'], '개인 아침 안내')
        self.assertNotIn('_morning_pair', self.event('overnight')['tts_values'])

    def test_dawn_only_and_morning_only_remain_independent(self):
        for kind in ('overnight', 'morning'):
            with self.subTest(kind=kind):
                events = [self.snapshot['events'][0 if kind == 'overnight' else 1]]
                candidates = schedule_notices(dict(self.snapshot, events=events), self.store.snapshot(), self.now)
                self.assertEqual(len(candidates), 1)
                self.assertTrue(candidates[0]['id'].endswith('/' + kind))
                self.assertNotIn('_morning_pair', candidates[0]['tts_values'])

    def test_daily_half_open_boundaries_and_duplicate_inputs_never_overlap(self):
        for weekday in range(7):
            with self.subTest(weekday=weekday):
                self.now = datetime(2026, 11, 2, 21, tzinfo=KST) + timedelta(days=weekday)
                rows = [self.row('last-dawn', 5, 59, second=59), self.row('first-morning', 6),
                        self.row('last-morning', 7, 59, second=59), self.row('outside', 8)]
                rows[0]['scheduled_at'] = datetime.fromisoformat(rows[0]['scheduled_at']).replace(microsecond=999999).isoformat()
                candidates = schedule_notices(dict(self.snapshot, events=rows + rows), self.store.snapshot(), self.now)
                dawn = next(event for event in candidates if event['tts_template'] == 'participation.dawn')
                morning = next(event for event in candidates if event['tts_template'] == 'participation.morning')
                dawn_names = [row['name'] for row in dawn['tts_values']['_boss_entries']]
                morning_names = [row['name'] for row in morning['tts_values']['_boss_entries']]
                self.assertEqual(dawn_names, ['last-dawn'])
                self.assertEqual(morning_names, ['first-morning', 'last-morning'])
                self.assertFalse(set(dawn_names) & set(morning_names))

    def test_confirmed_maintenance_cuts_off_morning_targets(self):
        self.snapshot['regular_maintenance'] = '2026-11-04T07:00:00+09:00'
        self.snapshot['events'] += [self.row('at-maintenance', 7), self.row('after-maintenance', 7, 30)]
        self.refresh()
        self.assertEqual([row['name'] for row in self.event('morning')['tts_values']['_boss_entries']], ['히로킨', '스카디'])

    def test_fractional_morning_time_keeps_pair_signature_valid_after_store_roundtrip(self):
        self.snapshot['events'][1]['scheduled_at'] = datetime.fromisoformat(
            self.snapshot['events'][1]['scheduled_at']).replace(microsecond=1).isoformat()
        self.refresh()
        self.now = self.now.replace(hour=22, minute=31)
        token = self.store.delivery_token(self.event('overnight')['id'], opportunity=self.context())
        self.assertIsNotNone(token)
        self.assertTrue(self.store.complete_delivery(token))


if __name__ == '__main__':
    unittest.main()
