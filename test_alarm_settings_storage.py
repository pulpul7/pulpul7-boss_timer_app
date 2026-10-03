import ast
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from alarm_settings_storage import load_defaults, reserve_save, write_settings
from repair_current_chimes import apply_repair


class AlarmStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))

    def method(self, name, namespace):
        node = next(n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        exec(compile(ast.Module([node], []), 'alarm-method', 'exec'), namespace)
        return namespace[name]

    def test_no_profile_file_uses_distribution_defaults_not_hardcoded_empty_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'init').mkdir()
            (root/'init/default_schedule_alarm_settings.json').write_text(json.dumps(dict(
                fixed_boss_overrides={'world': {'enabled': True, 'offsets': [60]}},
                chime_settings={'general': 'wave/custom.wav'}, fixed_boss_enabled=True)))
            namespace = dict(os=os, json=json, get_resource_root=lambda: str(root),
                get_record_book_boss_catalog=lambda: [], SCHEDULE_ALARM_VOICE_RULE_VERSION='test',
                SCHEDULE_ALARM_DEFAULT_CHIME_PATHS={'general': 'wave/default.wav'})
            default = self.method('_default_schedule_alarm_settings_payload', namespace)
            load = self.method('_load_schedule_alarm_settings', namespace)
            app = NS(_default_schedule_alarm_settings_payload=lambda: default(None),
                _get_schedule_alarm_settings_storage_path=lambda: str(root/'absent.json'))
            value = load(app)
            self.assertEqual(value['chime_settings']['general'], 'wave/custom.wav')
            self.assertTrue(value['fixed_boss_overrides']['world']['enabled'])

    def test_regular_gui_save_never_overwrites_default_seed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'profile.json'
            app = NS(_get_schedule_alarm_settings_storage_path=lambda: str(path),
                _save_default_schedule_alarm_settings_seed=Mock(), _append_debug_log=Mock())
            write = self.method('_write_schedule_alarm_settings_payload', {})
            self.assertTrue(write(app, {'chime_settings': {'general': ''}}))
            app._save_default_schedule_alarm_settings_seed.assert_not_called()
            self.assertEqual(json.loads(path.read_text())['chime_settings']['general'], '')

    def test_blank_schedule_defaults_include_chimes_and_fixed_overrides(self):
        defaults = load_defaults(Path('init'), dict(chime_settings={}, fixed_boss_overrides={}))
        self.assertTrue(all(defaults['chime_settings'].get(key) for key in ('general', 'fixed', 'rapid_chain')))
        self.assertTrue(defaults['fixed_boss_overrides'])
        self.assertTrue(Path('init/schedule_fixed_bosses.txt').read_text(encoding='utf-8').strip())

    def test_late_save_cannot_overwrite_newer_setting_or_other_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = Path(folder)/'a.json', Path(folder)/'b.json'
            old = reserve_save(a)
            latest = reserve_save(a)
            self.assertTrue(write_settings(a, {'chime_settings': {'general': 'chosen.wav'}}, latest))
            self.assertFalse(write_settings(a, {'chime_settings': {'general': ''}}, old))
            self.assertFalse(write_settings(b, {}, latest))
            self.assertFalse(b.exists())
            self.assertEqual(json.loads(a.read_text())['chime_settings']['general'], 'chosen.wav')

    def test_async_gui_save_captures_profile_before_worker_runs(self):
        method = next(n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef) and n.name == '_save_schedule_alarm_settings_async')
        workers = []
        thread = lambda **kw: NS(start=lambda: workers.append(kw['target']))
        namespace = dict(threading=NS(Thread=thread))
        exec(compile(ast.Module([method], []), 'async-save', 'exec'), namespace)
        with tempfile.TemporaryDirectory() as folder:
            current = [str(Path(folder)/'A.json')]
            app = NS(_build_schedule_alarm_settings_payload=lambda: {'chime_settings': {'general': 'A.wav'}},
                _get_schedule_alarm_settings_storage_path=lambda: current[0],
                _write_schedule_alarm_settings_payload=Mock())
            namespace[method.name](app)
            current[0] = str(Path(folder)/'B.json')
            workers[0]()
            self.assertEqual(app._write_schedule_alarm_settings_payload.call_args.kwargs['settings_path'], str(Path(folder)/'A.json'))

    def test_repair_backs_up_only_empty_chimes_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data, resource = root/'data', root/'resource'
            data.mkdir(); (resource/'init').mkdir(parents=True); (resource/'wave').mkdir()
            sounds = {key: 'wave/test.wav' for key in ('general', 'fixed', 'rapid_chain')}
            (resource/'wave/test.wav').write_bytes(b'fixture')
            (resource/'init/default_schedule_alarm_settings.json').write_text(json.dumps({'chime_settings': sounds}))
            (data/'active_server_profile.json').write_text(json.dumps(dict(season_key='season_26', server_id='9')))
            profile = data/'server_profiles/season_26/9'
            profile.mkdir(parents=True)
            alarm = profile/'schedule_alarm_settings.json'
            original = json.dumps(dict(chime_settings=dict.fromkeys(sounds, ''), master_enabled=False, fixed_boss_overrides={'custom': 1})).encode()
            alarm.write_bytes(original)
            schedule = profile/'schedule_state.json'
            schedule.write_text('unchanged')
            backup, paths = apply_repair(data, resource)
            self.assertEqual(len(paths), 1)
            self.assertEqual((backup/alarm.relative_to(data)).read_bytes(), original)
            value = json.loads(alarm.read_bytes())
            self.assertEqual(value['chime_settings'], sounds)
            self.assertFalse(value['master_enabled'])
            self.assertEqual(value['fixed_boss_overrides'], {'custom': 1})
            self.assertEqual(schedule.read_text(), 'unchanged')
            self.assertEqual(apply_repair(data, resource), (None, []))


if __name__ == '__main__':
    unittest.main()
