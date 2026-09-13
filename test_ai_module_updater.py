"""No live GitHub access, application startup, or distributable builds."""
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from ai_module_updater import (
    AiModuleUpdater, KST, MODULE_ID, REPOSITORY, UpdateError,
    inspect_package, release_rows, version_tuple,
)
from ai_update_center import AiUpdateCenter, display_date


def package(version="1.0.0", *, manifest_changes=None, extra=None):
    files = {"payload/main.py": b"def start(host):\n    return None\n"}
    files.update(extra or {})
    manifest = {"module_id": MODULE_ID, "api_version": 1, "version": version,
                "min_app_version": "5.0.0", "entrypoint": "payload/main.py",
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    manifest.update(manifest_changes or {})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("module.json", json.dumps(manifest))
        for name, data in files.items():
            archive.writestr(name, data)
    data = stream.getvalue()
    release = {"name": f"AI {version}", "tag_name": f"ai-v{version}", "published_at": "2026-09-13T00:00:00Z",
               "body": "기능 추가\n- 공지 관리 개선", "assets": [{
                   "name": f"Update_AI_v{version}.zip", "size": len(data),
                   "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                   "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/ai-v{version}/Update_AI_v{version}.zip"}]}
    return data, release


class PackageTests(unittest.TestCase):
    def test_numeric_versions(self):
        self.assertGreater(version_tuple("1.10.0"), version_tuple("v1.9.9"))
        for invalid in ("1.0", "1.0.0-beta", "../1.0.0", "01.0.0"):
            with self.assertRaises(UpdateError):
                version_tuple(invalid)

    def test_stable_release_filter(self):
        _, valid = package()
        preview = dict(valid, prerelease=True)
        draft = dict(valid, draft=True)
        unrelated = {"assets": [{"name": "BossTimer-v5.3.2.zip"}]}
        self.assertEqual(len(release_rows([preview, valid, draft, unrelated])), 1)

    def test_duplicate_version_refused(self):
        _, release = package()
        with self.assertRaises(UpdateError):
            release_rows([release, release])

    def test_missing_digest_or_foreign_url_blocked(self):
        for field, value in (("digest", None), ("browser_download_url", "https://example.com/evil.zip")):
            _, release = package()
            release["assets"][0][field] = value
            self.assertTrue(release_rows([release])[0]["blocked"])

    def test_valid_package(self):
        data, release = package()
        manifest, files = inspect_package(data, release_rows([release])[0], "v5.0.0")
        self.assertEqual(manifest["version"], "1.0.0")
        self.assertIn("payload/main.py", files)

    def test_corrupt_download(self):
        data, release = package()
        with self.assertRaises(UpdateError):
            inspect_package(data + b"broken", release_rows([release])[0], "5.0.0")

    def test_manifest_defence(self):
        for changes in ({"version": "0.9.0"}, {"module_id": "another_module"},
                        {"api_version": 2}, {"min_app_version": "99.0.0"},
                        {"max_app_version": "4.0.0"}, {"python_version": "2.7"},
                        {"files": {}}, {"entrypoint": "payload/missing.py"}):
            with self.subTest(changes=changes):
                data, release = package(manifest_changes=changes)
                with self.assertRaises(UpdateError):
                    inspect_package(data, release_rows([release])[0], "5.0.0")

    def test_escape_and_unrelated_files(self):
        for name in ("../settings.ini", "payload/../../evil.py", "/payload/a.py", "payload/a:evil.py",
                     "payload/CON.py", "payload/dir./evil.py", "boss_timer_settings.ini",
                     "payload/evil.exe", "payload\\evil.py"):
            with self.subTest(name=name):
                data, release = package(extra={name: b""})
                with self.assertRaises(UpdateError):
                    inspect_package(data, release_rows([release])[0], "5.0.0")

    def test_case_insensitive_duplicate(self):
        data, release = package(extra={"payload/MAIN.py": b""})
        with self.assertRaises(UpdateError):
            inspect_package(data, release_rows([release])[0], "5.0.0")

    def test_symlink_refused(self):
        data, release = package()
        stream = io.BytesIO(data)
        with zipfile.ZipFile(stream, "a") as archive:
            entry = zipfile.ZipInfo("payload/link.py")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, "../../outside.py")
        data = stream.getvalue()
        row = release_rows([release])[0]
        row.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        with self.assertRaises(UpdateError):
            inspect_package(data, row, "5.0.0")


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boss-timer-updater-unit-")
        self.addCleanup(self.temp.cleanup)
        self.data, self.release = package()
        self.fetch = Mock(side_effect=lambda url, _: self.data if url.endswith(".zip") else json.dumps([self.release]).encode())
        self.updater = AiModuleUpdater(Path(self.temp.name) / "updater", "5.0.0", self.fetch)

    def test_install_is_pending_until_restart(self):
        self.updater.install("1.0.0")
        state = self.updater.snapshot()
        self.assertEqual(state["pending"], "1.0.0")
        self.assertFalse(state["active"])
        self.assertIsNone(self.updater.active_module_path())
        self.updater.activate_pending()
        self.assertTrue((self.updater.active_module_path() / "payload/main.py").is_file())
        self.assertEqual(self.updater.snapshot()["active"], "1.0.0")

    def test_same_and_lower_version_refused_before_download(self):
        self.updater.install("1.0.0")
        for version in ("1.0.0", "0.9.0"):
            self.data, self.release = package(version)
            self.fetch.reset_mock()
            with self.assertRaises(UpdateError):
                self.updater.install(version)
            self.assertFalse(any(call.args[0].endswith(".zip") for call in self.fetch.call_args_list))

    def test_recheck_after_download(self):
        original_fetch = self.updater.fetch
        def fetch(url, limit):
            if url.endswith(".zip"):
                with self.updater._locked():
                    state = self.updater.snapshot()
                    state["pending"] = "2.0.0"
                    self.updater._save(state)
            return original_fetch(url, limit)
        self.updater.fetch = fetch
        with self.assertRaisesRegex(UpdateError, "다운로드 중"):
            self.updater.install("1.0.0")
        self.assertEqual(self.updater.snapshot()["pending"], "2.0.0")

    def test_failed_activation_keeps_old_version_and_all_user_files(self):
        user_file = Path(self.temp.name) / "schedule_state.json"
        user_file.write_text("unchanged", encoding="utf-8")
        self.updater.install("1.0.0")
        self.updater.activate_pending()
        self.data, self.release = package("1.1.0")
        self.updater.install("1.1.0")
        state = self.updater.snapshot()
        folder = self.updater.root / "versions" / state["packages"]["1.1.0"]["folder"]
        (folder / "payload/main.py").write_text("damaged", encoding="utf-8")
        self.updater.activate_pending()
        self.assertEqual(self.updater.snapshot()["active"], "1.0.0")
        self.assertEqual(self.updater.snapshot()["history"][-1]["status"], "적용 실패")
        self.assertEqual(user_file.read_text(encoding="utf-8"), "unchanged")

    def test_old_version_backup_preserved(self):
        self.updater.install("1.0.0")
        self.updater.activate_pending()
        first = self.updater.active_module_path()
        self.data, self.release = package("1.1.0")
        self.updater.install("1.1.0")
        self.updater.activate_pending()
        self.assertEqual(self.updater.snapshot()["previous"], "1.0.0")
        self.assertTrue(first.exists())

    def test_daily_first_start_even_in_afternoon(self):
        self.assertFalse(self.updater.claim_daily_check(datetime(2026, 9, 13, 5, 59, tzinfo=KST)))
        self.assertTrue(self.updater.claim_daily_check(datetime(2026, 9, 13, 18, 0, tzinfo=KST)))
        self.assertFalse(self.updater.claim_daily_check(datetime(2026, 9, 13, 20, 0, tzinfo=KST)))
        self.assertTrue(self.updater.claim_daily_check(datetime(2026, 9, 14, 6, 0, tzinfo=KST)))

    def test_disable_and_config_validation(self):
        self.updater.configure(False, "06:50")
        self.assertFalse(self.updater.claim_daily_check(datetime(2026, 9, 13, 18, 0, tzinfo=KST)))
        with self.assertRaises(UpdateError):
            self.updater.configure(True, "24:00")

    def test_network_failure_retains_catalog(self):
        expected = self.updater.check()
        self.updater.fetch = Mock(side_effect=OSError("offline"))
        with self.assertRaises(OSError):
            self.updater.check()
        self.assertEqual(self.updater.snapshot()["catalog"], expected)

    def test_no_release_is_not_an_install(self):
        self.updater.fetch = Mock(return_value=b"[]")
        self.assertEqual(self.updater.check(), [])
        with self.assertRaises(UpdateError):
            self.updater.install("1.0.0")

    def test_corrupt_state_not_overwritten(self):
        self.updater.configure(True, "06:00")
        path = self.updater.root / "state.json"
        path.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.updater.claim_daily_check()
        self.assertEqual(path.read_text(encoding="utf-8"), "broken")

    @unittest.skipUnless(sys.platform == "win32", "Windows process locking")
    def test_other_updater_lock_refused_and_released(self):
        other = AiModuleUpdater(self.updater.root, "5.0.0", self.fetch)
        with self.updater._locked():
            with self.assertRaises(UpdateError):
                other.configure(True, "06:00")
        other.configure(True, "06:00")

    def test_installed_version_order_is_rechecked_at_activation(self):
        self.updater.install("1.0.0")
        with self.updater._locked():
            state = self.updater.snapshot()
            state["active"] = "2.0.0"
            self.updater._save(state)
        self.updater.activate_pending()
        state = self.updater.snapshot()
        self.assertEqual(state["active"], "2.0.0")
        self.assertEqual(state["pending"], "")
        self.assertEqual(state["history"][-1]["status"], "적용 실패")

    def test_ui_manual_check_does_not_start_countdown(self):
        ui = object.__new__(AiUpdateCenter)
        ui._status = Mock()
        ui._render = Mock()
        ui._offer = Mock()
        ui.updater = self.updater
        rows = release_rows([self.release])
        ui._checked(rows)
        ui._offer.assert_not_called()
        ui._checked(rows, automatic=True)
        ui._offer.assert_called_once_with(rows[0])

    def test_dates_are_korean_time(self):
        self.assertEqual(display_date("2026-09-12T20:00:00Z"), "2026-09-13 05:00")

    def test_hidden_tk_center_can_render_catalog_and_history(self):
        import tkinter as tk
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        self.addCleanup(root.destroy)
        ui = object.__new__(AiUpdateCenter)
        ui.app = Mock(root=root, schedule_window=None)
        ui.root = root
        ui.window = None
        ui.prompt = None
        ui.rows = []
        ui.status_text = "test"
        ui.busy = True  # No workers or real network.
        ui.updater = self.updater
        self.updater.check()
        self.updater.record("설치 실패", "1.0.0", "테스트 오류")
        original = tk.Toplevel
        def hidden(*args, **kwargs):
            window = original(*args, **kwargs)
            window.withdraw()
            return window
        with patch("ai_update_center.tk.Toplevel", side_effect=hidden):
            ui.open()
            root.update_idletasks()
            self.assertEqual(len(ui.tree.get_children()), 1)
            self.assertIn("설치 실패", ui.history_text.get("1.0", "end"))
            self.assertIn("공지 관리 개선", ui.detail.get("1.0", "end"))
            ui._close()


if __name__ == "__main__":
    unittest.main()
