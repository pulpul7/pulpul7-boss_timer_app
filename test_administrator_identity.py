import ast
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid

from administrator_identity import AdministratorIdentity, FILENAME, display_identity
from discord_connection_policy import ConnectionPolicy


class IdentityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-identity-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.store = AdministratorIdentity(self.root)

    def test_existing_active_profile_id_migrated_exactly_and_state_preserved(self):
        config = self.root / "server_profiles/season_18/9/discord_bot.ini"
        policy = ConnectionPolicy(config)
        client = uuid.uuid4().hex
        policy.update(client_id=client, retries=7, standby=True)
        before = policy.path.read_bytes()
        self.assertEqual(self.store.load_or_create(config)["client_id"], client)
        self.assertEqual(before, policy.path.read_bytes())

    def test_rename_season_server_and_release_keep_same_id(self):
        old = self.store.save_name("관리자1", "길드A")
        config = self.root / "server_profiles/season_19/7/discord_bot.ini"
        ConnectionPolicy(config).update(client_id=uuid.uuid4().hex)
        renamed = AdministratorIdentity(self.root).save_name("관리자2", "길드B", config)
        self.assertEqual(renamed["client_id"], old["client_id"])
        self.assertEqual(renamed["identifier"], "관리자2_" + old["client_id"])
        self.assertEqual(renamed["guild_name"], "길드B")
        self.assertEqual(display_identity(renamed, short=True), "관리자2_" + old["client_id"][:8])

    def test_two_pcs_same_name_get_distinct_ids(self):
        first = self.store.save_name("동일 이름")
        second = AdministratorIdentity(self.root / "pc2").save_name("동일 이름")
        self.assertNotEqual(first["client_id"], second["client_id"])

    def test_corrupt_identity_never_regenerated(self):
        self.store.path.write_text("broken", encoding="utf-8")
        for operation in (self.store.load_or_create, lambda: self.store.save_name("관리자")):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(self.store.path.read_text(), "broken")

    def test_bad_legacy_file_not_silently_discarded(self):
        config = self.root / "discord_bot.ini"
        Path(str(config) + ".connection.json").write_text("broken")
        with self.assertRaises(ValueError):
            self.store.load_or_create(config)
        self.assertFalse(self.store.path.exists())

    def test_invalid_name_does_not_modify_saved_identity(self):
        self.store.save_name("원본")
        before = self.store.path.read_bytes()
        for name in ("", " \n ", "a" * 41):
            with self.assertRaises(ValueError):
                self.store.save_name(name)
        self.assertEqual(before, self.store.path.read_bytes())

    def test_concurrent_creation_issues_only_one_id(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: AdministratorIdentity(self.root).load_or_create()["client_id"], range(8)))
        self.assertEqual(len(set(ids)), 1)

    def test_save_failure_does_not_destroy_old_identity(self):
        self.store.save_name("원본")
        before = self.store.path.read_bytes()
        with patch("runtime_storage.os.replace", side_effect=OSError("denied")):
            with self.assertRaises(OSError):
                self.store.save_name("변경")
        self.assertEqual(before, self.store.path.read_bytes())


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(Path(__file__).with_name("boss_timer_gui.py").read_text(encoding="utf-8-sig"))

    def method(self, name):
        return next(n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef) and n.name == name)

    def test_season_name_saved_before_profile_changes_and_defaults_exclude_identity(self):
        source = ast.unparse(self.method("_show_season_setup_dialog"))
        self.assertIn("value=identity['name']", source)
        self.assertIn("normalize_name(administrator_name_var.get())", source)
        self.assertLess(source.index("self._save_administrator_identity("), source.index("self.current_season_no = season_text"))
        self.assertIn("get_user_config_dir()", ast.unparse(self.method("_get_administrator_identity")))
        from runtime_storage import LEGACY_FILES, LEGACY_DIRS
        from schedule_profile_migration import SETTINGS_FILES
        self.assertNotIn(FILENAME, (*LEGACY_FILES, *LEGACY_DIRS, *SETTINGS_FILES))

    def test_existing_season_prompt_does_not_change_season_or_schedule(self):
        source = ast.unparse(self.method("_ensure_administrator_name"))
        self.assertNotIn("_reset_schedule", source)
        self.assertNotIn("_show_season_setup_dialog", source)
        self.assertIn("if identity['name']:", source)
        self.assertIn("return True", source)

    def test_distribution_guard_rejects_identity_and_lock(self):
        tree = ast.parse(Path(__file__).with_name("boss_timer_gui.spec").read_text(encoding="utf-8-sig"))
        constant = next(n for n in tree.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "DISTRIBUTION_PRIVATE_RUNTIME_FILENAMES" for t in n.targets))
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "assert_distribution_has_no_private_runtime_data")
        namespace = dict(Path=Path, DISTRIBUTION_PRIVATE_RUNTIME_FILENAMES=ast.literal_eval(constant.value))
        exec(compile(ast.Module(body=[function], type_ignores=[]), "packaging-guard", "exec"), namespace)
        for name in (FILENAME, FILENAME + ".lock"):
            with self.assertRaises(RuntimeError):
                namespace[function.name]([(name, ".")])


