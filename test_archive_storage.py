import ast
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from archive_storage import season_directory, relocate_legacy_log_path


class ArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        names = {'run_archive_management_cleanup', '_get_archive_entry_season_no_from_label',
                 '_open_selected_archive_record_logs'}
        nodes = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
        cls.methods = dict(os=os, re=re, shutil=shutil, datetime=datetime)
        exec(compile(ast.Module(nodes, []), 'archive-methods', 'exec'), cls.methods)

    def test_legacy_path_redirect_but_custom_path_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            old = base / 'app' / 'archive_logs' / '18차 시즌'
            self.assertEqual(Path(relocate_legacy_log_path(str(old), base/'app', base/'data')),
                             base/'data'/'archive_logs'/'18차 시즌')
            custom = str(base/'custom')
            self.assertEqual(relocate_legacy_log_path(custom, base/'app', base/'data'), custom)

    def test_reject_root_traversal_and_absolute_target(self):
        with tempfile.TemporaryDirectory() as folder:
            for label in ('', '.', '..', '../outside', str(Path(folder).resolve())):
                with self.subTest(label=label), self.assertRaises(ValueError):
                    season_directory(folder, label)

    def test_selected_season_routes_to_record_editor_under_actual_data_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'AppData'/'BossTimer'/'archive_logs'
            chosen = root/'26차 시즌_오9_길드'
            chosen.mkdir(parents=True)
            entry = dict(season_label=chosen.name, folder_name=chosen.name)
            app = NS(_get_log_archive_dir=lambda: str(root),
                     _collect_archive_management_entries=lambda: [entry],
                     _get_log_archive_selected_entry=lambda entries: entries[0],
                     switch_record_subview=Mock(), log_status_var=NS(set=Mock()),
                     log_archive_status_var=NS(set=Mock()))
            self.methods['_open_selected_archive_record_logs'](app)
            app.switch_record_subview.assert_called_once_with('history', folder_path=str(chosen.resolve()))
            entry['folder_name'] = '../outside'
            app.switch_record_subview.reset_mock()
            self.methods['_open_selected_archive_record_logs'](app)
            app.switch_record_subview.assert_not_called()

    def test_contextual_season_cleanup_uses_data_root_and_preserves_current(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'archive_logs'
            root.mkdir()
            names = ['1차 시즌', '2차 시즌_오9_길드', '9차 시즌_오9_길드', 'custom']
            for name in names:
                (root/name).mkdir()
            unrelated = Path(folder)/'server_profiles'
            unrelated.mkdir()
            app = NS(current_season_no='9', season_history_map={}, log_panel=None,
                _has_active_season=lambda: True, _get_archive_keep_seasons_value=lambda: 3,
                _get_log_archive_dir=lambda: str(root), _show_centered_yesno=Mock(return_value=True),
                log_archive_status_var=NS(set=Mock()), _refresh_archive_management_view=Mock())
            app._get_archive_entry_season_no_from_label = lambda name: self.methods['_get_archive_entry_season_no_from_label'](app, name)
            self.methods['run_archive_management_cleanup'](app)
            self.assertFalse((root/names[0]).exists())
            self.assertFalse((root/names[1]).exists())
            self.assertTrue((root/names[2]).exists())
            self.assertTrue((root/'custom').exists())
            self.assertTrue(unrelated.exists())


if __name__ == '__main__':
    unittest.main()
