"""Read-only runtime version checks; never import GUI or execute a build spec."""
import ast
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, mock_open, patch


ROOT = Path(__file__).parent


def metadata_code():
    tree = ast.parse((ROOT / "boss_timer_gui.py").read_text(encoding="utf-8-sig"))
    wanted = {"_read_build_metadata_file", "load_build_metadata"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    return compile(ast.Module(body=nodes, type_ignores=[]), "boss_timer_gui.py", "exec")


def metadata_functions(code):
    values = dict(os=os, json=json, sys=SimpleNamespace(frozen=False),
        DEFAULT_AUTHOR_NAME="author", DEFAULT_APP_VERSION="v5.5.1", DEFAULT_LAST_UPDATED="fallback-date",
        DEFAULT_BUILD_DETAIL_VERSION="unknown", DEFAULT_BUILD_TIMESTAMP="", BUILD_METADATA_FILENAME="build_metadata.json",
        get_resource_root=Mock(return_value="resources"), get_app_root=Mock(return_value="app"),
        Path=Path, resolve_release_version=Mock(side_effect=lambda fallback, root: fallback),
        _run_git_text_command=Mock(return_value="unrelated-git-value"))
    exec(code, values)
    return values


class BuildMetadataDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Parse the large GUI once, without importing it or starting the app.
        previous_stack_size = threading.stack_size(16 * 1024 * 1024)
        values = []
        try:
            worker = threading.Thread(target=lambda: values.append(metadata_code()))
            worker.start(); worker.join()
        finally:
            threading.stack_size(previous_stack_size)
        cls.code = values[0]

    def setUp(self):
        self.values = metadata_functions(self.code)
        self.packaged = dict(version="v5.5.5", author="author", last_updated="2026-10-03",
                             build_detail_version="v5.5.5", build_timestamp="2026-10-03 16:06:38")
        self.reader = self.values["_read_build_metadata_file"]
        self.values["_read_build_metadata_file"] = Mock(return_value=self.packaged)

    def load(self):
        return self.values["load_build_metadata"]()

    def test_deployment_keeps_embedded_version_and_timestamp_even_with_git(self):
        self.values["sys"].frozen = True
        self.assertEqual(self.load()["version"], "v5.5.5")
        self.assertEqual(self.load()["build_timestamp"], "2026-10-03 16:06:38")
        self.values["_run_git_text_command"].assert_not_called()
        self.values["resolve_release_version"].assert_not_called()

    def test_source_keeps_recorded_build_instead_of_newest_module_tag(self):
        self.assertEqual(self.load(), self.packaged)
        self.values["_run_git_text_command"].assert_not_called()

    def test_source_release_uses_reachable_program_tag_while_deployment_keeps_its_build(self):
        self.packaged["version"] = "v5.5.1"
        self.values["resolve_release_version"].side_effect = None
        self.values["resolve_release_version"].return_value = "v5.5.5"
        self.values["_run_git_text_command"].return_value = "v5.5.5-dirty"
        source = self.load()
        self.assertEqual(source["version"], "v5.5.5")
        self.assertEqual(source["build_detail_version"], "v5.5.5-dirty")
        self.values["sys"].frozen = True
        self.assertEqual(self.load()["version"], "v5.5.1")

    def test_missing_deployment_metadata_uses_defaults_without_git(self):
        self.values["sys"].frozen = True
        self.values["_read_build_metadata_file"].return_value = {}
        result = self.load()
        self.assertEqual(result["version"], "v5.5.1")
        self.assertEqual(result["build_timestamp"], "")
        self.values["_run_git_text_command"].assert_not_called()

    def test_partial_source_metadata_only_fills_missing_diagnostics(self):
        self.values["_read_build_metadata_file"].return_value = dict(version="v5.5.5", last_updated="saved-date")
        result = self.load()
        self.assertEqual(result["version"], "v5.5.5")
        self.assertEqual(result["last_updated"], "saved-date")
        commands = [call.args[0] for call in self.values["_run_git_text_command"].call_args_list]
        self.assertEqual(len(commands), 2)
        self.assertFalse(any(command[0] == "tag" for command in commands))

    def test_embedded_metadata_wins_over_sidecar(self):
        with patch("builtins.open", mock_open(
                read_data=json.dumps(self.packaged))) as opened, patch.object(os.path, "exists", return_value=True):
            self.assertEqual(self.reader()["version"], "v5.5.5")
            self.assertEqual(opened.call_args.args[0], os.path.join("resources", "build_metadata.json"))
            self.assertEqual(opened.call_count, 1)
            self.assertEqual(opened.call_args.kwargs["encoding"], "utf-8-sig")

    def test_bad_encoding_in_first_location_falls_back_to_next_location(self):
        with patch("builtins.open", side_effect=[UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
                mock_open(read_data=json.dumps(self.packaged))()]), \
                patch.object(os.path, "exists", return_value=True):
            self.assertEqual(self.reader()["version"], "v5.5.5")


if __name__ == "__main__":
    unittest.main()
