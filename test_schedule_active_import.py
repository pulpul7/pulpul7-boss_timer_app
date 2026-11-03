"""Active boss imports replace stale future chains without touching stored user data."""
from datetime import datetime, timedelta
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

import test_schedule_input_order as fixture


class ActiveScheduleImportTests(unittest.TestCase):
    def setUp(self):
        fixture.ScheduleInputOrderTests.setUp(self)
        self.now = datetime(2026, 10, 28, 13, 39, 17)
        self.app._get_schedule_reference_datetime = lambda: self.now
        self.app._get_schedule_respawn_seconds_for_state_item = lambda _: 72 * 3600
        self.name = '최하층스네르'
        self.key = 'normal:' + self.name
        # Use the real canonical-name normalizer, including colon stripping.
        del self.app._normalize_schedule_input_boss_name
        self.app._get_schedule_boss_lookup_map = lambda: {self.name:self.name}
        self.app._get_schedule_voice_boss_lookup_map = lambda: {}
        self.app._get_display_boss_name = lambda name: name

    def event(self, when, key=None):
        return dict(raw_key=key or self.key, boss_name=self.name, display_name=self.name,
                    scheduled_at=when, state='scheduled', created_at=self.now-timedelta(days=3))

    def active(self):
        return dict(raw_key=self.key, boss_name=self.name, display_name=self.name,
                    state='active', scheduled_at=None, created_at=self.now)

    def test_active_import_syntaxes_remove_the_old_future_chain(self):
        for text in (self.name+' 출현 중', self.name+': 출현중'):
            with self.subTest(text=text):
                old = self.event(self.now.replace(hour=16, minute=47, second=0))
                self.app.schedule_events = [old]
                self.app.schedule_active_entries = []
                fixture.ScheduleInputOrderTests.apply(self, text)
                self.assertEqual(self.app.schedule_events, [])
                self.assertEqual([row['raw_key'] for row in self.app.schedule_active_entries], [self.key])

    def test_past_other_bosses_and_invasions_are_preserved(self):
        past = self.event(self.now-timedelta(hours=1))
        future = self.event(self.now+timedelta(hours=3))
        next_cycle = self.event(self.now+timedelta(days=3))
        other = self.event(self.now+timedelta(hours=1), 'normal:other')
        invasion = self.event(self.now+timedelta(hours=2), 'invasion:'+self.name)
        self.app.schedule_events = [past, future, next_cycle, other, invasion]
        count = self.app._merge_schedule_import_batch(self.now-timedelta(days=1), self.now, [], [self.active()])
        self.assertEqual(self.app.schedule_events, [past, other, invasion])
        self.assertEqual(count, 2)

    def test_confirmation_counts_a_future_row_before_replacing_it_with_active(self):
        future = self.event(self.now.replace(hour=16, minute=47, second=0))
        self.app.schedule_events = [future]
        self.app._show_schedule_input_overwrite_confirm = Mock(return_value=False)
        self.app.schedule_input_status_var = Mock()
        items, ignored = self.app._parse_schedule_input_lines(self.name+' 출현 중', reference_datetime=self.now)
        result = self.app._apply_schedule_parsed_batch(self.name+' 출현 중', items, ignored, self.now, self.now,
                                                      store_input_undo_backup=False)
        self.assertFalse(result)
        self.app._show_schedule_input_overwrite_confirm.assert_called_once_with(1, is_edit_mode=False, is_add_mode=True)
        self.assertEqual(self.app.schedule_events, [future])
        self.assertEqual(self.app.schedule_active_entries, [])

    def test_scheduled_import_replaces_active_with_confirmed_time(self):
        for discord in (False, True):
            with self.subTest(discord=discord):
                self.app.schedule_active_entries = [self.active()]
                self.app.schedule_events = []
                fixture.ScheduleInputOrderTests.apply(self, '1647 '+self.name, discord)
                self.assertEqual(self.app.schedule_active_entries, [])
                self.assertEqual([row['scheduled_at'] for row in self.app.schedule_events],
                                 [self.now.replace(hour=16, minute=47, second=0)])

    def test_explicit_cutoff_and_empty_import_keep_their_existing_scope(self):
        early = self.event(self.now+timedelta(hours=1))
        late = self.event(self.now+timedelta(hours=3))
        self.app.schedule_events = [early, late]
        self.assertEqual(self.app._merge_schedule_import_batch(self.now, self.now, [], []), 0)
        self.assertEqual(self.app.schedule_events, [early, late])
        self.assertEqual(self.app._merge_schedule_import_batch(self.now, self.now, [], [self.active()],
            overwrite_cutoff_by_raw_key={self.key:self.now+timedelta(hours=2)}), 1)
        self.assertEqual(self.app.schedule_events, [early])

    def test_web_snapshot_after_import_keeps_active_without_old_scheduled_time(self):
        from test_web_boss_catalog import app_fixture
        from web_schedule_snapshot import collect_snapshot
        self.app.schedule_events = [self.event(self.now.replace(hour=16, minute=47, second=0))]
        fixture.ScheduleInputOrderTests.apply(self, self.name+' 출현 중')
        public, _ = app_fixture()
        public._get_schedule_reference_datetime = lambda: self.now
        public._get_schedule_visible_cutoff_datetime = lambda: None
        public.schedule_break_rows_enabled_var = SimpleNamespace(get=lambda:False)
        public.fixed_boss_entries = []
        public._collect_schedule_share_rows = lambda start, end, **kw: (
            [dict(row, kind='event', boss_text=row['boss_name']) for row in self.app.schedule_events], start, end)
        public.schedule_control_events = []
        public.schedule_active_entries = self.app.schedule_active_entries
        public._get_schedule_boss_display_name = lambda item, **kw:item['boss_name']
        public._schedule_boss_has_star = lambda item:True
        public.current_season_no = 18
        public.season_history_map = {}
        public._get_schedule_share_server_name = lambda:'test'
        snapshot = collect_snapshot(public)
        self.assertEqual(snapshot['events'], [])
        self.assertEqual([(row['name'], row['status']) for row in snapshot['active']], [(self.name, '출현 중')])

    def test_generation_extension_cannot_recreate_a_future_chain_for_active_boss(self):
        old_time = self.now.replace(hour=16, minute=47, second=0)-timedelta(days=3)
        past = dict(self.event(old_time), respawn_seconds=72*3600)
        other = dict(self.event(old_time, 'normal:other'), respawn_seconds=72*3600)
        self.app.schedule_events = [past, other]
        self.app.schedule_active_entries = [self.active()]
        self.app._ensure_schedule_events_cover_generation_end()
        self.assertEqual([row['scheduled_at'] for row in self.app.schedule_events if row['raw_key']==self.key], [old_time])
        self.assertEqual([row['scheduled_at'] for row in self.app.schedule_events if row['raw_key']=='normal:other'],
                         [old_time, self.now.replace(hour=16, minute=47, second=0)])


if __name__ == '__main__':
    unittest.main()
