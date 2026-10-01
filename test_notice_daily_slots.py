"""Offline daily-slot selection, completion quotas and one-shot imports."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from notice_module.payload.notice_management import NoticeStore, KST
from notice_module.payload.notice_schedule import synchronize_schedule
from notice_module.payload.notice_participation import evening_notices, event_priority, important, first_opportunity
from notice_module.payload.notice_maintenance_import import import_temporary_maintenance
from notice_module.payload.notice_analysis import analyze_notice


class DailySlotTests(unittest.TestCase):
    def setUp(self):
        # Neutral baseline for algorithm tests; packaged defaults are tested separately.
        defaults = patch('notice_module.payload.notice_defaults.bundled_preferences', return_value=None)
        defaults.start()
        self.addCleanup(defaults.stop)
        temp = tempfile.TemporaryDirectory(prefix='notice-daily-slot-unit-')
        self.addCleanup(temp.cleanup)
        self.now = datetime(2026, 9, 22, 18, tzinfo=KST)
        self.store = NoticeStore(Path(temp.name), 'odin9', clock=lambda: self.now)
        settings = self.store.snapshot()['settings']
        settings['output_enabled'] = True
        self.store.configure(settings)
        self.snapshot = dict(server_id='odin9', season='18', events=[])

    def row(self, name, hour, minute=0, **extra):
        return dict(boss_name=name, scheduled_at=self.now.replace(hour=hour, minute=minute).isoformat(),
                    precision='minute', **extra)

    def refresh(self):
        synchronize_schedule(self.store, self.snapshot)

    def event(self, slot):
        return next(item for item in self.store.snapshot()['events'].values() if item.get('tts_values', {}).get('_slot') == slot)

    def context(self, hour, **extra):
        value = dict(id='boss-' + str(hour), server_id='odin9', at=self.now,
                     phase='one_minute_complete', kind='fixed', fixed_kind='world_boss',
                     scheduled_at=self.now.replace(hour=hour, minute=0).isoformat())
        value.update(extra)
        return value

    def test_importance_uses_stars_and_six_exact_events_not_just_chapter_or_absolute(self):
        self.assertFalse(important(dict(boss_name='오딘', area='니플하임', absolute=True)))
        self.assertTrue(important(dict(boss_name='프레이', area='니다벨리르', star=True)))
        for name in ('공성전', '트리니트리그', '트리니티 리그', '방어전', '점령전', '지옥성체', '지옥 성채', '길던', '길드던전'):
            self.assertTrue(important(dict(boss_name=name)), name)
        for name in ('공성전 입찰시작', '공성전 공격대상 선택', '점령전 신청', '방어전 신청마감'):
            self.assertFalse(important(dict(boss_name=name)), name)

    def test_event_priority_precedes_time_and_singular_plural_speech(self):
        self.snapshot['events'] = [self.row('길던', 21), self.row('공성전', 22), self.row('프레이', 21, star=True)]
        self.refresh()
        self.assertEqual(self.event('evening_first')['tts_text'],
            '오늘 저녁 22시 공성전 일정이 있습니다. 많은 참여 부탁드립니다.')
        self.snapshot['events'] = [self.row('프레이', 22, star=True)]
        self.refresh()
        self.assertEqual(self.event('evening_first')['tts_text'],
            '오늘 저녁 22시 프레이 일정이 있습니다. 많은 참여 부탁드립니다.')
        self.assertEqual([event_priority(name) for name in ('공성전', '트리니트리그', '방어전', '점령전', '지옥성체', '길던')], list(range(6)))

    def test_evening_window_counts_names_once_and_preserves_full_body(self):
        self.snapshot['events'] = [self.row('공성전', 22), self.row('프레이', 22, 10, star=True),
                                   self.row('오딘', 22, 29, star=True), self.row('제외보스', 22, 30, star=True)]
        self.refresh()
        event = self.event('evening_first')
        self.assertEqual(event['tts_text'], '오늘 저녁 22시 공성전 외 2개 일정이 있습니다. 많은 참여 부탁드립니다.')
        self.assertIn('제외보스', event['body'])
        self.assertEqual(self.event('evening_second')['tts_text'], event['tts_text'])

    def test_existing_name_and_time_template_uses_the_same_group(self):
        from notice_module.payload.notice_templates import render_template
        self.snapshot['events'] = [self.row('공성전', 22), self.row('프레이', 22, 10, star=True),
                                   self.row('오딘', 22, 29, star=True), self.row('제외보스', 22, 30, star=True)]
        self.refresh()
        event = self.event('evening_first')
        state = {'tts_templates': {'participation.general':
                 '오늘은 {시작시간}에 {알림제목} 일정이 있습니다. 많은 참여 부탁드립니다.'}}
        self.assertEqual(render_template(state, 'participation.general', event['tts_values'], now=self.now),
                         '오늘은 22시에 공성전 외 2개 일정이 있습니다. 많은 참여 부탁드립니다.')

    def test_chain_priority_then_highest_chapter_then_time(self):
        day = self.now.replace(hour=0)
        rows = [self.row('상위', 18, 10, area='바나하임'), self.row('연속1', 18, 30, area='무스펠하임'),
                self.row('연속2', 18, 31, area='무스펠하임')]
        self.assertEqual(first_opportunity(rows, day)['boss_name'], '연속1')
        self.assertEqual(first_opportunity(rows[:2], day)['boss_name'], '상위')
        self.assertEqual(first_opportunity([self.row('4챕', 18, 30, area='알브하임')], day)['kind'], 'timer')

    def test_first_at_18_once_and_second_only_after_20_world_boss(self):
        self.snapshot['events'] = [self.row('프레이', 23, star=True)]
        self.refresh()
        key = self.event('evening_first')['id']
        self.now -= timedelta(seconds=1)
        self.assertIsNone(self.store.delivery_token(key))
        self.now += timedelta(seconds=1)
        token = self.store.delivery_token(key)
        self.assertTrue(self.store.complete_delivery(token))
        self.assertFalse(self.store.complete_delivery(token))
        self.assertIsNone(self.store.delivery_token(key))
        # Restart and changed text cannot replay the first slot.
        self.store = NoticeStore(self.store.root, 'odin9', clock=lambda: self.now)
        self.snapshot['events'].append(self.row('길던', 22, 30))
        self.refresh()
        self.assertIsNone(self.store.delivery_token(key))
        self.now = self.now.replace(hour=19, minute=59)
        key2 = self.event('evening_second')['id']
        self.assertEqual(self.event('evening_second')['valid_from'], self.now.isoformat())
        self.assertEqual(self.event('evening_second')['valid_until'], self.now.replace(hour=20, minute=2).isoformat())
        self.assertIsNone(self.store.delivery_token(key2))
        self.assertIsNone(self.store.delivery_token(key2, opportunity=self.context(12)))
        self.assertIsNone(self.store.delivery_token(key2, opportunity=self.context(22)))
        self.assertIsNone(self.store.delivery_token(key2, opportunity=self.context(20, phase='started')))
        second = self.store.delivery_token(key2, opportunity=self.context(20))
        self.assertTrue(self.store.complete_delivery(second))
        self.assertIsNone(self.store.delivery_token(key2, opportunity=self.context(20, id='another')))
        self.assertEqual(len(self.store.snapshot()['delivery_ledger']), 2)
        self.now = self.now.replace(hour=21, minute=59)
        self.assertIsNone(self.store.delivery_token(key2, opportunity=self.context(22)))

    def test_first_boss_requires_actual_matching_completed_announcement(self):
        self.snapshot['events'] = [self.row('프레이', 23, star=True), self.row('방송보스', 18, 30, area='아스가르드')]
        self.refresh()
        key = self.event('evening_first')['id']
        self.assertIsNone(self.store.delivery_token(key))
        self.now = self.now.replace(minute=29)
        context = self.context(18, kind='boss', chapter=6, boss_name='방송보스', scheduled_at=self.now.replace(minute=30))
        self.assertIsNotNone(self.store.delivery_token(key, opportunity=context))
        self.assertIsNone(self.store.delivery_token(key, opportunity=dict(context, boss_name='다른보스')))

    def dawn_row(self):
        return dict(boss_name='스카디', star=True, precision='minute', area='바나하임',
                    scheduled_at=(self.now + timedelta(days=1)).replace(hour=3, minute=8).isoformat())

    def test_dawn_at_late_major_boss_once_and_not_at_23_fallback(self):
        self.snapshot['events'] = [self.dawn_row(), self.row('프레이', 23, 30, star=True, area='니플하임')]
        self.refresh()
        event = self.event('overnight_once')
        self.assertEqual(event['valid_from'], self.now.replace(hour=23, minute=29).isoformat())
        self.assertEqual(event['tts_values']['_slot_plan']['boss_name'], '프레이')
        self.assertIsNone(self.store.delivery_token(event['id']))
        self.now = self.now.replace(hour=23)
        self.assertIsNone(self.store.delivery_token(event['id']))
        self.now = self.now.replace(minute=29)
        context = self.context(23, kind='boss', chapter=7, boss_name='프레이',
                               scheduled_at=self.now.replace(minute=30))
        self.assertIsNone(self.store.delivery_token(event['id'], opportunity=dict(context, phase='started')))
        self.assertIsNone(self.store.delivery_token(event['id'], opportunity=dict(context, boss_name='다른 보스')))
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token(event['id'], opportunity=context)))
        self.refresh()
        self.assertIsNone(self.store.delivery_token(event['id'], opportunity=dict(context, id='retry')))

    def test_dawn_fallback_at_23_independent_of_two_evening_announcements(self):
        self.snapshot['events'] = [self.dawn_row()]
        self.refresh()
        event = self.event('overnight_once')
        self.assertEqual(event['trigger'], 'immediate')
        self.assertIn('23:00:00', event['valid_from'])
        with self.store._transaction() as state:
            state['delivery_ledger'] = [dict(at=self.now.isoformat(), event_id=slot, opportunity_id=slot,
                slot=slot, group='participation_evening') for slot in ('evening_first', 'evening_second')]
        self.now = self.now.replace(hour=22, minute=59, second=59)
        self.assertIsNone(self.store.delivery_token(event['id']))
        self.now = self.now.replace(hour=23, minute=0, second=0)
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token(event['id'])))
        self.store = NoticeStore(self.store.root, 'odin9', clock=lambda: self.now)
        self.refresh()
        self.assertIsNone(self.store.delivery_token(event['id']))

    def test_dawn_nonstar_chain_above_chapter4_is_eligible(self):
        self.snapshot['events'] = [self.dawn_row(), self.row('연타1', 22, 10, area='무스펠하임'),
                                   self.row('연타2', 22, 11, area='무스펠하임')]
        self.refresh()
        event = self.event('overnight_once')
        self.assertEqual(event['tts_values']['_slot_plan']['boss_name'], '연타1')
        self.now = self.now.replace(hour=22, minute=9)
        context = self.context(22, kind='boss', chapter=5, boss_name='연타1', scheduled_at=self.now.replace(minute=10))
        self.assertIsNotNone(self.store.delivery_token(event['id'], opportunity=context))
        self.snapshot['events'][1]['area'] = self.snapshot['events'][2]['area'] = '알브하임'
        self.refresh()
        self.assertEqual(self.event('overnight_once')['tts_values']['_slot_plan']['kind'], 'timer')

    def test_dawn_midnight_boss_uses_2359_on_previous_date(self):
        midnight = (self.now + timedelta(days=1)).replace(hour=0)
        self.snapshot['events'] = [self.dawn_row(), dict(self.row('자정 보스', 23, star=True), scheduled_at=midnight.isoformat())]
        self.refresh()
        key = self.event('overnight_once')['id']
        self.now = self.now.replace(hour=23, minute=59)
        context = self.context(0, kind='boss', boss_name='자정 보스', scheduled_at=midnight)
        self.assertIsNotNone(self.store.delivery_token(key, opportunity=context))
        self.now = midnight
        self.assertIsNone(self.store.delivery_token(key, opportunity=context))

    def test_dawn_old_policy_completion_prevents_migration_replay(self):
        self.snapshot['events'] = [self.dawn_row()]
        self.refresh()
        key = self.event('overnight_once')['id']
        with self.store._transaction() as state:
            state['delivery_ledger'] = [dict(at=self.now.isoformat(), event_id=key,
                                           opportunity_id='legacy', group='participation')]
        self.now = self.now.replace(hour=23)
        self.refresh()
        self.assertIsNone(self.store.delivery_token(key))

    def test_fractional_boss_time_is_preserved_for_matching(self):
        at = self.now.replace(minute=30, microsecond=123456)
        boss = self.row('정밀보스', 18, 30, area='아스가르드')
        boss['scheduled_at'] = at.isoformat()
        self.snapshot['events'] = [self.row('프레이', 23, star=True), boss]
        self.refresh()
        self.now = self.now.replace(minute=29, second=1)
        token = self.store.delivery_token(self.event('evening_first')['id'], opportunity=self.context(
            18, kind='boss', chapter=6, boss_name='정밀보스', scheduled_at=at))
        self.assertIsNotNone(token)

    def test_noon_siege_extra_once_even_if_outside_21_to_24(self):
        self.now = self.now.replace(hour=11, minute=59)
        self.snapshot['events'] = [self.row('공성전', 20), self.row('프레이', 23, star=True)]
        self.refresh()
        event = self.event('siege_noon')
        self.assertEqual(event['tts_text'], '오늘은 공성전이 있는 날입니다. 많은 참여 부탁드립니다.')
        self.assertIsNone(self.store.delivery_token(event['id'], opportunity=self.context(22)))
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token(event['id'], opportunity=self.context(12))))
        self.assertIsNone(self.store.delivery_token(event['id'], opportunity=self.context(12, id='duplicate')))
        self.now = self.now.replace(hour=18, minute=0)
        self.refresh()
        self.assertIsNotNone(self.store.delivery_token(self.event('evening_first')['id']))

    def test_remove_star_revokes_prepared_notice_and_manual_target_is_not_extra_audio(self):
        self.snapshot['events'] = [self.row('프레이', 23, star=True)]
        self.refresh()
        token = self.store.delivery_token(self.event('evening_first')['id'])
        self.snapshot['events'][0]['star'] = False
        self.refresh()
        self.assertFalse(self.store.complete_delivery(token))
        self.store.register(dict(id='manual-guild', title='길드던전', category='participation', tts_text='직접 쓴 문장',
            event_from=self.now.replace(hour=22), valid_from=self.now, valid_until=self.now.replace(hour=23)))
        self.refresh()
        self.assertEqual(self.event('evening_first')['tts_text'], '직접 쓴 문장')
        self.assertIsNone(self.store.delivery_token('manual-guild', opportunity=self.context(22)))
        key = self.event('evening_first')['id']
        self.assertIsNotNone(self.store.delivery_token(key))
        self.store.set_enabled('manual-guild', False)
        self.assertIsNone(self.store.delivery_token(key))  # Even before the next projection tick.


class MaintenanceImportTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='notice-maintenance-import-unit-')
        self.addCleanup(temp.cleanup)
        self.now = datetime(2026, 9, 22, 12, tzinfo=KST)
        self.store = NoticeStore(Path(temp.name), 'odin9', clock=lambda: self.now)
        self.snapshot = dict(server_id='odin9', season='18')
        self.apply = Mock(return_value='applied')
        self.article = dict(id='CT9G/1', title='임시점검 안내', category='maintenance', pinned=True,
            body='점검 일시: 2026년 9월 22일 14:00 ~ 15:00', published_date='2026-09-22', first_seen=self.now.isoformat())
        self.article['analysis'] = analyze_notice(self.article)
        self.put(self.article)

    def put(self, article):
        with self.store._transaction() as state:
            state.setdefault('articles', {})[article['id']] = deepcopy(article)

    def run_import(self):
        import_temporary_maintenance(self.store, self.snapshot, self.apply)

    def test_once_through_restart_recollection_edit_and_duplicate_article(self):
        self.run_import()
        self.store = NoticeStore(self.store.root, 'odin9', clock=lambda: self.now)
        self.put(self.article)
        self.put(dict(self.article, id='CT9G/2'))
        self.run_import()
        self.assertEqual(self.apply.call_count, 1)
        self.assertEqual(self.store.snapshot()['maintenance_imports']['CT9G/1']['status'], 'applied')
        changed = deepcopy(self.article)
        changed['analysis']['windows'][0]['start'] = '2026-09-22T16:00:00+09:00'
        changed['analysis']['windows'][0]['end'] = '2026-09-22T17:00:00+09:00'
        self.put(changed)
        self.run_import()
        self.assertEqual(self.apply.call_count, 1)

    def test_failure_conflict_and_pending_are_not_blindly_retried(self):
        for result in ('failed', 'conflict', 'existing', 'pending'):
            with self.subTest(result=result):
                with self.store._transaction() as state:
                    state['maintenance_imports'] = {}
                self.apply.reset_mock()
                self.apply.return_value = result
                self.run_import()
                self.run_import()
                self.assertEqual(self.apply.call_count, 1)

    def test_safe_defer_can_retry_and_wrong_server_never_imports(self):
        self.apply.return_value = 'deferred'
        self.run_import()
        self.assertFalse(self.store.snapshot()['maintenance_imports'])
        self.apply.return_value = 'applied'
        self.run_import()
        self.assertEqual(self.apply.call_count, 2)
        import_temporary_maintenance(self.store, dict(self.snapshot, server_id='odin8'), self.apply)
        self.assertEqual(self.apply.call_count, 2)

    def test_missing_period_unpinned_completed_and_regular_are_not_imported(self):
        for changes in ({'pinned': False}, {'title': '정기점검 안내'}, {'title': '임시점검 완료 안내'},
                        {'title': '임시점검 연장 안내'}, {'analysis': {'windows': []}}, {'body_error': 'error'}):
            self.put(dict(self.article, **changes))
            self.run_import()
        self.apply.assert_not_called()
