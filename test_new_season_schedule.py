"""New-season copies use synthetic data only; no user profiles or Discord."""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import boss_timer_gui as gui
from schedule_profile_migration import seed_schedule_snapshot


class SeasonScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='boss-season-unit-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / 'server_profiles/season_19/7/schedule_state.json'
        self.entry = dict(id='오7', name='오7', schedule='data/schedules/오7.json')
        self.app = object.__new__(gui.BossTimerApp)
        self.snapshot = dict(
            schedule_events=[dict(boss_name='스카디', scheduled_at=datetime(2026, 9, 30, 3, 8, 2, 551234))],
            schedule_active_entries=[dict(boss_name='헤르모드')],
            schedule_control_events=[dict(control_type='maintenance', scheduled_at=datetime(2026, 9, 30, 7))],
            schedule_second_precision_offsets={'스카디': 0.12},
            schedule_last_import_meta=dict(github_server_id='오9', source_path='old.json', scheduleVersion='18.123',
                                           bossConfigVersion='18.456', source_type='github_data'),
            schedule_tree_quick_cut_history=[dict(backup='old-season')])

    def copy(self, snapshot=None):
        return seed_schedule_snapshot(self.target, self.app._serialize_schedule_state_value(
            self.snapshot if snapshot is None else snapshot), source_season='season_18',
            target_season='season_19', entry=self.entry)

    def test_copy_retains_times_and_source_rebases_only_import_metadata(self):
        original = deepcopy(self.snapshot)
        self.assertTrue(self.copy())
        restored = self.app._deserialize_schedule_state_value(json.loads(self.target.read_text(encoding='utf-8')))
        for key in ('schedule_events', 'schedule_active_entries', 'schedule_control_events', 'schedule_second_precision_offsets'):
            self.assertEqual(restored[key], self.snapshot[key])
        self.assertEqual(self.snapshot, original)
        meta = restored['schedule_last_import_meta']
        self.assertEqual(meta['github_server_id'], '오7')
        self.assertEqual(meta['season_no'], '19')
        self.assertEqual(meta['source_type'], 'season_copy')
        self.assertNotIn('scheduleVersion', meta)
        self.assertNotIn('bossConfigVersion', meta)
        self.assertEqual(restored['schedule_tree_quick_cut_history'], [])

    def test_existing_even_empty_destination_is_preserved(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text('{}')
        self.assertFalse(self.copy())
        self.assertEqual(self.target.read_text(), '{}')

    def test_empty_source_does_not_create_schedule(self):
        self.assertFalse(self.copy({}))
        self.assertFalse(self.target.exists())

    def test_copy_failure_never_publishes_partial_schedule(self):
        with patch('runtime_storage.os.link', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                self.copy()
        self.assertFalse(self.target.exists())
        self.assertFalse(list(self.target.parent.glob('.season-schedule-*')))

    def test_explicit_setup_prepares_current_snapshot_before_activation(self):
        app = Mock()
        app.schedule_server_profile_id = '9'
        app._build_github_server_entry_from_metadata.return_value = self.entry
        app._normalize_schedule_server_profile_id.side_effect = gui.BossTimerApp._normalize_schedule_server_profile_id
        app._normalize_schedule_server_profile_season_key.return_value = 'season_19'
        app._get_active_schedule_server_profile_season_key.return_value = 'season_18'
        app._create_schedule_full_state_snapshot.return_value = self.snapshot
        app._serialize_schedule_state_value.side_effect = self.app._serialize_schedule_state_value
        with patch.object(gui, 'get_user_config_dir', return_value=str(self.root)), \
             patch('schedule_profile_migration.seed_profile'), \
             patch('schedule_profile_migration.ensure_profile_defaults'):
            gui.BossTimerApp._prepare_schedule_profile_for_new_season(app, '19', '오7', '길드')
        self.assertTrue(self.target.is_file())
        app._stop_discord_bot_runtime_core.assert_not_called()
        app._save_settings.assert_not_called()

    def test_setup_dialog_return_never_resets_copied_or_existing_schedule(self):
        for next_season in ('18', '19'):
            with self.subTest(season=next_season):
                app = Mock()
                app.current_season_no = '18'
                app.current_season_started_at = '2026-09-01 00:00:00'
                def dialog(**kwargs):
                    app.current_season_no = next_season
                    app.current_season_started_at = '2026-09-29 00:00:00'
                    return True
                app._show_season_setup_dialog.side_effect = dialog
                app._archive_preseason_records_for_season.return_value = 0
                gui.BossTimerApp._open_new_season_dialog(app)
                app._reset_schedule_for_new_season.assert_not_called()
                app._archive_current_schedule_for_season.assert_not_called()

    def test_existing_empty_season_requires_confirmation_and_can_cancel_without_writes(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text(json.dumps(dict(schedule_events=[], schedule_active_entries=[], schedule_control_events=[])))
        app = Mock()
        app.schedule_server_profile_id = '7'
        app._build_github_server_entry_from_metadata.return_value = self.entry
        app._normalize_schedule_server_profile_id.side_effect = gui.BossTimerApp._normalize_schedule_server_profile_id
        app._normalize_schedule_server_profile_season_key.return_value = 'season_19'
        app._get_active_schedule_server_profile_season_key.return_value = 'season_18'
        app._confirm_existing_new_season_profile.return_value = False
        with patch.object(gui, 'get_user_config_dir', return_value=str(self.root)), \
             patch('schedule_profile_migration.seed_profile') as seed, \
             patch('schedule_profile_migration.ensure_profile_defaults') as defaults:
            self.assertFalse(gui.BossTimerApp._prepare_schedule_profile_for_new_season(app, '19', '오7', '길드'))
        app._confirm_existing_new_season_profile.assert_called_once_with(self.target.parent, parent=None)
        app._save_schedule_alarm_settings.assert_not_called()
        app._save_settings.assert_not_called()
        seed.assert_not_called()
        defaults.assert_not_called()

    def test_existing_season_prompt_shows_empty_schedule_and_disabled_chimes_with_no_default(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text('{}')
        (self.target.parent / 'schedule_alarm_settings.json').write_text(json.dumps(
            dict(chime_settings=dict(general='', fixed='', rapid_chain=''))))
        self.app.schedule_events = self.snapshot['schedule_events']
        self.app.schedule_active_entries = self.snapshot['schedule_active_entries']
        self.app.schedule_control_events = self.snapshot['schedule_control_events']
        self.app.schedule_alarm_chime_settings = dict(general='wave/current.wav')
        self.app._show_centered_messagebox = Mock(return_value=False)
        self.assertFalse(self.app._confirm_existing_new_season_profile(self.target.parent))
        call = self.app._show_centered_messagebox.call_args
        self.assertIn('현재 화면: 스케줄 1건', call.args[2])
        self.assertIn('선택 시즌: 스케줄 0건', call.args[2])
        self.assertIn('current.wav', call.args[2])
        self.assertIn('사용 안 함', call.args[2])
        self.assertEqual(call.kwargs['default'], 'no')

    def test_existing_unreadable_or_invalid_schedule_blocks_season_change(self):
        self.target.parent.mkdir(parents=True)
        self.target.write_text('broken json')
        self.app._show_centered_messagebox = Mock()
        with self.assertRaises(ValueError):
            self.app._confirm_existing_new_season_profile(self.target.parent)
        self.app._show_centered_messagebox.assert_not_called()

    def test_alarm_save_failure_blocks_new_season_before_seeding(self):
        app = Mock()
        app.schedule_server_profile_id = '9'
        app._build_github_server_entry_from_metadata.return_value = self.entry
        app._normalize_schedule_server_profile_id.side_effect = gui.BossTimerApp._normalize_schedule_server_profile_id
        app._normalize_schedule_server_profile_season_key.return_value = 'season_19'
        app._get_active_schedule_server_profile_season_key.return_value = 'season_18'
        app._save_schedule_alarm_settings.return_value = False
        with patch.object(gui, 'get_user_config_dir', return_value=str(self.root)), \
             patch('schedule_profile_migration.seed_profile') as seed:
            with self.assertRaises(OSError):
                gui.BossTimerApp._prepare_schedule_profile_for_new_season(app, '19', '오7', '길드')
        seed.assert_not_called()


if __name__ == '__main__':
    unittest.main()
