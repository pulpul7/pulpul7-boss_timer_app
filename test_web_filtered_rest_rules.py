"""Filtered-rest settings cross the strict public snapshot/cache boundary."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from test_web_boss_catalog import app_fixture
from web_schedule_snapshot import collect_snapshot, validate_snapshot
from web_schedule_cache import ScheduleCache
from web_service_common import PROTOCOL, schedule_path, write_json


class FilteredRestRuleTests(unittest.TestCase):
    def snapshot(self):
        app, _ = app_fixture()
        now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
        app._get_schedule_reference_datetime = lambda: now
        app._get_schedule_visible_cutoff_datetime = lambda: None
        app._collect_schedule_share_rows = lambda start, end, **kw: ([], start, end)
        app._get_schedule_share_server_name = lambda: 'test'
        app.schedule_break_rows_enabled_var = NS(get=lambda: False)
        app.fixed_boss_entries = []; app.schedule_control_events = []; app.schedule_active_entries = []
        app.current_season_no = 18; app.season_history_map = {}
        app.schedule_break_entries = [dict(min_gap_minutes=40, max_gap_minutes=79, row_scale=1.5,
                                          enabled=True, label_text='private label', token='must-not-leak'),
                                      dict(min_gap_minutes=80, max_gap_minutes=120, enabled=False)]
        result = collect_snapshot(app)
        result.update(revision=1, updated_at=result['captured_at'])
        return result

    def test_only_public_enabled_rule_fields_are_published_and_validated(self):
        data = self.snapshot()
        expected = [dict(min_gap_minutes=40, max_gap_minutes=79, row_scale=1.5)]
        self.assertEqual(data['rest_rules'], expected)
        data['rest_rules'][0]['secret'] = 'private'
        self.assertEqual(validate_snapshot(data)['rest_rules'], expected)
        self.assertEqual(data['events'], [])  # Rules work even when desktop rests are hidden.

    def test_legacy_missing_rules_and_explicit_empty_rules_are_distinct(self):
        data = self.snapshot(); data.pop('rest_rules')
        self.assertNotIn('rest_rules', validate_snapshot(data))
        data['rest_rules'] = []
        self.assertEqual(validate_snapshot(data)['rest_rules'], [])

    def test_invalid_rule_ranges_and_nonfinite_heights_are_rejected(self):
        for change in [dict(min_gap_minutes=True), dict(max_gap_minutes=1), dict(row_scale=float('nan')),
                       dict(row_scale=13), dict(min_gap_minutes=-1)]:
            data = self.snapshot(); data['rest_rules'][0].update(change)
            with self.assertRaises(ValueError): validate_snapshot(data)

    def test_rule_edit_changes_schedule_version_for_browser_cache_invalidation(self):
        with tempfile.TemporaryDirectory(prefix='filtered-rest-unit-') as folder:
            root = Path(folder); run = 'a'*32; cache = ScheduleCache(root, run, Mock())
            data = self.snapshot()
            def publish(payload):
                write_json(schedule_path(root, run), dict(payload, run_id=run, protocol=PROTOCOL))
                cache.refresh()
                return cache.status()['schedule_version']
            old = publish(data)
            changed = deepcopy(data); changed['rest_rules'][0]['min_gap_minutes'] = 60; changed['revision'] += 1
            self.assertNotEqual(publish(changed), old)
            self.assertEqual(cache.payload()[1]['rest_rules'], changed['rest_rules'])


if __name__ == '__main__':
    unittest.main()
