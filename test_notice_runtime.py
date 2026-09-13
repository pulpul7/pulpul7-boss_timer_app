import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from ai_module_updater import UpdateError
from build_ai_update import package_bytes
from notice_runtime import NoticeHost, NoticeRuntime
from test_ai_module_updater import package


PLUGIN = b'''from .helper import VALUE
MODULE_API = 1
class Plugin:
    def __init__(self, host): self.host = host
    def start(self): self.host.log("start:" + VALUE)
    def stop(self): self.host.log("stop:" + VALUE)
    def health_check(self): return {"ok": True, "api_version": 1}
    def open_management(self): return VALUE
def create_plugin(host): return Plugin(host)
'''


class NoticeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boss-timer-module-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.host = NoticeHost(root=Mock(), data_root=self.root / "notice_data", get_server=Mock(return_value=("odin9", "오9")),
            get_parent=Mock(), message_box=Mock(), log=Mock(), call_later=Mock(return_value="timer"), cancel_later=Mock())
        self.runtime = NoticeRuntime(self.host, self.root, Path(__file__).resolve().parent, "5.0.0")
        self.addCleanup(self.runtime.close)

    def install(self, version="1.1.0", code=PLUGIN):
        data, release = package(version, extra={"payload/main.py": code, "payload/helper.py": f"VALUE = '{version}'\n".encode(),
                                               "payload/__init__.py": b""})
        self.runtime.updater.fetch = Mock(side_effect=lambda url, _: data if url.endswith(".zip") else json.dumps([release]).encode())
        self.runtime.updater.install(version)

    def restart(self):
        self.runtime.close()
        self.runtime.start()

    def test_bundled_module_is_loaded_dynamically_without_data_writes(self):
        before = list(sys.path)
        self.runtime.start()
        self.assertEqual(self.runtime.session.version, "1.0.0")
        self.assertTrue(type(self.runtime.session.plugin).__module__.startswith("_boss_notice_"))
        self.assertEqual(sys.path, before)
        self.assertFalse(self.host.data_root.exists())
        self.assertEqual(self.runtime.updater.snapshot()["runtime_loaded"]["source"], "bundled")

    def test_module_can_be_packaged_in_memory_without_runtime_files(self):
        data, manifest = package_bytes(Path(__file__).resolve().parent / "notice_module")
        self.assertTrue(data.startswith(b"PK"))
        self.assertIn("payload/notice_management_ui.py", manifest["files"])
        self.assertIn("payload/__init__.py", manifest["files"])
        self.assertFalse(any("notice_data" in key or "__pycache__" in key for key in manifest["files"]))

    def test_same_as_bundled_version_cannot_install(self):
        self.runtime.start()
        with self.assertRaises(UpdateError):
            self.install("1.0.0")

    def test_installed_module_starts_only_next_session(self):
        self.runtime.start()
        self.install()
        self.runtime.start()
        self.assertEqual(self.runtime.session.version, "1.0.0")
        self.restart()
        self.assertEqual(self.runtime.session.plugin.open_management(), "1.1.0")
        self.assertEqual(self.runtime.updater.snapshot()["active"], "1.1.0")

    def test_relative_imports_create_no_bytecode_and_unmount_cleanly(self):
        self.runtime.start()
        self.install()
        self.restart()
        finder = self.runtime.session.finder
        self.assertFalse(list(finder.directory.rglob("*.pyc")))
        self.runtime.updater.active_module_path()  # Still passes exact file-list check after import.
        self.runtime.close()
        self.assertNotIn(finder, sys.meta_path)
        self.assertFalse(any(key.startswith(finder.prefix) for key in sys.modules))

    def test_failed_start_falls_back_to_last_working_version(self):
        self.runtime.start()
        self.install()
        self.restart()
        bad = PLUGIN.replace(b'"ok": True', b'"ok": False')
        self.install("1.2.0", bad)
        self.restart()
        self.assertEqual(self.runtime.session.version, "1.1.0")
        state = self.runtime.updater.snapshot()
        self.assertEqual(state["pending"], "")
        self.assertIn("1.2.0", state["failed_versions"])
        self.host.log.assert_any_call("stop:1.2.0")

    def test_import_failure_and_system_exit_fall_back_to_builtin(self):
        self.runtime.start()
        self.install(code=b"raise SystemExit('bad plugin')\n")
        self.restart()
        self.assertEqual(self.runtime.session.version, "1.0.0")
        self.assertIn("1.1.0", self.runtime.updater.snapshot()["failed_versions"])

    def test_corrupt_active_version_falls_back_to_previous(self):
        self.runtime.start()
        self.install("1.1.0")
        self.restart()
        self.install("1.2.0")
        self.restart()
        path = self.runtime.updater.active_module_path()
        (path / "payload/helper.py").write_bytes(b"damaged")
        self.restart()
        self.assertEqual(self.runtime.session.version, "1.1.0")

    def test_stale_start_marker_blocks_crash_loop(self):
        self.runtime.start()
        self.install()
        with self.runtime.updater._locked():
            state = self.runtime.updater.snapshot()
            state["runtime_trial"] = {"version": "1.1.0", "source": "installed"}
            self.runtime.updater._save(state)
        self.restart()
        self.assertEqual(self.runtime.session.version, "1.0.0")
        self.assertIn("1.1.0", self.runtime.updater.snapshot()["failed_versions"])

    def test_manual_reinstall_allows_retry_after_failed_start(self):
        self.runtime.start()
        self.install(code=PLUGIN.replace(b'"ok": True', b'"ok": False'))
        self.restart()
        self.install(code=PLUGIN)
        self.assertNotIn("1.1.0", self.runtime.updater.snapshot()["failed_versions"])
        self.restart()
        self.assertEqual(self.runtime.session.version, "1.1.0")

    def test_unlisted_relative_import_is_not_allowed(self):
        self.runtime.start()
        self.install(code=PLUGIN + b"from .missing import secret\n")
        self.restart()
        self.assertEqual(self.runtime.session.version, "1.0.0")

    def test_update_preserves_existing_notice_data(self):
        self.host.data_root.mkdir()
        sentinel = self.host.data_root / "test.json"
        original = b'{"user_setting":"do not overwrite"}'
        sentinel.write_bytes(original)
        self.runtime.start()
        self.install()
        self.restart()
        self.assertEqual(sentinel.read_bytes(), original)

    def test_automatic_offer_skips_failed_version(self):
        from ai_update_center import AiUpdateCenter
        from ai_module_updater import release_rows
        self.runtime.start()
        self.install(code=PLUGIN.replace(b'"ok": True', b'"ok": False'))
        self.restart()
        ui = object.__new__(AiUpdateCenter)
        ui.updater = self.runtime.updater
        ui._status, ui._render, ui._offer = Mock(), Mock(), Mock()
        _, release = package("1.1.0")
        ui._checked(release_rows([release]), automatic=True)
        ui._offer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
