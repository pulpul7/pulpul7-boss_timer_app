import ast
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace, MethodType
import unittest
from unittest.mock import patch

from runtime_storage import migrate_legacy_data, copy_missing, atomic_write, MIGRATION_MARKER


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boss-storage-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old, self.data = self.root / "old", self.root / "AppData"
        self.old.mkdir()

    def put(self, path, content="saved"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_new_release_reuses_data_and_leaves_originals(self):
        self.put(self.old / "boss_timer_settings.ini", "[settings]\ncurrent_season_no=18\n")
        self.put(self.old / "season_history.json", '{"18":{"guild_name":"길드"}}')
        self.put(self.old / "notice_data/9/events.json", "events")
        self.put(self.old / "shared_schedules/main/schedule.json", "schedule")
        (self.old / "archive_logs/season_18").mkdir(parents=True)
        profile = self.data / "server_profiles/season_18/9/schedule_state.json"
        self.put(profile, "real profile")
        migrate_legacy_data(self.old, self.data)
        self.assertEqual((self.data / "boss_timer_settings.ini").read_bytes(),
                         (self.old / "boss_timer_settings.ini").read_bytes())
        self.assertTrue((self.data / "archive_logs/season_18").is_dir())
        new = self.root / "new-release"
        self.put(new / "boss_timer_settings.ini", "empty release defaults")
        self.assertEqual(migrate_legacy_data(new, self.data), [])
        self.assertIn("current_season_no=18", (self.data / "boss_timer_settings.ini").read_text())
        self.assertEqual(profile.read_text(), "real profile")
        self.assertEqual((self.data / "notice_data/9/events.json").read_text(), "events")

    def test_existing_even_empty_is_not_overwritten(self):
        self.put(self.old / "schedule_state.json", "old schedule")
        self.put(self.data / "schedule_state.json", "")
        migrate_legacy_data(self.old, self.data)
        self.assertEqual((self.data / "schedule_state.json").read_text(), "")
        (self.data / "schedule_state.json").unlink()
        migrate_legacy_data(self.old, self.data)
        self.assertFalse((self.data / "schedule_state.json").exists())

    def test_failed_copy_does_not_mark_success_or_erase_original(self):
        self.put(self.old / "boss_timer_settings.ini", "original")
        with patch("runtime_storage.os.link", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                migrate_legacy_data(self.old, self.data)
        self.assertFalse((self.data / MIGRATION_MARKER).exists())
        self.assertFalse((self.data / "boss_timer_settings.ini").exists())
        self.assertEqual((self.old / "boss_timer_settings.ini").read_text(), "original")
        self.assertFalse(list(self.data.glob(".migrate-*")))

    def test_concurrent_destination_is_never_overwritten(self):
        self.put(self.old / "file", "old")
        target = self.data / "file"
        original_link = os.link
        def competing_link(source, destination):
            self.put(Path(destination), "newer writer")
            original_link(source, destination)
        with patch("runtime_storage.os.link", side_effect=competing_link):
            self.assertFalse(copy_missing(self.old / "file", target))
        self.assertEqual(target.read_text(), "newer writer")

    def test_atomic_save_failure_preserves_existing(self):
        self.put(self.data / "settings.ini", "old")
        with patch("runtime_storage.os.replace", side_effect=PermissionError("busy")):
            with self.assertRaises(PermissionError):
                atomic_write(self.data / "settings.ini", b"new")
        self.assertEqual((self.data / "settings.ini").read_text(), "old")
        self.assertFalse(list(self.data.glob(".save-*")))


class GuiStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(Path(__file__).with_name("boss_timer_gui.py").read_text(encoding="utf-8-sig"))

    def method(self, name, namespace):
        node = next(n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), "gui-method", "exec"), namespace)
        return namespace[name]

    def test_upgrade_never_removes_user_settings(self):
        method = self.method("_reset_outdated_runtime_config_files_for_upgrade", {})
        self.assertFalse(method(SimpleNamespace()))

    def test_migration_precedes_profile_and_defaults(self):
        node = next(n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef)
                    and n.name == "__init__" and "migrate_legacy_data" in ast.unparse(n))
        source = ast.unparse(node)
        self.assertLess(source.index("migrate_legacy_data(get_app_root"), source.index("self._load_schedule_server_profile_selection()"))
        self.assertLess(source.index("migrate_legacy_data(get_app_root"), source.index("self._seed_runtime_default_files_from_resource_init()"))

    def test_persistent_paths_not_executable_folder(self):
        for name in ("CONFIG_PATH", "SEASON_HISTORY_PATH", "SCHEDULE_STATE_PATH", "INIT_DIR",
                     "SCHEDULE_ALARM_SETTINGS_PATH", "RECORD_BOOK_PATH", "SCHEDULE_OCR_CORRECTIONS_PATH"):
            assignment = next(n for n in self.tree.body if isinstance(n, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
            self.assertIn("get_user_config_dir()", ast.unparse(assignment.value), name)

    def test_chime_missing_is_preserved_and_relocated_file_resolves(self):
        with tempfile.TemporaryDirectory(prefix="boss-chime-unit-") as temporary:
            root = Path(temporary)
            (root / "wave").mkdir()
            (root / "wave/chime.wav").write_bytes(b"fixture")
            namespace = dict(os=os, Path=Path, get_app_root=lambda: str(root), get_resource_root=lambda: str(root),
                             SCHEDULE_ALARM_CHIME_DIR=str(root / "wave"), SCHEDULE_ALARM_VOICE_AUDIO_EXTENSIONS=(".wav",),
                             SCHEDULE_ALARM_CHIME_TYPES=(("general", ""),),
                             SCHEDULE_ALARM_DEFAULT_CHIME_PATHS={"general": "wave/chime.wav"})
            app = SimpleNamespace()
            for name in ("_resolve_schedule_alarm_chime_path", "_normalize_schedule_alarm_chime_settings"):
                setattr(app, name, MethodType(self.method(name, namespace), app))
            self.assertEqual(app._normalize_schedule_alarm_chime_settings({"general": "wave/missing.wav"})["general"], "wave/missing.wav")
            self.assertEqual(app._normalize_schedule_alarm_chime_settings({"general": ""})["general"], "")
            self.assertEqual(app._normalize_schedule_alarm_chime_settings({})["general"], str(root / "wave/chime.wav"))
            old = str(root / "removed-release/wave/chime.wav")
            self.assertEqual(app._resolve_schedule_alarm_chime_path(old), str(root / "wave/chime.wav"))


if __name__ == "__main__":
    unittest.main()
