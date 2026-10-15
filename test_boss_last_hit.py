"""Last-hit labels must never become schedule identities or editable input."""
from copy import deepcopy
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from boss_view_labels import boss_label, row_label, notice_label, strip_last_hit_marker
from boss_timer_gui import BossTimerApp
import boss_timer_discord_bot as bot
from schedule_share_text import render_share_text
from test_web_boss_catalog import app_fixture
from web_schedule_snapshot import _collect_catalog, collect_snapshot, validate_snapshot


class LastHitTests(unittest.TestCase):
    def setUp(self):
        self.app = BossTimerApp.__new__(BossTimerApp)
        self.app.schedule_boss_definitions = {
            '티르': dict(boss_name='티르', alias='티', area='test', respawn='002:00', last_hit=True),
            '미미르': dict(boss_name='미미르', alias='미', area='test', respawn='002:00'),
        }
        self.app._append_debug_log = Mock()
        self.app.record_book_catalog = []
        self.app._rebuild_boss_name_resolution_maps()

    def test_view_labels_leave_name_alias_raw_key_and_voice_candidates_plain(self):
        app = self.app
        item = dict(boss_name='티르', display_name='티', raw_name='티르', raw_key='normal:티르')
        before = deepcopy(item)
        self.assertEqual(app._get_schedule_boss_display_name(item, prefer_alias=True), '티')
        self.assertEqual(app._get_schedule_boss_view_name(item, prefer_alias=True), '티 (막)')
        self.assertEqual(app._get_schedule_boss_definition_name(item), '티르')
        self.assertEqual(app._get_canonical_boss_name('티'), '티르')
        self.assertNotIn('(막)', app._get_schedule_cut_input_text(item))
        self.assertTrue(all('(막)' not in text for text in app._get_schedule_alarm_boss_voice_name_candidates(boss_name='티르')))
        self.assertEqual(item, before)

    def test_invasion_and_star_are_view_only(self):
        app = self.app
        app.schedule_boss_definitions['티르']['star'] = True
        item = dict(boss_name='티르', is_invasion=True)
        self.assertEqual(app._get_schedule_boss_view_name(item, prefer_alias=True, include_star=True), '침공 티 (막) ★')
        self.assertEqual(app._get_schedule_boss_display_name(item, prefer_alias=True), '침공 티')
        self.assertEqual(app._get_schedule_boss_view_name(dict(boss_name='미미르')), '미')

    def test_defensive_marker_stripping_does_not_enable_flag_or_pollute_input(self):
        app = self.app
        app._get_schedule_voice_boss_lookup_map = lambda: {}
        for raw in ('티르(막)', '티르 (막)', '티 (막)', '티르 ( 막 ) (막)'):
            with self.subTest(raw=raw):
                normalized = app._normalize_schedule_input_boss_name(raw)
                self.assertEqual(normalized['boss_name'], '티르')
                self.assertEqual(normalized['raw_key'], 'normal:티르')
                self.assertTrue(all('(막)' not in str(value) for value in normalized.values()))
                self.assertEqual(app._get_canonical_boss_name(raw), '티르')
        normalized = app._normalize_schedule_input_boss_name('침공 미미르 (막)')
        self.assertEqual(normalized['raw_key'], 'invasion:미미르')
        self.assertFalse(app._schedule_boss_is_last_hit(normalized))
        self.assertEqual(strip_last_hit_marker('막타 보스'), '막타 보스')

    def test_pasted_text_strips_marker_before_multi_boss_expansion(self):
        app = self.app
        now = datetime(2026, 10, 15, 12)
        app._get_schedule_reference_datetime = lambda: now
        app._get_schedule_voice_boss_lookup_map = lambda: {}
        rows, ignored = app._parse_schedule_input_lines('1300 티르(막), 미미르 (막)', reference_datetime=now)
        self.assertEqual(ignored, 0)
        self.assertEqual({row['boss_name'] for row in rows}, {'티르', '미미르'})
        self.assertTrue(all('(막)' not in str(row) for row in rows))

    def test_adjacent_desktop_txt_is_input_and_has_no_mark_but_image_label_does(self):
        now = datetime(2026, 10, 15, 12)
        row = dict(kind='event', scheduled_at=now, boss_text='티', input_clock_text='1200', last_hit=True)
        self.assertEqual(render_share_text([row], now), '1200 티')
        self.assertEqual(row_label(row, 'boss_text'), '티 (막)')
        self.assertEqual(row['boss_text'], '티')

    def test_notice_labels_handle_alias_prefix_collisions_and_duplicate_suffix(self):
        definitions = {'티르': {'last_hit': True}, '티': {'last_hit': True}, '티르전사': {}, '미미르': {}}
        message = '곧 티르, 티, 티르전사, 미미르 타임입니다.'
        self.assertEqual(notice_label(message, definitions), '곧 티르 (막), 티 (막), 티르전사, 미미르 타임입니다.')
        self.assertEqual(notice_label('티르 (막) 5분 남았습니다.', definitions), '티르 (막) 5분 남았습니다.')
        self.assertEqual(boss_label('', True), '')
        self.assertEqual(message, '곧 티르, 티, 티르전사, 미미르 타임입니다.')

    def test_setting_round_trip_and_old_nine_column_files_default_off(self):
        app = self.app
        defaults = deepcopy(app.schedule_boss_definitions)
        app._build_schedule_boss_definitions = lambda: deepcopy(defaults)
        app._ensure_init_dir = Mock(); app._seed_init_file_from_resource = Mock()
        app.schedule_boss_deleted_builtin_names = set();app.schedule_area_definitions = {'test': {}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'bosses.txt'
            app._get_schedule_boss_definitions_storage_path = lambda: str(path)
            app._save_schedule_boss_definitions()
            stored = path.read_text(encoding='utf-8')
            self.assertIn('티르|티|', stored)
            self.assertNotIn('(막)', stored)
            self.assertTrue(app._load_schedule_boss_definitions()['티르']['last_hit'])
            path.write_text('티르|티|002:00|test|0|||0|1\n', encoding='utf-8')
            self.assertFalse(app._load_schedule_boss_definitions()['티르']['last_hit'])

    def test_shared_config_flag_and_legacy_hash(self):
        app = self.app
        exported = app._build_github_boss_definition_items()
        self.assertTrue(next(row for row in exported if row['boss_name']=='티르')['last_hit'])
        app.schedule_area_definitions = {'test': {}};app._build_schedule_boss_definitions = lambda: {}
        for raw, expected in ((dict(boss_name='티르', area='test'), True),
                              (dict(boss_name='티르', area='test', last_hit=False), False)):
            self.assertIs(app._normalize_github_boss_definition_item(raw, preserved_colors={})['last_hit'], expected)
        old = dict(boss_definitions=[dict(boss_name='티르')])
        off = deepcopy(old);off['boss_definitions'][0]['last_hit'] = False
        on = deepcopy(old);on['boss_definitions'][0]['last_hit'] = True
        self.assertEqual(app._get_github_boss_config_content_hash(old), app._get_github_boss_config_content_hash(off))
        self.assertNotEqual(app._get_github_boss_config_content_hash(old), app._get_github_boss_config_content_hash(on))

    def test_toggle_keeps_existing_events_and_drafts(self):
        app = self.app
        app.schedule_events = [dict(boss_name='티르', raw_key='normal:티르')]
        before = deepcopy(app.schedule_events)
        app.schedule_boss_draft_definitions = {'티르': dict(alias='수정 중', last_hit=True)}
        app.schedule_boss_selected_name = '티르';app.schedule_boss_form_syncing = False
        app.schedule_boss_last_hit_var = SimpleNamespace(set=Mock())
        for name in ('_save_schedule_definition_files', '_populate_schedule_boss_definition_tree',
                     '_refresh_schedule_view', '_refresh_schedule_boss_config_aux_buttons'):
            setattr(app, name, Mock())
        app._toggle_schedule_boss_last_hit('티르')
        self.assertFalse(app.schedule_boss_definitions['티르']['last_hit'])
        self.assertEqual(app.schedule_boss_draft_definitions['티르'], dict(alias='수정 중', last_hit=False))
        self.assertEqual(app.schedule_events, before)

    def test_tree_actions_are_separate(self):
        app = self.app;app.schedule_boss_tree_syncing = False;app.schedule_boss_tree = Mock()
        app.schedule_boss_tree.identify_row.return_value = '티르'
        app._toggle_schedule_boss_last_hit = Mock();app._toggle_schedule_boss_star = Mock()
        app._toggle_schedule_boss_enabled = Mock()
        app.schedule_boss_tree.identify_column.return_value = '#3'
        app._on_schedule_boss_tree_click(SimpleNamespace(x=1, y=1))
        app._toggle_schedule_boss_last_hit.assert_called_once_with('티르')
        app._toggle_schedule_boss_enabled.assert_not_called();app._toggle_schedule_boss_star.assert_not_called()

    def test_web_names_ids_catalog_and_selection_keys_do_not_change(self):
        app, _ = app_fixture();now = datetime(2026, 10, 15, 12)
        app._get_schedule_reference_datetime = lambda: now
        app._get_schedule_visible_cutoff_datetime = lambda: None
        app.schedule_break_rows_enabled_var = SimpleNamespace(get=lambda: False)
        app._collect_schedule_share_rows = lambda start, end, **kw: ([dict(kind='event', boss_text='티', scheduled_at=now)], start, end)
        app._get_schedule_boss_display_name = lambda item, **kw: app._get_display_boss_name(item['boss_name'])
        app._schedule_boss_has_star = lambda item: True
        app._normalize_fixed_boss_time_text = lambda value: value
        app.schedule_control_events=[];app.schedule_active_entries=[dict(boss_name='티르', state='active')]
        app.current_season_no=18;app.season_history_map={};app._get_schedule_share_server_name=lambda: '오9'
        before = collect_snapshot(app)
        app.schedule_boss_definitions['티르']['last_hit'] = True
        after = collect_snapshot(app)
        self.assertEqual(before['events'][0]['id'], after['events'][0]['id'])
        self.assertEqual(before['events'][0]['catalog_id'], after['events'][0]['catalog_id'])
        self.assertEqual(after['events'][0]['name'], '티')
        self.assertTrue(after['events'][0]['last_hit']);self.assertTrue(after['active'][0]['last_hit'])
        catalog = next(row for row in after['boss_catalog'] if row['name']=='티르')
        self.assertEqual(catalog['label'], '티');self.assertTrue(catalog['last_hit'])
        clean = validate_snapshot(dict(after, revision=1, updated_at=after['captured_at']))
        self.assertTrue(clean['events'][0]['last_hit']);self.assertTrue(clean['active'][0]['last_hit'])
        self.assertTrue(next(row for row in clean['boss_catalog'] if row['name']=='티르')['last_hit'])

    def test_discord_reader_keeps_names_plain_and_commands_render_the_mark(self):
        now = datetime.now()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);definitions=root/'bosses.txt';state=root/'state.json';fixed=root/'fixed.txt'
            definitions.write_text('티르|티|002:00|test|0|||0|1|1\n', encoding='utf-8')
            fixed.write_text('', encoding='utf-8')
            state.write_text(json.dumps(dict(schedule_events=[dict(state='scheduled', boss_name='티르',
                display_name='티', raw_key='normal:티르', scheduled_at=(now+timedelta(minutes=2)).isoformat())])), encoding='utf-8')
            with patch.object(bot, 'SCHEDULE_BOSS_DEFINITIONS_PATH', definitions), patch.object(bot, 'SCHEDULE_STATE_PATH', state), patch.object(bot, 'FIXED_BOSSES_PATH', fixed):
                rows = bot.ScheduleReader().upcoming_rows()
        self.assertEqual(rows[0]['name'], '티');self.assertTrue(rows[0]['last_hit'])
        self.assertEqual(bot.event_name(dict(boss_name='티르', display_name='티')), '티')
        self.assertIn('티 (막)', bot.DiscordScheduleBot._format_schedule_rows_as_text(rows))
        self.assertIn('티 (막)', bot.DiscordScheduleBot._format_schedule_rows_as_ansi(rows))


class LastHitNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_notice_is_marked_but_job_and_dedupe_remain_plain(self):
        import discord
        owner = bot.DiscordScheduleBot.__new__(bot.DiscordScheduleBot)
        owner.discord = discord
        owner._send_allowed = Mock(return_value=True);owner._tag_authority_task = Mock()
        owner._connection_state = lambda: dict(authority_generation=1)
        channel = SimpleNamespace(id=1, send=AsyncMock(return_value=SimpleNamespace(id=9)))
        owner._resolve_text_channel = AsyncMock(return_value=channel)
        owner._cleanup_bot_text_channel_messages = AsyncMock()
        owner.boss_notice_lock = asyncio.Lock();owner.boss_notice_groups = {};owner.text_notice_keys = set()
        owner._format_voice_notice_ansi = Mock(side_effect=lambda text: text)
        owner._find_notice_color = Mock(return_value=0x123456)
        job = SimpleNamespace(phase='SPAWN_WARNING', fallback_text='티르 5분 남았습니다.',
            scope_id='test', created_at=datetime.now(), target_time='', notice_group_id='',
            category='boss', offset_sec=300, is_expired=lambda now: False)
        with patch.object(bot.ScheduleReader, '_load_boss_definition_map', return_value={'티르': dict(last_hit=True)}), patch.object(bot, 'log'):
            await owner._send_voice_bridge_text_notice(job)
        channel.send.assert_awaited_once()
        embed = channel.send.call_args.kwargs['embed']
        self.assertIn('티르 (막)', embed.title);self.assertIn('티르 (막)', embed.description)
        self.assertEqual(job.fallback_text, '티르 5분 남았습니다.')
        self.assertTrue(owner.text_notice_keys)
        self.assertTrue(all('(막)' not in key for key in owner.text_notice_keys))


if __name__ == '__main__':
    unittest.main()
