"""Read-only source/spec checks: never execute a build spec or import the GUI."""
import ast
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parent


def tree(name):
    return ast.parse((ROOT / name).read_text(encoding='utf-8-sig'))


def function(name, function_name, namespace):
    node = next(n for n in ast.walk(tree(name)) if isinstance(n, ast.FunctionDef) and n.name == function_name)
    exec(compile(ast.Module(body=[node], type_ignores=[]), name, 'exec'), namespace)
    return namespace[function_name]


class ReleasePreflightTests(unittest.TestCase):
    def test_version_matches_metadata_specs_fallback_and_seed_markers(self):
        version = 'v5.5.0'
        from build_release import build_version
        self.assertEqual(build_version(), version)
        for filename, constant in [('boss_timer_gui.py', 'DEFAULT_APP_VERSION'), ('boss_timer_gui.spec', 'BUILD_VERSION')]:
            assignment = next(n for n in tree(filename).body if isinstance(n, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == constant for t in n.targets))
            self.assertEqual(ast.literal_eval(assignment.value), version)
        metadata = json.loads((ROOT / 'build_metadata.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['version'], version)
        self.assertEqual(metadata['build_detail_version'], version)
        for folder in ('init', 'assets'):
            self.assertEqual((ROOT / folder / '.seed_version').read_text().strip(), version)

    def test_frozen_debug_default_off_with_explicit_opt_in(self):
        enabled = function('boss_timer_gui.py', '_is_debug_log_enabled', dict(os=os, sys=sys))
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(sys, 'frozen', True, create=True):
                self.assertFalse(enabled(None))
                with patch.dict(os.environ, BOSS_TIMER_DEBUG_LOG='1'):
                    self.assertTrue(enabled(None))
            with patch.object(sys, 'frozen', False, create=True):
                self.assertTrue(enabled(None))
                with patch.dict(os.environ, BOSS_TIMER_DEBUG_LOG='0'):
                    self.assertFalse(enabled(None))

    def test_packaging_includes_notice_defaults_and_excludes_logs(self):
        collect = function('boss_timer_gui.spec', 'collect_tree', dict(Path=Path))
        module = collect(ROOT / 'notice_module', 'notice_module')
        init = collect(ROOT / 'init', 'init')
        self.assertIn('notice_defaults.json', [Path(source).name for source, _ in module])
        self.assertIn('default_notice_settings.json', [Path(source).name for source, _ in init])
        for source, _ in module + init:
            self.assertNotIn(Path(source).suffix.lower(), {'.log', '.jsonl', '.tmp', '.pyc'})


if __name__ == '__main__':
    unittest.main()
