import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from schedule_profile_migration import seed_profile, copy_baseline, ensure_profile_defaults, DEFAULT_GROUPS, build_distribution_baseline


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boss-profile-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def baseline(self, profile="season_17/9"):
        origin = self.root / profile
        relative = "init/schedule_fixed_bosses.txt"
        baseline = origin / "settings_rollback/baseline"
        self.put(f"{profile}/settings_rollback/baseline/{relative}", "fixed rollback")
        self.put(f"{profile}/settings_rollback/baseline/manifest.json", json.dumps({
            "schema_version": 1, "server_id": "9", "season_key": "season_17",
            "groups": {"fixed": [{"source": str(origin / relative), "snapshot": relative, "exists": True}]},
        }))
        return baseline

    def test_fresh_server_has_every_shipped_setting_before_any_window_opens(self):
        resources = Path(__file__).parent / "init"
        for server in ("9", "7"):
            profile = self.root / "season_18" / server
            ensure_profile_defaults(profile, resources)
            for relatives in DEFAULT_GROUPS.values():
                for relative in relatives:
                    filename = "default_schedule_alarm_settings.json" if relative == "schedule_alarm_settings.json" else Path(relative).name
                    self.assertEqual((profile / relative).read_bytes(), (resources / filename).read_bytes())
            self.assertFalse((profile / "schedule_state.json").exists())
            manifest = json.loads((profile / "settings_rollback/baseline/manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest["groups"]["fixed"][0]["exists"])
            self.assertEqual(manifest["source_type"], "distribution_defaults")

    def test_defaults_fill_only_missing_files_and_preserve_existing_baseline(self):
        profile = self.root / "season_18/9"
        self.put("season_18/9/init/schedule_fixed_bosses.txt", "")  # Intentional empty file is preserved.
        self.put("season_18/9/schedule_state.json", "my schedule")
        baseline = self.baseline("season_18/9")
        original = (baseline / "manifest.json").read_bytes()
        ensure_profile_defaults(profile, Path(__file__).parent / "init")
        self.assertEqual((profile / "init/schedule_fixed_bosses.txt").read_text(), "")
        self.assertEqual((profile / "schedule_state.json").read_text(), "my schedule")
        self.assertEqual((baseline / "manifest.json").read_bytes(), original)

    def test_first_baseline_does_not_capture_already_reset_live_values(self):
        profile = self.root / "season_18/9"
        self.put("season_18/9/init/schedule_fixed_bosses.txt", "already reset")
        ensure_profile_defaults(profile, Path(__file__).parent / "init")
        snapshot = profile / "settings_rollback/baseline/init/schedule_fixed_bosses.txt"
        self.assertEqual(snapshot.read_bytes(), (Path(__file__).parent / "init/schedule_fixed_bosses.txt").read_bytes())

    def test_incomplete_distribution_cannot_seed_partial_settings(self):
        resources = self.root / "incomplete_resources"
        resources.mkdir()
        with self.assertRaises(ValueError):
            ensure_profile_defaults(self.root / "season_18/9", resources)
        self.assertFalse((self.root / "season_18/9").exists())

    def test_new_season_inherits_settings_not_schedule_or_old_flat_data(self):
        self.put("9/init/schedule_fixed_bosses.txt", "stale")
        self.put("season_17/9/init/schedule_fixed_bosses.txt", "current")
        self.put("season_17/9/schedule_alarm_settings.json", "alarm")
        self.put("season_17/9/schedule_state.json", "must not carry schedule")
        self.put("season_17/9/schedule_delete_history.json", "must not carry history")
        self.baseline()
        source = seed_profile(self.root, "season_18", "9")
        self.assertEqual(Path(source), self.root / "season_17/9")
        target = self.root / "season_18/9"
        self.assertEqual((target / "init/schedule_fixed_bosses.txt").read_text(), "current")
        self.assertFalse((target / "schedule_state.json").exists())
        self.assertFalse((target / "schedule_delete_history.json").exists())
        manifest = json.loads((target / "settings_rollback/baseline/manifest.json").read_text())
        self.assertEqual(manifest["season_key"], "season_18")
        self.assertEqual(manifest["groups"]["fixed"][0]["source"], str(target / "init/schedule_fixed_bosses.txt"))

    def test_existing_profile_is_untouched(self):
        self.put("season_18/9/schedule_state.json", "new schedule")
        self.put("season_17/9/init/schedule_fixed_bosses.txt", "old")
        self.assertIsNone(seed_profile(self.root, "season_18", "9"))
        self.assertFalse((self.root / "season_18/9/init").exists())

    def test_first_legacy_migration_keeps_schedule(self):
        self.put("9/schedule_state.json", "legacy schedule")
        self.baseline("9")
        seed_profile(self.root, "season_17", "9")
        self.assertEqual((self.root / "season_17/9/schedule_state.json").read_text(), "legacy schedule")
        manifest = json.loads((self.root / "season_17/9/settings_rollback/baseline/manifest.json").read_text())
        self.assertEqual(manifest["groups"]["fixed"][0]["source"], str(self.root / "season_17/9/init/schedule_fixed_bosses.txt"))

    def test_no_other_server_or_future_season_fallback(self):
        self.put("season_19/9/init/schedule_fixed_bosses.txt", "future")
        self.put("9/init/schedule_fixed_bosses.txt", "stale")
        self.put("season_17/8/init/schedule_fixed_bosses.txt", "other server")
        self.assertIsNone(seed_profile(self.root, "season_18", "9"))

    def test_prefers_explicit_previous_active_season(self):
        self.put("season_17/9/init/schedule_fixed_bosses.txt", "active")
        self.put("season_18/9/init/schedule_fixed_bosses.txt", "older trial season")
        seed_profile(self.root, "season_20", "9", preferred_season_key="season_17")
        self.assertEqual((self.root / "season_20/9/init/schedule_fixed_bosses.txt").read_text(), "active")

    def test_failed_copy_does_not_publish_partial_profile(self):
        self.put("season_17/9/init/schedule_fixed_bosses.txt", "current")
        with patch("schedule_profile_migration.shutil.copy2", side_effect=OSError("copy failed")):
            with self.assertRaises(OSError):
                seed_profile(self.root, "season_18", "9")
        self.assertFalse((self.root / "season_18/9").exists())

    def test_baseline_rejects_other_server(self):
        self.baseline()
        with self.assertRaises(ValueError):
            copy_baseline(self.root / "season_17/9", self.root / "season_18/8", self.root / "staged")


if __name__ == "__main__":
    unittest.main()
