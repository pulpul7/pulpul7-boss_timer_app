"""Exercise the GUI's real uploader methods without importing or starting the GUI."""
import ast
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock


def source_methods():
    wanted = {'_compare_github_data_versions', '_get_github_data_content_hash',
              '_get_github_schedule_content_hash', '_build_github_server_index_payload',
              '_build_github_server_index_entry', '_find_github_server_entry',
              '_extract_github_server_entries', '_reconcile_github_handover_schedule_versions',
              '_upload_current_schedule_to_github_data'}
    tree = ast.parse(Path(__file__).with_name('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
    methods = [node for cls in tree.body if isinstance(cls, ast.ClassDef)
               for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    scope = dict(re=re, hashlib=hashlib, json=json, datetime=datetime)
    exec(compile(ast.Module(body=methods, type_ignores=[]), 'uploader-source', 'exec'), scope)
    return {name: scope[name] for name in wanted}


class VersionRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.methods = source_methods()

    def setUp(self):
        self.app = NS(current_season_no=18)
        for name, function in self.methods.items():
            setattr(self.app, name, MethodType(function, self.app))
        app = self.app
        app._serialize_schedule_state_value = deepcopy
        app._get_app_version_for_data = lambda: '1.0.0'
        app._append_debug_log = Mock()
        self.payload = dict(kind='schedule', dataVersion='2026.10.12.001',
            payload=dict(version=1, season_no='18', share_prefix='9', server_name='9',
                         schedule_events=[dict(boss_name='test', time='10:00')]))
        self.entry = dict(id='9', name='9', schedule='data/schedules/9.json',
            scheduleVersion='2026.10.12.001', scheduleHash=app._get_github_schedule_content_hash(self.payload),
            bosses='data/bosses/9.json', bossConfigVersion='2026.10.12.001', bossConfigHash='boss-hash')
        self.files = {'data/server_index.json': dict(servers=[deepcopy(self.entry)]),
                      'data/schedules/9.json': deepcopy(self.payload)}
        self.shas = {path: path+'-0' for path in self.files}
        self.writes = []
        def get(path, **_):
            return deepcopy(self.files.get(path)), self.shas.get(path), ''
        def put(path, data, **kw):
            if kw.get('sha') != self.shas.get(path):
                return False, '409'
            self.files[path] = deepcopy(data)
            self.writes.append((path, deepcopy(data), kw))
            self.shas[path] = path+'-'+str(len(self.writes))
            return True, ''
        app._github_get_json_file = Mock(side_effect=get)
        app._github_put_json_file = Mock(side_effect=put)
        app._get_current_github_upload_server_entry = lambda: self.entry
        app._get_github_cached_item_for_entry = lambda _: {}
        app._load_github_local_schedule_payload = lambda _: (dict(_githubScheduleVersion='2026.10.12.001',
            _githubBossConfigVersion='2026.10.12.001'), '')
        app._get_github_import_meta_versions_for_entry = lambda _: ('', '')
        app._set_github_cached_versions = Mock()

    def repair(self, guard=None):
        return self.app._reconcile_github_handover_schedule_versions(
            deepcopy(self.files['data/server_index.json']), self.shas['data/server_index.json'],
            deepcopy(self.entry), deepcopy(self.files['data/schedules/9.json']),
            self.shas['data/schedules/9.json'], deepcopy(self.payload), guard)

    def test_index_behind_repairs_only_index_with_cas(self):
        self.files['data/schedules/9.json']['dataVersion'] = '2026.10.12.002'
        before = deepcopy(self.files['data/schedules/9.json'])
        schedule, index, entry, sha = self.repair()
        self.assertEqual([item[0] for item in self.writes], ['data/server_index.json'])
        self.assertEqual(entry['scheduleVersion'], '2026.10.12.002')
        self.assertEqual(self.files['data/schedules/9.json'], before)
        self.assertEqual(sha, self.shas['data/server_index.json'])
        self.assertEqual(entry['bosses'], self.entry['bosses'])

    def test_file_behind_changes_metadata_only(self):
        self.entry['scheduleVersion'] = '2026.10.12.002'
        self.files['data/server_index.json']['servers'][0] = deepcopy(self.entry)
        before = deepcopy(self.payload['payload'])
        self.repair()
        self.assertEqual([item[0] for item in self.writes], ['data/schedules/9.json', 'data/server_index.json'])
        self.assertEqual(self.files['data/schedules/9.json']['payload'], before)
        self.assertEqual(self.files['data/schedules/9.json']['dataVersion'], '2026.10.12.002')

    def test_different_schedule_never_rewrites_metadata(self):
        self.files['data/schedules/9.json']['payload']['schedule_events'] = []
        with self.assertRaises(ValueError):
            self.repair()
        self.assertEqual(self.writes, [])

    def test_expired_request_never_writes(self):
        with self.assertRaises(ValueError):
            self.repair(lambda: False)
        self.assertEqual(self.writes, [])

    def test_request_changes_after_file_write_stops_index_write(self):
        self.entry['scheduleVersion'] = '2026.10.12.002'
        with self.assertRaises(ValueError):
            self.repair(Mock(side_effect=[True, False]))
        self.assertEqual([item[0] for item in self.writes], ['data/schedules/9.json'])

    def test_concurrent_index_write_is_rejected(self):
        self.app._github_put_json_file.side_effect = lambda *_, **__: (False, '409')
        with self.assertRaises(ValueError):
            self.repair()
        self.assertEqual(self.writes, [])

    def test_real_uploader_accepts_same_content_after_partial_commit(self):
        self.files['data/schedules/9.json']['dataVersion'] = '2026.10.12.002'
        ok, message, entry = self.app._upload_current_schedule_to_github_data(
            self.entry, handover_schedule_payload=deepcopy(self.payload), handover_guard=lambda: True)
        self.assertTrue(ok, message)
        self.assertEqual(entry['scheduleVersion'], '2026.10.12.002')
        self.assertEqual(entry['scheduleHash'], self.app._get_github_schedule_content_hash(self.payload))
        self.assertEqual([item[0] for item in self.writes], ['data/server_index.json'])

    def test_real_uploader_rejects_partial_commit_with_different_content(self):
        self.files['data/schedules/9.json']['dataVersion'] = '2026.10.12.002'
        self.files['data/schedules/9.json']['payload']['schedule_events'] = []
        ok, _, _ = self.app._upload_current_schedule_to_github_data(
            self.entry, handover_schedule_payload=deepcopy(self.payload), handover_guard=lambda: True)
        self.assertFalse(ok)
        self.assertEqual(self.writes, [])

    def test_same_schedule_is_not_uploaded_again(self):
        for _ in range(3):
            ok, message, entry = self.app._upload_current_schedule_to_github_data(
                self.entry, handover_schedule_payload=deepcopy(self.payload))
            self.assertTrue(ok, message)
            self.assertEqual(entry['schedule'], self.entry['schedule'])
        self.assertEqual(self.writes, [])

    def test_boss_settings_commit_uses_sha_after_schedule_repair(self):
        self.entry['scheduleVersion'] = '2026.10.12.002'
        self.files['data/server_index.json']['servers'][0] = deepcopy(self.entry)
        self.files['data/bosses/9.json'] = dict(dataVersion='2026.10.12.001', payload=dict(boss='old'))
        self.shas['data/bosses/9.json'] = 'boss-0'
        app = self.app
        app._load_github_local_schedule_payload = lambda _: (dict(_githubScheduleVersion='2026.10.12.002',
            _githubBossConfigVersion='2026.10.12.003'), '')
        app._build_github_boss_config_payload = lambda version: dict(dataVersion=version, payload=dict(boss='new'))
        app._get_github_boss_config_content_hash = lambda data: app._get_github_data_content_hash(data['payload'])
        for name in ('_update_github_import_meta', '_save_current_schedule_to_github_local_cache', '_save_schedule_state'):
            setattr(app, name, Mock())
        ok, message, entry = app._upload_current_schedule_to_github_data(
            self.entry, handover_schedule_payload=deepcopy(self.payload), handover_guard=lambda: True)
        self.assertTrue(ok, message)
        self.assertEqual(entry['bossConfigVersion'], '2026.10.12.003')
        self.assertEqual(self.writes[-1][2]['sha'], 'data/server_index.json-2')
        self.assertEqual(self.files['data/schedules/9.json']['payload'], self.payload['payload'])


if __name__ == '__main__':
    unittest.main()
