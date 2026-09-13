"""Safety checks for switching schedule-server profiles."""

from __future__ import annotations

import unittest
import tempfile
from types import MethodType
from pathlib import Path
from unittest.mock import Mock, patch

import boss_timer_gui as gui


class ScheduleServerProfileSwitchTests(unittest.TestCase):
    def test_new_season_uses_explicit_setup_server_instead_of_old_dropdown(self):
        app = Mock()
        app.schedule_server_profile_id = "9"
        app.schedule_server_profile_name = "오9"
        app._normalize_schedule_server_profile_id.side_effect = gui.BossTimerApp._normalize_schedule_server_profile_id
        app._get_current_github_upload_server_entry.return_value = {"id": "오7", "name": "오7"}
        app._get_current_schedule_server_profile_season_key.return_value = "season_19"
        app._get_active_schedule_server_profile_season_key.return_value = "season_18"
        app.discord_bot_expected_running = False
        app.discord_bot_last_status_payload = {}
        app._is_discord_bot_process_alive.return_value = False
        app._activate_schedule_server_profile.return_value = True
        self.assertTrue(gui.BossTimerApp._activate_current_server_profile_for_new_season(app))
        app._activate_schedule_server_profile.assert_called_once_with("7", "오7")
        app._sync_github_server_combo_to_loaded_meta.assert_called_once()

    def test_distribution_recovery_bypasses_broken_baseline_and_reloads_fixed_bosses(self):
        with tempfile.TemporaryDirectory(prefix="boss-default-restore-unit-") as directory:
            root = Path(directory)
            target = root / "init/schedule_fixed_bosses.txt"
            target.parent.mkdir()
            target.write_text("")
            config = root / "settings.ini"
            config.write_text("[settings]\ncurrent_season_no=18\n", encoding="utf-8")
            schedule = root / "schedule_state.json"
            schedule.write_text("keep user's schedule")
            app = Mock()
            app._get_settings_rollback_group_files.return_value = {"fixed": [(str(target), "init/schedule_fixed_bosses.txt")]}
            app._get_settings_rollback_root_dir.return_value = str(root / "backups")
            app._widget_available.return_value = False
            app.fixed_boss_window_open = app.schedule_boss_metrics_window_open = False
            app._is_settings_rollback_config_key.side_effect = gui.BossTimerApp._is_settings_rollback_config_key
            app._restore_settings_rollback_baseline = MethodType(gui.BossTimerApp._restore_settings_rollback_baseline, app)
            with patch.object(gui, "get_resource_root", return_value=str(Path(__file__).parent)), patch.object(gui, "CONFIG_PATH", str(config)):
                ok, message = app._restore_settings_rollback_baseline({"fixed"}, distribution_defaults=True)
            self.assertTrue(ok, message)
            app._load_settings_rollback_manifest.assert_not_called()
            self.assertEqual(target.read_bytes(), (Path(__file__).parent / "init/schedule_fixed_bosses.txt").read_bytes())
            self.assertEqual(schedule.read_text(), "keep user's schedule")
            self.assertIn("current_season_no = 18", config.read_text())
            reader = object.__new__(gui.BossTimerApp)
            reader._ensure_init_dir = Mock()
            reader._get_schedule_fixed_bosses_storage_path = lambda: str(target)
            reader._write_fixed_boss_definitions_file = Mock()
            self.assertGreater(len(reader._load_fixed_boss_definitions()), 0)

    def test_first_startup_selects_setup_server_without_opening_settings(self):
        app = Mock()
        app.schedule_server_profile_id = ""
        app._has_ready_season.return_value = True
        app._can_seed_startup_schedule.return_value = False
        entry = {"id": "오9", "name": "오9"}
        app._get_current_github_upload_server_entry.return_value = entry
        gui.BossTimerApp._ensure_startup_season_configuration(app)
        app._select_github_server_entry.assert_called_once_with(entry)

    def test_profile_initialization_uses_complete_distribution_without_ui(self):
        with tempfile.TemporaryDirectory(prefix="boss-first-profile-unit-") as directory:
            root = Path(directory)
            app = Mock()
            app._normalize_schedule_server_profile_id.side_effect = gui.BossTimerApp._normalize_schedule_server_profile_id
            target = root / "server_profiles/season_18/9"
            app._get_schedule_server_profile_dir.return_value = str(target)
            app._get_active_schedule_server_profile_season_key.return_value = "season_18"
            with patch.object(gui, "get_user_config_dir", return_value=str(root)), patch.object(gui, "get_resource_root", return_value=str(Path(__file__).parent)):
                gui.BossTimerApp._migrate_flat_server_profile_to_season_profile(app, "오9")
            self.assertEqual((target / "init/schedule_fixed_bosses.txt").read_bytes(), (Path(__file__).parent / "init/schedule_fixed_bosses.txt").read_bytes())
            self.assertTrue((target / "init/schedule_break_rules.json").is_file())
            self.assertTrue((target / "schedule_alarm_settings.json").is_file())

    def test_switch_and_restore_refresh_all_open_settings_views(self):
        app = Mock()
        app._widget_available.return_value = True
        gui.BossTimerApp._refresh_profile_settings_views(app)
        for method in (app._refresh_fixed_boss_list, app._refresh_schedule_break_list,
                       app._populate_schedule_boss_metrics_tree, app._refresh_schedule_alarm_window,
                       app._populate_schedule_boss_definition_tree):
            method.assert_called_once()
        app.schedule_boss_draft_definitions.clear.assert_called_once()
        app.schedule_boss_metric_draft_entries.clear.assert_called_once()

    def test_rollback_restores_current_profile_not_stored_absolute_source(self):
        with tempfile.TemporaryDirectory(prefix="boss-rollback-unit-") as directory:
            root = Path(directory)
            old = root / "old.txt"
            current = root / "current.txt"
            baseline = root / "baseline"
            baseline.mkdir()
            old.write_text("old season stays intact")
            current.write_text("current before restore")
            (baseline / "boss.txt").write_text("rollback value")
            app = Mock()
            app._load_settings_rollback_manifest.return_value = ({"groups": {"boss": [
                {"source": str(old), "snapshot": "boss.txt", "exists": True}]}}, str(baseline))
            app._get_settings_rollback_group_files.return_value = {"boss": [(str(current), "boss.txt")]}
            app._get_settings_rollback_root_dir.return_value = str(root / "backups")
            app._widget_available.return_value = False
            app.fixed_boss_window_open = app.schedule_boss_metrics_window_open = False
            ok, _ = gui.BossTimerApp._restore_settings_rollback_baseline(app, {"boss"})
            self.assertTrue(ok)
            self.assertEqual(current.read_text(), "rollback value")
            self.assertEqual(old.read_text(), "old season stays intact")

    def test_missing_rollback_snapshot_is_rejected_before_overwriting(self):
        with tempfile.TemporaryDirectory(prefix="boss-rollback-unit-") as directory:
            root = Path(directory)
            current = root / "current.txt"
            current.write_text("keep")
            app = Mock()
            app._load_settings_rollback_manifest.return_value = ({"groups": {"boss": [
                {"source": str(current), "snapshot": "missing.txt", "exists": True}]}}, str(root))
            app._get_settings_rollback_group_files.return_value = {"boss": [(str(current), "missing.txt")]}
            ok, _ = gui.BossTimerApp._restore_settings_rollback_baseline(app, {"boss"})
            self.assertFalse(ok)
            self.assertEqual(current.read_text(), "keep")
            app._get_settings_rollback_root_dir.assert_not_called()

    def test_absent_baseline_file_never_reports_successful_rollback(self):
        app = Mock()
        app._load_settings_rollback_manifest.return_value = ({"groups": {"fixed": [
            {"snapshot": "fixed.txt", "exists": False}]}}, "unused")
        app._get_settings_rollback_group_files.return_value = {"fixed": [("unused/fixed.txt", "fixed.txt")]}
        ok, message = gui.BossTimerApp._restore_settings_rollback_baseline(app, {"fixed"})
        self.assertFalse(ok)
        self.assertIn("배포 기본값", message)
        app._get_settings_rollback_root_dir.assert_not_called()

    def _selection_app(self, profile_id="9", meta=None):
        app = object.__new__(gui.BossTimerApp)
        app.schedule_server_profile_id = profile_id
        app.schedule_server_profile_name = "오" + profile_id if profile_id else ""
        app.schedule_github_server_entries = [{"id": "오8", "name": "오8"}, {"id": "오9", "name": "오9"}]
        app.schedule_github_server_var = Mock()
        app.schedule_github_server_combo = None
        app.schedule_last_import_meta = meta
        app._upsert_github_server_entry_locally = Mock()
        app._activate_schedule_server_profile = Mock()
        app._save_schedule_state = Mock()
        return app

    def test_empty_new_season_restores_active_server_display(self):
        app = self._selection_app()
        self.assertTrue(app._sync_github_server_combo_to_loaded_meta())
        app.schedule_github_server_var.set.assert_called_with(app._format_github_server_combo_text("오9"))
        app._save_schedule_state.assert_not_called()
        app._activate_schedule_server_profile.assert_not_called()
        app._upsert_github_server_entry_locally.assert_not_called()

    def test_active_profile_wins_over_stale_import_metadata(self):
        app = self._selection_app("8", {"github_server_id": "오9", "server_name": "오9"})
        self.assertTrue(app._sync_github_server_combo_to_loaded_meta())
        app.schedule_github_server_var.set.assert_called_with(app._format_github_server_combo_text("오8"))

    def test_legacy_without_profile_can_still_use_import_metadata(self):
        app = self._selection_app("", {"github_server_id": "오9", "server_name": "오9"})
        self.assertTrue(app._sync_github_server_combo_to_loaded_meta())
        app.schedule_github_server_var.set.assert_called_with(app._format_github_server_combo_text("오9"))

    def test_existing_profile_is_never_seeded_from_cache(self) -> None:
        self.assertFalse(gui.BossTimerApp._should_seed_schedule_server_profile_from_cache(True))

    def test_new_profile_can_be_seeded_from_cache(self) -> None:
        self.assertTrue(gui.BossTimerApp._should_seed_schedule_server_profile_from_cache(False))


if __name__ == "__main__":
    unittest.main()
