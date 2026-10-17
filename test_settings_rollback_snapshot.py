"""Current rollback baselines, backup/failure recovery and next-season inheritance."""
import json
from pathlib import Path
import shutil
import tempfile
from types import MethodType
import unittest
from unittest.mock import Mock, patch

import boss_timer_gui as gui
from schedule_profile_migration import DEFAULT_GROUPS, build_distribution_baseline, seed_profile
from settings_rollback_snapshot import refresh_baseline


class CurrentRollbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rollback-snapshot-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = self.root / 'server_profiles/season_18/9'
        self.baseline = self.profile / 'settings_rollback/baseline'
        self.groups = {group: [(str(self.profile / name), name) for name in names]
                       for group, names in DEFAULT_GROUPS.items()}
        for entries in self.groups.values():
            for source, relative in entries:
                target = Path(source)
                target.parent.mkdir(parents=True, exist_ok=True)
                shipped = Path('init') / ('default_schedule_alarm_settings.json'
                                          if relative == 'schedule_alarm_settings.json' else Path(relative).name)
                shutil.copy2(shipped, target)
        self.config = self.root / 'settings.ini'
        self.config.write_text('[settings]\nschedule_color_data_enabled=false\nschedule_break_rows_enabled=true\n'
                               'current_season_no=18\ndiscord_token=SECRET\n', encoding='utf-8')
        build_distribution_baseline(Path('init'), self.baseline, self.groups, server_id='9', season_key='season_18')
        self.fixed = self.profile / 'init/schedule_fixed_bosses.txt'
        self.fixed.write_bytes(self.fixed.read_bytes() + b'\n# current rollback marker\n')
        self.key_filter = gui.BossTimerApp._is_settings_rollback_config_key

    def refresh(self, **kwargs):
        return refresh_baseline(self.baseline, self.groups, self.config, self.key_filter,
                                server_id='9', season_key='season_18', **kwargs)

    def test_captures_current_files_and_scoped_config_and_preserves_previous_baseline(self):
        old = (self.baseline / 'manifest.json').read_bytes()
        live = self.fixed.read_bytes()
        result = self.refresh()
        self.assertEqual((Path(result['backup']) / 'manifest.json').read_bytes(), old)
        self.assertEqual((self.baseline / 'init/schedule_fixed_bosses.txt').read_bytes(), live)
        self.assertEqual(self.fixed.read_bytes(), live)
        config = (self.baseline / 'boss_timer_settings.ini').read_text(encoding='utf-8')
        self.assertIn('schedule_color_data_enabled = false', config)
        self.assertNotIn('SECRET', config)
        self.assertNotIn('current_season_no', config)
        self.assertEqual(json.loads((self.baseline / 'manifest.json').read_bytes())['source_type'], 'current_settings')

    def test_next_season_inherits_refreshed_baseline_and_restore_uses_it(self):
        expected = self.fixed.read_bytes()
        self.refresh()
        seed_profile(self.root / 'server_profiles', 'season_19', '9', preferred_season_key='season_18')
        target_profile = self.root / 'server_profiles/season_19/9'
        target_baseline = target_profile / 'settings_rollback/baseline'
        manifest = json.loads((target_baseline / 'manifest.json').read_bytes())
        self.assertEqual(manifest['season_key'], 'season_19')
        target = target_profile / 'init/schedule_fixed_bosses.txt'
        self.assertEqual(manifest['groups']['fixed'][0]['source'], str(target))
        target.write_bytes(b'# changed after new season\n')
        app = Mock()
        app._get_settings_rollback_group_files.return_value = {'fixed': [(str(target), 'init/schedule_fixed_bosses.txt')]}
        app._get_settings_rollback_root_dir.return_value = str(target_baseline.parent)
        app._load_settings_rollback_manifest.return_value = manifest, str(target_baseline)
        app._is_settings_rollback_config_key.side_effect = self.key_filter
        app._widget_available.return_value = False
        app.fixed_boss_window_open = app.schedule_boss_metrics_window_open = False
        with patch.object(gui, 'CONFIG_PATH', str(self.config)):
            ok, message = gui.BossTimerApp._restore_settings_rollback_baseline(app, {'fixed'})
        self.assertTrue(ok, message)
        self.assertEqual(target.read_bytes(), expected)

    def test_missing_input_leaves_previous_baseline_untouched(self):
        old = (self.baseline / 'manifest.json').read_bytes()
        self.fixed.unlink()
        with self.assertRaises(FileNotFoundError):
            self.refresh()
        self.assertEqual((self.baseline / 'manifest.json').read_bytes(), old)
        self.assertFalse(list(self.baseline.parent.glob('before_refresh_*')))

    def test_failed_publication_puts_previous_baseline_back(self):
        old = (self.baseline / 'manifest.json').read_bytes()
        rename = Path.rename

        def fail_stage(path, target):
            if path.parent.name.startswith('.baseline-refresh-'):
                raise OSError('injected publish failure')
            return rename(path, target)

        with patch.object(Path, 'rename', fail_stage), self.assertRaises(OSError):
            self.refresh()
        self.assertEqual((self.baseline / 'manifest.json').read_bytes(), old)
        self.assertFalse(list(self.baseline.parent.glob('.baseline-refresh-*')))

    def test_notice_snapshot_contains_preferences_without_event_history(self):
        notice = self.root / 'notice.json'
        prefs = json.loads(Path('init/default_notice_settings.json').read_bytes())
        notice.write_text(json.dumps(dict(prefs, events={'secret': 'history'}, delivery_ledger=['private'])), encoding='utf-8')
        self.refresh(notice=notice)
        snapshot = json.loads((self.baseline / 'notice_settings.json').read_bytes())
        self.assertNotIn('events', snapshot)
        self.assertNotIn('delivery_ledger', snapshot)
        self.assertEqual(notice.read_text(encoding='utf-8').count('private'), 1)

    def test_gui_master_guard_and_alarm_save_failure_do_not_refresh(self):
        app = Mock()
        app._refresh_settings_rollback_baseline = MethodType(gui.BossTimerApp._refresh_settings_rollback_baseline, app)
        app._is_master_developer_mode_enabled.return_value = False
        self.assertFalse(app._refresh_settings_rollback_baseline()[0])
        app._save_settings.assert_not_called()
        app._is_master_developer_mode_enabled.return_value = True
        app.schedule_server_profile_id = '9'
        app._save_schedule_alarm_settings.return_value = False
        self.assertFalse(app._refresh_settings_rollback_baseline()[0])
        app._get_settings_rollback_group_files.assert_not_called()

    def test_gui_refresh_saves_live_preferences_and_handles_unmodified_voice_defaults(self):
        app = Mock()
        app._is_master_developer_mode_enabled.return_value = True
        app.schedule_server_profile_id = '9'
        app._save_schedule_alarm_settings.return_value = True
        groups = {key: list(value) for key, value in self.groups.items()}
        groups['alarm'].append((str(self.root / 'missing_edge.ini'), 'global/edge_tts.ini'))
        app._get_settings_rollback_group_files.return_value = groups
        app._get_settings_rollback_baseline_dir.return_value = str(self.baseline)
        app._get_active_schedule_server_profile_season_key.return_value = 'season_18'
        app._get_distribution_settings_sources.return_value = {'notice': None}
        app._is_settings_rollback_config_key.side_effect = self.key_filter
        with patch.object(gui, 'CONFIG_PATH', str(self.config)), patch.object(gui, 'get_resource_root', return_value=str(Path.cwd())):
            ok, message = gui.BossTimerApp._refresh_settings_rollback_baseline(app)
        self.assertTrue(ok, message)
        app._save_settings.assert_called_once()
        app._save_schedule_alarm_settings.assert_called_once()
        self.assertEqual((self.baseline / 'global/edge_tts.ini').read_bytes(), Path('init/default_edge_tts.ini').read_bytes())


if __name__ == '__main__':
    unittest.main()
