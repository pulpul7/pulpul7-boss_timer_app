"""Deterministic schedule projection/date speech tests. No audio/network/game."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
import ast

from notice_module.payload.notice_management import NoticeStore, KST
from notice_module.payload.notice_schedule import schedule_notices, synchronize_schedule
from notice_module.payload.notice_templates import render_template, migrate_date_templates


class DateSpeechTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 12, 31, 18, tzinfo=KST)
        self.values = {'시작시간': '12월 31일 18시 00분', '종료시간': '01월 01일 02시 30분',
                       '_start_date': '2026-12-31', '_end_date': '2027-01-01'}

    def test_four_fields_and_year_boundary_today_tomorrow(self):
        state = {'tts_templates': {'transfer.start': '{시작날짜} {시작시간}부터 {종료날짜} {종료시간}까지'}}
        before = deepcopy(self.values)
        self.assertEqual(render_template(state, 'transfer.start', self.values, now=self.now),
                         '오늘 18시부터 내일 2시 30분까지')
        self.assertEqual(self.values, before)

    def test_clock_only_and_same_day_date_omission(self):
        state = {'tts_templates': {'transfer.start': '{시작시간} / {종료시간}'}}
        self.assertEqual(render_template(state, 'transfer.start', self.values, now=self.now), '18시 / 2시 30분')
        values = dict(self.values, 종료시간='12월 31일 23시 00분', _end_date='2026-12-31')
        state['tts_templates']['transfer.start'] = '{시작날짜} {시작시간}부터 {종료날짜} {종료시간}까지'
        self.assertEqual(render_template(state, 'transfer.start', values, now=self.now), '오늘 18시부터 23시까지')

    def test_legacy_custom_template_migration_once_preserves_literals(self):
        state = {'generation': 0, 'tts_templates': {'transfer.start': '{{시작시간}} {시작시간} ~ {종료시간}'}}
        migrate_date_templates(state)
        before = deepcopy(state)
        migrate_date_templates(state)
        self.assertEqual(state, before)
        self.assertEqual(render_template(state, 'transfer.start', self.values, now=self.now),
                         '{시작시간} 오늘 18시 ~ 내일 2시 30분')


class ScheduleNoticeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='boss-notice-schedule-unit-')
        self.addCleanup(temp.cleanup)
        self.now = datetime(2026, 9, 22, 18, tzinfo=KST)  # Tuesday
        self.store = NoticeStore(Path(temp.name), 'odin9', clock=lambda: self.now)
        settings = self.store.snapshot()['settings']
        settings['output_enabled'] = True
        self.store.configure(settings)
        self.snapshot = dict(server_id='odin9', season='18', events=[], regular_maintenance='2026-09-23T08:00:00+09:00')

    def row(self, name, when, **changes):
        item = dict(boss_name=name, scheduled_at='2026-09-' + when + '+09:00',
                    precision='minute', area='니플하임', absolute=False, star=True)
        item.update(changes)
        return item

    def planned(self):
        return schedule_notices(self.snapshot, self.store.snapshot(), self.now)

    def test_evening_and_dawn_boundaries_and_confirmed_only(self):
        self.snapshot['events'] = [self.row('before', '22T20:59:59'), self.row('start', '22T21:00:00'),
            self.row('midnight', '23T00:00:00'), self.row('gap', '23T00:00:00.500000'),
            self.row('dawn', '23T00:00:01'), self.row('last', '23T05:59:59'),
            self.row('hour', '22T22:00:00', precision='hour'), self.row('other', '22T22:00:00', area='아스가르드', star=False),
            self.row('absolute', '22T22:00:00', area='요툰하임', absolute=True),
            self.row('invasion', '22T22:00:00', is_invasion=True)]
        planned = self.planned()
        nights = [item for item in planned if item['tts_template'] == 'participation.dawn']
        self.assertEqual([e['name'] for e in nights[0]['tts_values']['_boss_entries']], ['dawn', 'last'])
        evening = [item for item in planned if item['tts_values'].get('_slot') in {'evening_first', 'evening_second'}]
        self.assertEqual(len(evening), 2)
        self.assertTrue(all(item['tts_values']['알림제목'] == 'start' for item in evening))
        self.assertNotIn('other', evening[0]['body'])
        self.assertIn('midnight', evening[0]['body'])

    def test_dungeon_dawn_and_maintenance_before_not_after(self):
        self.snapshot['events'] = [self.row('최하층강글', '23T02:30:00', area='던전'),
            self.row('최하층굴베', '23T06:30:00', area='던전'), self.row('morning', '23T07:30:00'),
            self.row('at-maintenance', '23T08:00:00')]
        plan = self.planned()[0]
        self.assertEqual([e['name'] for item in self.planned() for e in item['tts_values']['_boss_entries']],
                         ['최하층 강글', '최하층 굴베', 'morning'])
        synchronize_schedule(self.store, self.snapshot)
        event = self.store.snapshot()['events'][plan['id']]
        self.assertIn('내일 새벽 2시 30분 최하층 강글', event['tts_text'])
        morning = next(item for item in self.store.snapshot()['events'].values() if item['tts_template'] == 'participation.morning')
        self.assertIn('내일 아침 6시 30분 최하층 굴베', morning['tts_text'])
        self.snapshot['regular_maintenance'] = '2026-09-23T07:00:00+09:00'
        self.assertNotIn('morning', [e['name'] for item in self.planned() for e in item['tts_values']['_boss_entries']])

    def test_notice_7am_overrides_local_8am(self):
        self.store.register(dict(id='maintenance', title='정기점검 안내', category='maintenance', tts_text='점검',
            valid_from=self.now, valid_until=self.now + timedelta(days=1),
            event_from='2026-09-23 07:00', event_until='2026-09-23 11:00'))
        self.snapshot['events'] = [self.row('yes', '23T06:30:00'), self.row('no', '23T07:30:00')]
        self.assertEqual([e['name'] for e in self.planned()[0]['tts_values']['_boss_entries']], ['yes'])

    def test_dawn_and_morning_switches_are_independent_even_on_same_day(self):
        from notice_module.payload.notice_templates import TEMPLATES
        self.snapshot['events'] = [self.row('새벽보스', '23T02:30:00'), self.row('아침보스', '23T06:30:00')]
        synchronize_schedule(self.store, self.snapshot)
        state = self.store.snapshot()
        flags = dict.fromkeys(TEMPLATES, True)
        flags['participation.morning'] = False
        self.store.save_tts_templates({key: item['text'] for key, item in TEMPLATES.items()},
                                     expected_revision=state.get('tts_templates_revision', 0), enabled=flags)
        planned = self.planned()
        self.now = self.now.replace(hour=23)
        context = dict(id='one', server_id='odin9', at=self.now, kind='boss', chapter=7, star=True, phase='one_minute_complete')
        for item in planned:
            token = self.store.delivery_token(item['id'], opportunity=context)
            self.assertEqual(token is not None, item['tts_template'] == 'participation.dawn')

    def test_old_combined_notice_split_keeps_manual_disable_and_text(self):
        self.snapshot['events'] = [self.row('새벽보스', '23T02:30:00'), self.row('아침보스', '23T06:30:00')]
        dawn, morning = self.planned()
        legacy = deepcopy(dawn)
        legacy['tts_values']['_boss_entries'] += morning['tts_values']['_boss_entries']
        legacy['event_until'] = morning['event_until']
        self.store.register(legacy, collected=True)
        self.store.edit_tts(legacy['id'], '예전에 편집한 합동 안내')
        self.store.set_enabled(legacy['id'], False)
        synchronize_schedule(self.store, self.snapshot)
        for item in self.store.snapshot()['events'].values():
            self.assertFalse(item['enabled'])
            self.assertEqual(item['tts_text'], '예전에 편집한 합동 안내')
            self.assertTrue(item['tts_review_required'])

    def test_discarded_old_overnight_group_does_not_reappear_as_morning(self):
        self.snapshot['events'] = [self.row('새벽보스', '23T02:30:00'), self.row('아침보스', '23T06:30:00')]
        dawn, morning = self.planned()
        self.store.register(dawn, collected=True)
        self.store.discard(dawn['id'])
        synchronize_schedule(self.store, self.snapshot)
        self.assertNotIn(morning['id'], self.store.snapshot()['events'])

    def test_wednesday_morning_is_not_added_on_other_days(self):
        self.now = self.now.replace(day=21)
        self.snapshot['regular_maintenance'] = '2026-09-22T08:00:00+09:00'
        self.snapshot['events'] = [self.row('morning', '22T06:30:00')]
        self.assertEqual(self.planned(), [])

    def test_registration_idempotence_disable_edit_remove_and_reappear(self):
        self.snapshot['events'] = [self.row('히로킨', '22T22:00:00')]
        self.assertTrue(synchronize_schedule(self.store, self.snapshot))
        self.assertFalse(synchronize_schedule(self.store, self.snapshot))
        key = self.planned()[0]['id']
        self.store.edit_tts(key, '직접 편집')
        self.store.set_enabled(key, False)
        synchronize_schedule(self.store, self.snapshot)
        self.assertEqual(self.store.snapshot()['events'][key]['tts_text'], '직접 편집')
        self.assertFalse(self.store.snapshot()['events'][key]['enabled'])
        saved = self.snapshot['events']
        self.snapshot['events'] = []
        synchronize_schedule(self.store, self.snapshot)
        self.assertTrue(self.store.snapshot()['events'][key]['analysis_hold'])
        self.snapshot['events'] = saved
        synchronize_schedule(self.store, self.snapshot)
        self.assertFalse(self.store.snapshot()['events'][key]['analysis_hold'])
        self.assertFalse(self.store.snapshot()['events'][key]['enabled'])
        self.store.reconcile_sources([], successful=True)
        self.assertFalse(self.store.snapshot()['events'][key].get('retired_at'))

    def test_server_isolation_wrong_source_and_quiet_hours(self):
        self.snapshot['events'] = [self.row('히로킨', '23T02:00:00')]
        self.assertFalse(synchronize_schedule(self.store, dict(self.snapshot, server_id='odin8')))
        synchronize_schedule(self.store, self.snapshot)
        key = self.planned()[0]['id']
        context = dict(id='boss1', server_id='odin9', at=self.now, phase='one_minute_complete', kind='fixed', fixed_kind='world_boss')
        self.assertIsNone(self.store.delivery_token(key, opportunity=context))
        context.update(kind='boss', chapter=7, major=False, star=True)
        self.assertIsNone(self.store.delivery_token(key, opportunity=context))
        self.now = self.now.replace(hour=23)
        self.assertIsNotNone(self.store.delivery_token(key))  # New dawn fallback, independent of old 23:00 quiet rule.
        self.now = self.now.replace(minute=2)
        self.assertEqual(self.planned(), [])
        self.assertIsNone(self.store.delivery_token(key, opportunity=context))

    def test_relative_day_refresh_does_not_replay_completed_notice(self):
        self.store.register(dict(id='relative', title='기간', category='general',
            tts_template='transfer.start', tts_values={'종료시간': '09월 24일 18시 00분', '_end_date': '2026-09-24'},
            valid_from=self.now, valid_until=self.now + timedelta(days=3)))
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token('relative')))
        self.now += timedelta(days=1)
        self.assertIn('내일 18시', self.store.snapshot()['events']['relative']['tts_text'])
        self.assertIsNone(self.store.delivery_token('relative'))

    def test_incomplete_old_date_is_held_without_blocking_management(self):
        self.store.register(dict(id='incomplete', title='기간', category='general',
            tts_text='기존 문장', valid_from=self.now, valid_until=self.now + timedelta(days=1)))
        with self.store._transaction() as state:
            state['events']['incomplete'].update(tts_template='transfer.start',
                tts_values={'종료시간': '18시', '_end_date': 'invalid-old-date'})
        event = self.store.snapshot()['events']['incomplete']
        self.assertIn('정보 확인 필요', event['analysis_hold'])
        self.assertEqual(event['tts_text'], '기존 문장')
        self.assertIsNone(self.store.delivery_token('incomplete'))

    def test_morning_and_manual_share_two_per_day_and_old_tokens_expire(self):
        self.snapshot['events'] = [self.row('히로킨', '23T06:30:00')]
        synchronize_schedule(self.store, self.snapshot)
        key = self.planned()[0]['id']
        def context(index):
            return dict(id=str(index), server_id='odin9', at=self.now,
                        phase='one_minute_complete', kind='boss', chapter=7, star=True)
        token = self.store.delivery_token(key, opportunity=context(1))
        self.assertTrue(self.store.complete_delivery(token))
        self.now += timedelta(hours=1)
        self.store.register(dict(id='guild', title='길던', category='participation', tts_text='길던 안내',
            valid_from=self.now, valid_until=self.now + timedelta(hours=4),
            event_from=self.now + timedelta(hours=3)))
        manual_context = dict(context(2), major=True)
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token('guild', opportunity=manual_context)))
        self.now += timedelta(hours=1)
        self.assertIsNone(self.store.delivery_token(key, opportunity=context(3)))

    def test_changed_schedule_invalidates_prepared_token(self):
        self.snapshot['events'] = [self.row('히로킨', '22T22:00:00')]
        synchronize_schedule(self.store, self.snapshot)
        key = self.planned()[0]['id']
        token = self.store.delivery_token(key, opportunity=dict(id='one', server_id='odin9', at=self.now,
            phase='one_minute_complete', kind='boss', chapter=7))
        self.assertIsNotNone(token)
        self.snapshot['events'] = []
        synchronize_schedule(self.store, self.snapshot)
        self.assertFalse(self.store.complete_delivery(token))


class ScheduleHostTests(unittest.TestCase):
    def test_gui_snapshot_is_readonly_and_handover_returns_no_schedule(self):
        # Compile only the adapter, without importing/starting the application.
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                      and node.name == '_get_notice_schedule_snapshot')
        namespace = {'datetime': datetime}
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'snapshot-adapter', 'exec'), namespace)
        when = datetime(2026, 9, 22, 22, 0, 0, 123456)
        app = SimpleNamespace(schedule_events=[dict(boss_name='히로킨', scheduled_at=when)],
            schedule_boss_definitions={'히로킨': {'area': '니플하임', 'absolute': False}},
            schedule_server_profile_id='odin9', current_season_no=18,
            _get_schedule_boss_definition_name=lambda row: row['boss_name'],
            _get_schedule_boss_display_name=lambda row, **kw: row['boss_name'],
            _infer_schedule_state_precision=lambda row: 'second',
            _is_schedule_invasion_item=lambda row: False,
            _get_schedule_reference_datetime=lambda: when,
            _get_schedule_maintenance_cutoff_datetime=lambda now: None)
        before = deepcopy(app.schedule_events)
        result = namespace['_get_notice_schedule_snapshot'](app)
        self.assertEqual(result['events'][0]['scheduled_at'], when.isoformat())
        self.assertEqual(app.schedule_events, before)
        app.discord_handover_busy = True
        self.assertIsNone(namespace['_get_notice_schedule_snapshot'](app))

    def test_plugin_snapshot_worker_and_stop_without_network_or_audio(self):
        from notice_module.payload.main import NoticePlugin
        host = SimpleNamespace(get_server=Mock(return_value=('odin9', '오9')),
            get_schedule_snapshot=Mock(return_value=None), call_later=Mock(return_value='timer'),
            cancel_later=Mock(), log=Mock(), data_root=Path('unused-mocked-path'))
        plugin = NoticePlugin(host)
        plugin.stopped = False
        with patch('notice_module.payload.main.threading.Thread') as worker:
            plugin._schedule_tick()
            worker.assert_not_called()  # Handover/no snapshot cannot launch work.
            host.get_schedule_snapshot.return_value = {'server_id': 'odin9', 'events': [], 'season': '18'}
            now = [0.0]
            plugin.change_gate.clock = lambda: now[0]
            plugin._schedule_tick()
            worker.assert_not_called()
            now[0] = 5.0
            plugin._schedule_tick()
            worker.return_value.start.assert_called_once()
            plugin.stop()
            host.cancel_later.assert_called_with('timer')
            host.call_later.reset_mock()
            plugin._schedule_tick()
            host.call_later.assert_not_called()


if __name__ == '__main__': unittest.main()
