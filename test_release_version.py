"""Version resolution with simulated Git; no builds or repository changes."""
import ast
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import patch

from release_version import resolve_release_version
import build_release


class ReleaseVersionTests(unittest.TestCase):
    def resolve(self, output="v5.5.5\n", error=None):
        with patch.object(Path, "exists", return_value=True), patch("release_version.subprocess.run",
                return_value=SimpleNamespace(stdout=output), side_effect=error) as run:
            version = resolve_release_version("v5.5.1", Path("checkout"))
        return version, run

    def test_current_program_tag_drives_build_and_archive_version(self):
        version, run = self.resolve()
        self.assertEqual(version, "v5.5.5")
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["git", "describe", "--tags", "--abbrev=0"])
        self.assertIn("--match", command)
        self.assertNotIn("--all", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 5)

    def test_supported_program_suffixes_are_preserved(self):
        for version in ["v5.5.2.fix", "v5.1.0.beta", "v1.0.0-rc.1"]:
            self.assertEqual(self.resolve(version)[0], version)

    def test_module_tags_and_non_version_tags_do_not_relabel_program(self):
        for tag in ["tts-module-v1.0.0", "notice-v1.0.0", "not-a-version", ""]:
            self.assertEqual(self.resolve(tag)[0], "v5.5.1")

    def test_without_git_checkout_falls_back_without_running_git(self):
        with patch.object(Path, "exists", return_value=False), patch("release_version.subprocess.run") as run:
            self.assertEqual(resolve_release_version("v5.5.1", Path("archive")), "v5.5.1")
            run.assert_not_called()

    def test_missing_git_tags_and_timeout_use_version_fallback(self):
        for error in [FileNotFoundError("git"), subprocess.CalledProcessError(128, "git"),
                      subprocess.TimeoutExpired("git", 5)]:
            self.assertEqual(self.resolve(error=error)[0], "v5.5.1")

    def test_release_driver_uses_same_resolver_as_gui_spec(self):
        with patch.object(build_release, "resolve_release_version", return_value="v5.5.5") as resolve:
            self.assertEqual(build_release.build_version(), "v5.5.5")
            resolve.assert_called_once_with("v5.5.1", build_release.ROOT)

    def test_spec_writes_resolved_version_into_embedded_metadata(self):
        source = (Path(__file__).parent / "boss_timer_gui.spec").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        assignments = [node for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "BUILD_VERSION" for target in node.targets)]
        write = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == "write_build_metadata")
        values = dict(Path=Path, project_root=Path("unused"), resolve_release_version=lambda fallback, root: "v5.5.5",
                      datetime=datetime, json=json, BUILD_LAST_UPDATED="fallback-date", read_git_text=lambda args: "")
        exec(compile(ast.Module(body=assignments+[write], type_ignores=[]), "boss_timer_gui.spec", "exec"), values)
        with patch.object(Path, "write_text") as saved:
            values["write_build_metadata"]()
        metadata = json.loads(saved.call_args.args[0])
        self.assertEqual(metadata["version"], "v5.5.5")
        self.assertEqual(metadata["build_detail_version"], "v5.5.5")

    def test_zip_name_uses_completed_build_metadata_without_building(self):
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "mkdir"), \
                patch.object(Path, "read_text", return_value=json.dumps(dict(version="v5.5.5"))), \
                patch.object(build_release, "run_pyinstaller") as build, \
                patch.object(build_release, "create_release_zip", return_value=Path("unused.zip")) as archive, \
                redirect_stdout(io.StringIO()):
            self.assertEqual(build_release.main(), 0)
        self.assertEqual(build.call_count, 2)
        archive.assert_called_once_with("v5.5.5")


if __name__ == "__main__":
    unittest.main()
