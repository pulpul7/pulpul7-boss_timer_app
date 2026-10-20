"""Source edits rebuild once; ordinary timer ticks only renew the envelope."""
from copy import deepcopy
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from test_web_boss_catalog import app_fixture
from web_snapshot_memo import WebSnapshotMemo
from web_schedule_snapshot import collect_snapshot
from web_delegated_hosting import edit_hash
from web_service_common import PROTOCOL, schedule_path, write_json
from web_schedule_cache import ScheduleCache


class Variable:
    def __init__(self, value): self.value = value
    def get(self): return self.value


class SnapshotMemoTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 18, 8)
        self.app, _ = app_fixture()
        app = self.app
        app.fixed_boss_entries = []
        app.schedule_control_events = []
        app.schedule_active_entries = []
        app.schedule_events = [dict(boss_name='티르', scheduled_at=self.now+timedelta(hours=1), input_order=2),
                               dict(boss_name='코요아', scheduled_at=self.now+timedelta(hours=1), input_order=1)]
        app._get_schedule_reference_datetime = lambda: self.now
        app._get_schedule_visible_cutoff_datetime = lambda: None
        app._get_schedule_share_server_name = lambda: 'server9'
        app.current_season_no = 18
        app.season_history_map = {'18': dict(guild_name='guild')}
        app.schedule_break_rows_enabled_var = Variable(False)
        app._get_schedule_boss_display_name = lambda row, **_: app._get_display_boss_name(row['boss_name'])
        app._schedule_boss_has_star = lambda row: bool(app.schedule_boss_definitions.get(
            row['boss_name'] if isinstance(row, dict) else row, {}).get('star'))
        app.schedule_break_entries = [dict(min_gap_minutes=40, max_gap_minutes=79, row_scale=1.5)]
        app.log_stats_boss_enabled = {'티르': True}
        def rows(start, end, **_):
            return ([dict(kind='event', boss_text=row['boss_name'], scheduled_at=row['scheduled_at'],
                          input_order=row['input_order']) for row in app.schedule_events], start, end)
        app._collect_schedule_share_rows = rows
        self.collector = Mock(wraps=collect_snapshot)
        self.memo = WebSnapshotMemo()

    def capture(self):
        return self.memo.capture(self.app, self.collector)

    def test_thirty_heartbeats_keep_one_collection_and_same_view_and_edit_hash(self):
        first = self.capture()
        original = deepcopy(first)
        for tick in range(30):
            self.now += timedelta(seconds=3)
            with patch('web_snapshot_memo.timestamp', return_value=str(tick)):
                next_payload = self.capture()
            self.assertEqual(edit_hash(next_payload), edit_hash(first))
            self.assertEqual(next_payload['captured_at'], str(tick))
            self.assertIs(next_payload['events'], first['events'])
        self.assertEqual(self.collector.call_count, 1)
        self.assertEqual(first, original)
        self.assertEqual([r['name'] for r in first['events']], ['코요아', '티르'])

    def test_in_place_edit_delete_alias_star_last_hit_and_rest_changes_rebuild(self):
        self.capture()
        self.app.schedule_events[0]['scheduled_at'] += timedelta(minutes=1)
        edited = self.capture()
        self.assertEqual(self.collector.call_count, 2)
        expected = collect_snapshot(self.app)
        self.assertEqual({k:v for k,v in edited.items() if k != 'captured_at'},
                         {k:v for k,v in expected.items() if k != 'captured_at'})
        self.app.schedule_events.pop()
        self.assertEqual(len(self.capture()['events']), 1)
        definition = self.app.schedule_boss_definitions['티르']
        definition['last_hit'] = True
        self.assertTrue(self.capture()['events'][0]['last_hit'])
        definition.update(alias='new alias', star=False)
        self.capture()
        self.assertFalse(self.memo.reused)
        self.app.schedule_break_entries[0]['row_scale'] = 2
        self.assertEqual(self.capture()['rest_rules'][0]['row_scale'], 2)
        self.app.schedule_break_rows_enabled_var.value = True
        self.capture()
        self.assertFalse(self.memo.reused)

    def test_active_profile_color_and_statistics_revision_are_source_changes(self):
        self.capture()
        self.app.schedule_active_entries.append(dict(boss_name='티르', state='active'))
        self.assertEqual(len(self.capture()['active']), 1)
        self.app.schedule_invasion_last_bg_color = '#123456'
        self.capture();self.assertFalse(self.memo.reused)
        self.app.schedule_avg_cut_cache_revision = 1
        self.capture();self.assertFalse(self.memo.reused)
        self.app.current_season_no = 19
        self.app.season_history_map['19'] = dict(guild_name='next guild')
        changed = self.capture()
        self.assertEqual((changed['season'], changed['guild_name']), ('19', 'next guild'))

    def test_midnight_control_boundary_and_maintenance_change_without_edit_rebuild(self):
        self.now = datetime(2026, 10, 18, 23, 59, 59)
        control_time = self.now+timedelta(seconds=4)
        self.app.schedule_control_events = [dict(control_type='server_open', scheduled_at=control_time)]
        self.capture()
        self.now += timedelta(seconds=2)
        self.capture();self.assertEqual(self.collector.call_count, 2)
        self.now += timedelta(seconds=2)
        self.capture();self.assertEqual(self.collector.call_count, 3)
        self.now += timedelta(seconds=1)
        self.capture();self.assertEqual(self.collector.call_count, 3)
        cutoff = [self.now+timedelta(days=3)]
        self.app._get_schedule_visible_cutoff_datetime = lambda: cutoff[0]
        self.capture()
        cutoff[0] += timedelta(days=7)
        self.capture();self.assertEqual(self.collector.call_count, 5)

    def test_failed_new_generation_retries_and_unknown_input_type_never_reuses_stale_content(self):
        self.capture()
        self.app.schedule_events[0]['input_order'] = 99
        self.collector.side_effect = ValueError('temporary fixture error')
        with self.assertRaises(ValueError): self.capture()
        self.collector.side_effect = None
        self.capture()
        self.assertFalse(self.memo.reused)
        self.app.schedule_events[0]['unknown_runtime_value'] = object()
        self.capture();self.capture()
        self.assertFalse(self.memo.reused)

    def test_reuse_renews_worker_freshness_without_changing_browser_versions(self):
        with tempfile.TemporaryDirectory(prefix='snapshot-memo-unit-') as folder:
            root, run_id = Path(folder), 'a'*32
            cache = ScheduleCache(root, run_id, None)
            base = datetime(2026, 10, 18, tzinfo=timezone.utc)
            for seconds in (0, 12, 24, 60):
                self.now += timedelta(seconds=seconds)
                capture_time = base+timedelta(seconds=seconds)
                with patch('web_snapshot_memo.timestamp', return_value=capture_time.isoformat()):
                    payload = self.capture()
                write_json(schedule_path(root, run_id), dict(payload, protocol=PROTOCOL, run_id=run_id,
                                                            revision=1, updated_at=payload['captured_at']))
                with patch('web_schedule_cache.time.monotonic', return_value=100+seconds), \
                     patch('web_schedule_cache.datetime', NS(now=lambda _:capture_time, fromisoformat=datetime.fromisoformat)):
                    cache.refresh()
                    state = cache.status()
                self.assertEqual(state['schedule_state'], 'ready')
                if seconds == 0:
                    versions = [state[key] for key in ('schedule_version', 'major_version', 'catalog_version')]
                else:
                    self.assertEqual([state[key] for key in ('schedule_version', 'major_version', 'catalog_version')], versions)
            self.assertEqual(self.collector.call_count, 1)

    def test_real_gui_three_second_callback_reuses_but_still_offers_heartbeats(self):
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        method = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef) and node.name == '_publish_web_schedule_snapshot')
        scope = dict(time=time, tk=NS(TclError=RuntimeError), WebSnapshotMemo=WebSnapshotMemo,
                     collect_web_schedule_snapshot=self.collector,
                     RECORD_BOOK_BOSS_ORDER={}, SCHEDULE_OCR_SLOT_GRID={})
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'web-publish-callback', 'exec'), scope)
        app = self.app
        app.root = Mock()
        app.web_schedule_snapshot_failed = False
        app.web_service_manager = NS(closed=threading.Event(), delegation=None,
                                     status=lambda:dict(enabled=True), offer_snapshot=Mock(), _log=Mock())
        app._refresh_schedule_web_status = Mock()
        callback = scope['_publish_web_schedule_snapshot']
        app._publish_web_schedule_snapshot = lambda: callback(app)
        for _ in range(10):
            callback(app)
            self.now += timedelta(seconds=3)
        self.assertEqual(self.collector.call_count, 1)
        self.assertEqual(app.web_service_manager.offer_snapshot.call_count, 10)
        self.assertEqual(app.root.after.call_args.args[0], 3000)
        app.schedule_events[0]['scheduled_at'] += timedelta(minutes=1)
        callback(app)
        self.assertEqual(self.collector.call_count, 2)


if __name__ == '__main__':
    unittest.main()
