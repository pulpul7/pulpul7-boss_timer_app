"""Input switches must not delete schedules or shift OCR card coordinates."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from boss_timer_gui import BossTimerApp, RECORD_BOOK_BOSS_ORDER
from schedule_boss_usage import input_enabled


class BossUsageTests(unittest.TestCase):
    def setUp(self):
        self.app = BossTimerApp.__new__(BossTimerApp)
        self.app.schedule_boss_definitions = {
            'A': dict(boss_name='A', alias='aa', area='test', respawn='002:00', enabled=False),
            'B': dict(boss_name='B', alias='bb', area='test', respawn='002:00'),
        }
        self.app._append_debug_log = Mock()
        self.now = datetime(2026, 10, 15, 12)
        self.app._get_schedule_reference_datetime = lambda: self.now
        self.app._normalize_schedule_input_boss_name = lambda name: dict(
            boss_name={'aa': 'A', 'bb': 'B'}.get(name, name), raw_name=name,
            display_name=name, raw_key='normal:'+name,
        )

    def test_legacy_default_alias_invasion_unknown_and_control(self):
        definitions = self.app.schedule_boss_definitions
        for name in ('A', ' aa ', '침공 aa', '침공 A'):
            self.assertFalse(input_enabled(definitions, name))
        for item in ('B', 'bb', 'unknown', dict(state='control', boss_name='A')):
            self.assertTrue(input_enabled(definitions, item))

    def test_canonical_name_wins_over_colliding_alias(self):
        self.app.schedule_boss_definitions['A']['alias'] = 'B'
        self.assertTrue(self.app._is_schedule_boss_input_enabled('B'))

    def test_new_input_filters_disabled_without_rejecting_valid_lines(self):
        parsed, ignored = self.app._parse_schedule_input_lines(
            '1300 aa, B\n1400 침공 A', reference_datetime=self.now, respect_boss_usage=True)
        self.assertEqual([row['boss_name'] for row in parsed], ['B'])
        self.assertEqual(ignored, 0)
        self.assertIn('schedule_input_disabled_bosses_skipped', self.app._append_debug_log.call_args.args[0])

    def test_existing_edit_parser_remains_lossless(self):
        events = [dict(boss_name='A', scheduled_at=self.now)]
        self.app.schedule_events = events
        parsed, ignored = self.app._parse_schedule_input_lines('1300 A\n1400 B', reference_datetime=self.now)
        self.assertEqual([row['boss_name'] for row in parsed], ['A', 'B'])
        self.assertEqual(ignored, 0)
        self.assertIs(self.app.schedule_events, events)

    def test_disabled_duplicates_do_not_open_warning(self):
        self.app._widget_available = lambda widget: False
        self.app._show_centered_messagebox = Mock()
        self.assertFalse(self.app._warn_schedule_input_duplicate_bosses('1300 A\n1400 A'))
        self.app._show_centered_messagebox.assert_not_called()

    def test_cached_ocr_entries_are_filtered_when_usage_changes(self):
        self.app._is_schedule_input_ocr_second_confirmed_filter_enabled = lambda: False
        self.app._is_schedule_input_ocr_within_day_filter_enabled = lambda: False
        self.assertFalse(self.app._should_include_schedule_input_ocr_render_entry(dict(boss_name='A')))
        self.assertTrue(self.app._should_include_schedule_input_ocr_render_entry(dict(boss_name='B')))
        self.app.schedule_boss_definitions['A']['enabled'] = True
        self.assertTrue(self.app._should_include_schedule_input_ocr_render_entry(dict(boss_name='A')))

    def test_input_switches_do_not_change_ocr_slot_coordinates(self):
        area = next(area for area, names in RECORD_BOOK_BOSS_ORDER.items() if len(names) >= 8)
        before = self.app._get_schedule_ocr_slot_rects(area, 1600, 900)
        self.app.schedule_boss_definitions = {
            name: dict(boss_name=name, enabled=index < 2)
            for index, name in enumerate(RECORD_BOOK_BOSS_ORDER[area])
        }
        after = self.app._get_schedule_ocr_slot_rects(area, 1600, 900)
        self.assertEqual(after, before)
        self.assertEqual([row['slot_index'] for row in after], list(range(1, len(after)+1)))

    def test_missing_input_summary_excludes_disabled_boss(self):
        self.app.schedule_missing_boss_catalog_cache = None
        self.app.schedule_area_definitions = {'test': dict(label='Test')}
        self.app._get_schedule_area_values = lambda: ['test']
        self.assertEqual(self.app._get_schedule_missing_boss_catalog(), (('test', 'Test', ('B',)),))

    def test_usage_round_trip_and_legacy_eight_column_load(self):
        app = self.app
        defaults = deepcopy(app.schedule_boss_definitions)
        for item in defaults.values():
            item.update(enabled=True, absolute=False, star=False, text_color='', bg_color='')
        app._build_schedule_boss_definitions = lambda: deepcopy(defaults)
        app._ensure_init_dir = Mock();app._seed_init_file_from_resource = Mock()
        app.schedule_boss_deleted_builtin_names = set()
        app.schedule_area_definitions = {'test': {}}
        with tempfile.TemporaryDirectory(prefix='boss-usage-unit-') as temporary:
            path = Path(temporary)/'definitions.txt'
            app._get_schedule_boss_definitions_storage_path = lambda: str(path)
            app._save_schedule_boss_definitions()
            self.assertEqual(path.read_text(encoding='utf-8').splitlines()[1].split('|')[8], '0')
            loaded = app._load_schedule_boss_definitions()
            self.assertFalse(loaded['A']['enabled']);self.assertTrue(loaded['B']['enabled'])
            path.write_text('A|aa|002:00|test|0|||0\nB|bb|002:00|test|0|||0\n', encoding='utf-8')
            loaded = app._load_schedule_boss_definitions()
            self.assertTrue(loaded['A']['enabled']);self.assertTrue(loaded['B']['enabled'])

    def test_shared_configuration_exports_and_restores_usage(self):
        app = self.app
        exported = app._build_github_boss_definition_items()
        self.assertFalse(next(row for row in exported if row['boss_name']=='A')['enabled'])
        app.schedule_area_definitions = {'test': {}}
        app._build_schedule_boss_definitions = lambda: {}
        for raw, expected in ((dict(boss_name='A', area='test'), False),
                              (dict(boss_name='A', area='test', enabled=True), True)):
            restored = app._normalize_github_boss_definition_item(raw, preserved_colors={})
            self.assertIs(restored['enabled'], expected)

    def test_usage_toggle_preserves_events_and_unsaved_form_draft(self):
        app = self.app
        app.schedule_boss_draft_definitions = {'A': dict(boss_name='A', text_color='#123456', enabled=False)}
        app.schedule_boss_selected_name = 'A';app.schedule_boss_form_syncing = False
        app.schedule_boss_enabled_var = SimpleNamespace(set=Mock())
        app.schedule_events = [dict(boss_name='A')]
        before = deepcopy(app.schedule_events)
        for name in ('_save_schedule_definition_files', '_populate_schedule_boss_definition_tree',
                     '_refresh_schedule_view', '_refresh_schedule_boss_config_aux_buttons'):
            setattr(app, name, Mock())
        app._toggle_schedule_boss_enabled('A')
        self.assertTrue(app.schedule_boss_definitions['A']['enabled'])
        self.assertEqual(app.schedule_boss_draft_definitions['A']['text_color'], '#123456')
        self.assertTrue(app.schedule_boss_draft_definitions['A']['enabled'])
        self.assertEqual(app.schedule_events, before)

    def test_cached_ocr_refresh_preserves_manual_edits(self):
        app = self.app
        app.schedule_input_window = object();app._widget_available = lambda widget: True
        app.schedule_input_ocr_mode_render_texts = {'ocr': '1300 A', 'ocr2': '1400 B'}
        app.schedule_input_ocr_mode_results = {'ocr': [], 'ocr2': []}
        app._get_schedule_input_ocr_text_widget_for_mode = lambda mode: mode
        current = {'ocr': '1300 A manually changed', 'ocr2': '1400 B'}
        app._get_schedule_input_ocr_text_widget_rendered_text = lambda mode: current[mode]
        app._render_schedule_input_ocr_text = Mock()
        app._refresh_schedule_boss_input_usage_views()
        app._render_schedule_input_ocr_text.assert_not_called()
        current['ocr'] = '1300 A';app._refresh_schedule_boss_input_usage_views()
        self.assertEqual(app._render_schedule_input_ocr_text.call_count, 2)

    def test_usage_and_star_columns_keep_separate_actions(self):
        app = self.app
        app.schedule_boss_tree_syncing = False
        app.schedule_boss_tree = Mock()
        app.schedule_boss_tree.identify_row.return_value = 'A'
        app._toggle_schedule_boss_enabled = Mock();app._toggle_schedule_boss_star = Mock()
        event = SimpleNamespace(x=1, y=1)
        app.schedule_boss_tree.identify_column.return_value = '#2'
        self.assertEqual(app._on_schedule_boss_tree_click(event), 'break')
        app._toggle_schedule_boss_enabled.assert_called_once_with('A')
        app._toggle_schedule_boss_star.assert_not_called()
        app.schedule_boss_tree.identify_column.return_value = '#4'
        app._on_schedule_boss_tree_click(event)
        app._toggle_schedule_boss_star.assert_called_once_with('A')

    def test_legacy_on_shared_hash_stays_compatible_but_off_changes_it(self):
        old = dict(boss_definitions=[dict(boss_name='A')], fixed_bosses=[])
        on = deepcopy(old);on['boss_definitions'][0]['enabled'] = True
        off = deepcopy(old);off['boss_definitions'][0]['enabled'] = False
        self.assertEqual(self.app._get_github_boss_config_content_hash(old), self.app._get_github_boss_config_content_hash(on))
        self.assertNotEqual(self.app._get_github_boss_config_content_hash(old), self.app._get_github_boss_config_content_hash(off))
        self.assertTrue(on['boss_definitions'][0]['enabled'])


if __name__ == '__main__':
    unittest.main()