class SeasonDialogTests(unittest.TestCase):
    """Exercise the real dialog's callbacks without opening any Tk windows."""
    def run_dialog(self, *, name="담당자", cancel=False, save_error=False, profile_error=False, profile_cancel=False):
        import boss_timer_gui as gui
        from contextlib import ExitStack
        class Variable:
            def __init__(self, value=""):
                self.value = value
            def get(self):
                return self.value
            def set(self, value):
                self.value = value
            def trace_add(self, *_args):
                pass
        variables, buttons = [], {}
        def variable(**kwargs):
            value = Variable(**kwargs)
            variables.append(value)
            return value
        def button(*args, **kwargs):
            buttons[kwargs["text"]] = kwargs["command"]
            return Mock()
        app = Mock()
        app.current_season_no = "18"
        app.current_season_started_at = "2026-09-01 00:00:00"
        app.season_history_map = {"18": {"guild_name": "길드", "server_name": "오9"}}
        app._sanitize_season_metadata_text.side_effect = lambda value: str(value or "").strip()
        app._get_next_season_number_text.return_value = "19"
        app._get_existing_archive_season_numbers.return_value = set()
        app._has_active_season.return_value = True
        app._get_administrator_identity.return_value = dict(client_id="a" * 32, name="기존담당자")
        if save_error:
            app._save_administrator_identity.side_effect = OSError("save failed")
        if profile_error:
            app._prepare_schedule_profile_for_new_season.side_effect = PermissionError(13, 'Permission denied', 'boss-settings.txt')
        if profile_cancel:
            app._prepare_schedule_profile_for_new_season.return_value = False
        dialog = Mock()
        def interact():
            # Name is the last StringVar; it is prefilled from global identity.
            self.assertEqual(variables[-1].get(), "기존담당자")
            variables[-1].set(name)
            buttons["취소" if cancel else "시작"]()
        dialog.wait_window.side_effect = interact
        with ExitStack() as stack:
            stack.enter_context(patch.object(gui.tk, "Toplevel", return_value=dialog))
            stack.enter_context(patch.object(gui.tk, "StringVar", side_effect=variable))
            stack.enter_context(patch.object(gui.tk, "Button", side_effect=button))
            for widget in ("Label", "Entry"):
                stack.enter_context(patch.object(gui.tk, widget))
            stack.enter_context(patch.object(gui.ttk, "Combobox"))
            result = gui.BossTimerApp._show_season_setup_dialog(app)
        return app, result

    def test_name_saved_with_guild_and_season_then_activated(self):
        app, result = self.run_dialog()
        self.assertTrue(result)
        app._save_administrator_identity.assert_called_once_with("담당자", "길드")
        self.assertEqual(app.current_season_no, "19")
        app._activate_current_server_profile_for_new_season.assert_called_once()

    def test_cancel_empty_name_and_save_error_preserve_season(self):
        for kwargs in (dict(cancel=True), dict(name=""), dict(save_error=True)):
            with self.subTest(kwargs=kwargs):
                app, result = self.run_dialog(**kwargs)
                self.assertFalse(result)
                self.assertEqual(app.current_season_no, "18")
                self.assertEqual(set(app.season_history_map), {"18"})
                app._save_settings.assert_not_called()
                app._activate_current_server_profile_for_new_season.assert_not_called()

    def test_profile_permission_error_preserves_season_and_connection(self):
        app, result = self.run_dialog(profile_error=True)
        self.assertFalse(result)
        self.assertEqual(app.current_season_no, '18')
        self.assertEqual(app.current_season_started_at, '2026-09-01 00:00:00')
        self.assertEqual(set(app.season_history_map), {'18'})
        app._save_administrator_identity.assert_not_called()
        app._record_season_history_transition.assert_not_called()
        app._save_season_history.assert_not_called()
        app._save_settings.assert_not_called()
        app._activate_current_server_profile_for_new_season.assert_not_called()
        app._show_centered_messagebox.assert_called_once()

    def test_declining_existing_profile_preserves_season_identity_and_connection(self):
        app, result = self.run_dialog(profile_cancel=True)
        self.assertFalse(result)
        self.assertEqual(app.current_season_no, '18')
        self.assertEqual(set(app.season_history_map), {'18'})
        app._save_administrator_identity.assert_not_called()
        app._save_settings.assert_not_called()
        app._save_season_history.assert_not_called()
        app._activate_current_server_profile_for_new_season.assert_not_called()


if __name__ == "__main__":
    unittest.main()
