"""Equal-time input order survives normalization, persistence and all readers."""
from datetime import datetime, timedelta
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

import boss_timer_discord_bot as bot
from schedule_order import assign_input_order, event_sort_key
import test_edge_tts_voice as fixtures
from test_web_boss_catalog import app_fixture
from web_schedule_snapshot import collect_snapshot, validate_snapshot


class AfterMerge(Exception):
    pass


class ScheduleInputOrderTests(unittest.TestCase):
    names = ['스카드', '헤이드', '프레이']

    def setUp(self):
        self.now = datetime(2026, 10, 17, 10)
        self.app = app = fixtures.DiscordScheduleInputTests._parser_app(self.now)
        app.schedule_events = []
        app.schedule_active_entries = []
        app.schedule_control_events = []
        app.schedule_input_add_mode = True
        app.schedule_second_precision_offsets = {}
        app.schedule_last_import_meta = None
        app.schedule_delete_active_cutoff_datetime = None
        app._get_schedule_generation_end_datetime = lambda *_a, **_k: self.now + timedelta(hours=4)
        app._clear_schedule_second_precision_offsets_for_raw_keys = lambda *_: 0
        app._get_schedule_active_control_event = lambda *_: None
        app._get_schedule_latest_generation_control_datetime = lambda *_: None
        app._build_schedule_control_event_for_item = lambda *_: None
        app._is_schedule_invasion_item = lambda *_: False
        app._get_schedule_respawn_seconds_for_state_item = lambda *_: 3600
        app._is_schedule_boss_metric_schedule_duration_enabled = lambda: False
        app._apply_schedule_second_precision_offset_to_datetime = lambda value, _: value
        app._merge_schedule_control_events = Mock(side_effect=AfterMerge)
        app._is_schedule_boss_input_enabled = lambda *_: True

    def apply(self, text, discord=False):
        # Execute the real parser, order assignment, recurring generation and merge.
        # Stop before UI, disk or audio work (both GUI and Discord share this core).
        with patch.object(self.app, '_apply_schedule_parsed_batch', wraps=self.app._apply_schedule_parsed_batch) as apply:
            with self.assertRaises(AfterMerge):
                if discord:
                    self.app._apply_discord_schedule_text(bot.parse_discord_schedule_message(text)['raw_text'])
                else:
                    items, ignored = self.app._parse_schedule_input_lines(text, reference_datetime=self.now)
                    self.app._apply_schedule_parsed_batch(text, items, ignored, self.now, self.now,
                                                         skip_overwrite_confirm=True, store_input_undo_backup=False)
            self.assertTrue(apply.called)

    def at(self, hour=11):
        return [row['boss_name'] for row in self.app.schedule_events
                if row['scheduled_at'] == self.now.replace(hour=hour, minute=22)]

    def test_gui_and_discord_enter_same_persistent_input_order(self):
        for discord in (False, True):
            with self.subTest(discord=discord):
                self.app.schedule_events = []
                self.apply('1122 스카드, 헤이드, 프레이', discord)
                self.assertEqual(self.at(), self.names)
                self.assertEqual(self.at(12), self.names)
                self.assertTrue(all(row['input_order'] > 0 for row in self.app.schedule_events))

    def test_normalization_roundtrip_regeneration_and_renaming_do_not_revert_order(self):
        self.apply('1122 스카드, 헤이드, 프레이')
        wire = json.loads(json.dumps(self.app._serialize_schedule_state_value(self.app.schedule_events)))
        self.app.schedule_events = self.app._deserialize_schedule_state_value(wire)
        self.app.schedule_events.reverse()  # Rely on stored order rather than list accident.
        self.app.schedule_events = self.app._normalize_schedule_event_items(self.app.schedule_events)
        self.assertEqual(self.at(), self.names)
        self.app._get_schedule_generation_end_datetime = lambda *_a, **_k: self.now + timedelta(hours=7)
        self.app._ensure_schedule_events_cover_generation_end()
        self.assertEqual(self.at(16), self.names)
        for row in self.app.schedule_events:
            row['display_name'] = {'스카드': '힣', '헤이드': '가', '프레이': '나'}[row['boss_name']]
        self.app.schedule_events.sort(key=event_sort_key)
        self.assertEqual(self.at(), self.names)

    def test_later_batch_and_reinput_keep_each_new_batch_order(self):
        self.apply('1122 스카드, 헤이드, 프레이')
        self.apply('1122 가보스, 나보스')
        self.assertEqual(self.at(), self.names + ['가보스', '나보스'])
        self.apply('1122 프레이, 스카드, 헤이드')
        self.assertEqual(self.at(), ['가보스', '나보스', '프레이', '스카드', '헤이드'])

    def test_legacy_items_keep_stored_ties_but_different_times_stay_chronological(self):
        when = self.now.replace(hour=11, minute=22)
        rows = [dict(boss_name=name, raw_key=name, scheduled_at=when, state='scheduled') for name in self.names]
        earlier = dict(rows[0], raw_key='earlier', scheduled_at=when-timedelta(microseconds=1))
        normalized = self.app._normalize_schedule_event_items([*rows, earlier])
        self.assertEqual(normalized[0]['scheduled_at'], earlier['scheduled_at'])
        self.assertEqual([row['boss_name'] for row in normalized[1:]], self.names)

    def test_tree_and_share_sort_use_order_before_name_and_preserve_fixed_priority(self):
        self.apply('1122 스카드, 헤이드, 프레이')
        events = [r for r in self.app.schedule_events if r['scheduled_at'].hour == 11]
        tree = [(r['scheduled_at'], r['boss_name'], '', '', '', '', '', '', 'event', r, {}) for r in reversed(events)]
        fixed = (events[0]['scheduled_at'], '가고정', '', '', '', '', '', '', 'fixed', {}, {})
        result = sorted([fixed, *tree], key=self.app._get_schedule_visible_row_sort_key)
        self.assertEqual([r[1] for r in result], self.names + ['가고정'])
        share = [dict(r, kind='event', boss_text=r['boss_name']) for r in reversed(events)]
        self.assertEqual([r['boss_text'] for r in sorted(share, key=self.app._get_schedule_share_row_sort_key)], self.names)

    def test_web_collect_and_validation_preserve_order_after_shuffled_transport(self):
        self.apply('1122 스카드, 헤이드, 프레이')
        app, _ = app_fixture()
        app._get_schedule_reference_datetime = lambda: self.now
        app._get_schedule_visible_cutoff_datetime = lambda: None
        app.schedule_break_rows_enabled_var = NS(get=lambda: False)
        app.fixed_boss_entries = []
        rows = [dict(r, kind='event', boss_text=r['boss_name']) for r in reversed(self.app.schedule_events)]
        app._collect_schedule_share_rows = lambda start, end, **kw: (rows, start, end)
        app.schedule_control_events = []; app.schedule_active_entries = []
        app.current_season_no = 18; app.season_history_map = {}; app._get_schedule_share_server_name = lambda: 'test'
        payload = collect_snapshot(app)
        payload.update(revision=1, updated_at=payload['captured_at'])
        for _ in range(3):
            payload['events'].reverse()
            payload = validate_snapshot(json.loads(json.dumps(payload)))
            self.assertEqual([r['name'] for r in payload['events'][:3]], self.names)

    def test_discord_schedule_reader_does_not_replace_order_with_name_or_kind(self):
        self.apply('1122 스카드, 헤이드, 프레이')
        wire = self.app._serialize_schedule_state_value(dict(schedule_events=list(reversed(self.app.schedule_events))))
        reader = bot.ScheduleReader()
        with patch.object(bot, 'load_json', return_value=wire), \
             patch.object(reader, '_load_boss_definition_map', return_value={}), \
             patch.object(reader, '_load_fixed_boss_entries', return_value=[]):
            rows = reader.upcoming_rows(reference_datetime=self.now)
        self.assertEqual([r['name'] for r in rows[:3]], self.names)

    def test_internal_rebuild_keeps_rank_without_mutating_parser_rows(self):
        rows = [dict(state='scheduled', input_order=4), dict(state='scheduled')]
        result = assign_input_order(rows, [dict(input_order=9)])
        self.assertEqual([r['input_order'] for r in result], [4, 10])
        self.assertNotIn('input_order', rows[1])


if __name__ == '__main__':
    unittest.main()
