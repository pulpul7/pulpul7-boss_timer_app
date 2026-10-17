"""Deployment declaration regression checks on synthetic temp files. No builds."""
import ast
import configparser
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from distribution_defaults import (DECLARATION, STATE, FILES, apply_declared_defaults,
                                   declare, json_bytes, read_declaration)
from runtime_storage import atomic_write


class DistributionDefaultsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='distribution-defaults-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.resource, self.data = self.root / 'resource', self.root / 'data'
        self.profile = self.data / 'server_profiles/season_18/9'
        self.baseline = self.profile / 'settings_rollback/baseline'
        for group in ('boss', 'fixed'):
            for name in FILES[group]:
                self.put(self.resource / name, '배포|약칭|시간|지역|1|주기|색|1\n')
                self.put(self.data / name, 'old-global')
                self.put(self.profile / name, 'old-profile')
                self.put(self.baseline / name, 'old-baseline')
        self.put(self.resource / 'init/default_settings.ini',
                 '[settings]\nschedule_color_data_enabled=true\nfixed_boss_color_data_enabled=true\nprivate=must-not-copy\n')
        self.put(self.resource / 'web_assets/boss_spectrum.json', json.dumps(dict(schema=2, numbers={'a'*24:29})))
        self.put(self.data / 'web_service/boss_spectrum.json', json.dumps(dict(schema=2, numbers={'b'*24:7})))
        self.put(self.data / 'boss_timer_settings.ini', '[settings]\nschedule_color_data_enabled=false\nfixed_boss_color_data_enabled=false\ndiscord_token=KEEP\ncurrent_season_no=18\nvolume=17\n')
        self.put(self.baseline / 'boss_timer_settings.ini', '[settings]\nschedule_alarm_enabled=false\n')
        self.put(self.baseline / 'manifest.json', json.dumps(dict(groups={'alarm':[{'keep':True}]})))
        self.put(self.profile / 'schedule_state.json', 'real schedule')
        self.put(self.profile / 'discord_bot.ini', 'real bot token')
        self.put(self.data / 'web_service/access.json', 'real member and guest passwords')
        self.put(self.data / 'voice/user.wav', b'real user voice')

    @staticmethod
    def put(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode('utf-8'))

    def arm(self, groups=('boss','fixed','spectrum'), sequence=None):
        declaration = declare(self.resource, groups)
        if sequence is not None:
            declaration['sequence'] = sequence
        self.put(self.resource / DECLARATION, json_bytes(declaration))
        return declaration

    def apply(self, **kw):
        return apply_declared_defaults(self.resource, self.data, season='season_18', server='9', **kw)

    def snapshot(self):
        return {p.relative_to(self.data).as_posix():p.read_bytes() for p in self.data.rglob('*') if p.is_file()
                and 'distribution_defaults/backups' not in p.as_posix()}

    def test_no_declaration_or_disabled_keeps_every_existing_file(self):
        before = self.snapshot()
        self.assertFalse(self.apply()['applied'])
        self.put(self.resource / DECLARATION, json_bytes(dict(schema=1, enabled=False)))
        self.assertFalse(self.apply()['applied'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.data / STATE).exists())

    def test_explicit_override_preserves_private_data_and_updates_selected_rollback(self):
        self.arm();before=self.snapshot();result=self.apply()
        self.assertTrue(result['applied'])
        for group in ('boss','fixed'):
            for name in FILES[group]:
                expected=(self.resource/name).read_bytes()
                for base in (self.data,self.profile,self.baseline):
                    self.assertEqual((base/name).read_bytes(), expected)
        settings=configparser.ConfigParser();settings.read(self.data/'boss_timer_settings.ini',encoding='utf-8')
        self.assertTrue(settings['settings'].getboolean('schedule_color_data_enabled'))
        self.assertTrue(settings['settings'].getboolean('fixed_boss_color_data_enabled'))
        for key,value in (('discord_token','KEEP'),('current_season_no','18'),('volume','17')):
            self.assertEqual(settings['settings'][key],value)
        self.assertNotIn('private',settings['settings'])
        self.assertEqual((self.data/'web_service/boss_spectrum.json').read_bytes(),(self.resource/'web_assets/boss_spectrum.json').read_bytes())
        for name in ('server_profiles/season_18/9/schedule_state.json','server_profiles/season_18/9/discord_bot.ini','web_service/access.json','voice/user.wav'):
            self.assertEqual((self.data/name).read_bytes(),before[name])
        manifest=json.loads((self.baseline/'manifest.json').read_bytes())
        self.assertEqual(manifest['groups']['alarm'],[{'keep':True}])
        backup=Path(result['backup'])
        self.assertEqual((backup/'server_profiles/season_18/9/init/schedule_fixed_bosses.txt').read_bytes(),b'old-profile')
        self.assertEqual((backup/'boss_timer_settings.ini').read_bytes(),before['boss_timer_settings.ini'])

    def test_same_declaration_never_overwrites_later_user_changes(self):
        self.arm();self.apply()
        self.put(self.profile/'init/schedule_fixed_bosses.txt','user edited after deployment')
        before=self.snapshot()
        self.assertFalse(self.apply()['applied'])
        self.assertEqual(self.snapshot(),before)

    def test_new_declaration_same_app_version_applies_but_older_one_does_not(self):
        old=self.arm(sequence=100);self.apply()
        name='init/schedule_fixed_bosses.txt'
        self.put(self.resource/name,'새 버전|시간|요일|주기|기준|사용|색|색\n')
        self.arm(sequence=200);self.apply()
        self.put(self.profile/name,'new user choice')
        self.put(self.resource/name,'배포|약칭|시간|지역|1|주기|색|1\n')
        self.put(self.resource/DECLARATION,json_bytes(old))
        self.assertFalse(self.apply()['applied'])
        self.assertEqual((self.profile/name).read_text(encoding='utf-8'),'new user choice')
        result=apply_declared_defaults(self.resource,self.data,season='season_18',server='8')
        self.assertFalse(result['applied'])
        self.assertFalse((self.data/'server_profiles/season_18/8/init/schedule_fixed_bosses.txt').exists())

    def test_only_declared_group_changes(self):
        self.arm(groups=('fixed',));before=self.snapshot();self.apply()
        for name in (*FILES['boss'],'web_service/boss_spectrum.json'):
            self.assertEqual((self.data/name).read_bytes(),before[name])
        settings=configparser.ConfigParser();settings.read(self.data/'boss_timer_settings.ini',encoding='utf-8')
        self.assertFalse(settings['settings'].getboolean('schedule_color_data_enabled'))
        self.assertTrue(settings['settings'].getboolean('fixed_boss_color_data_enabled'))

    def test_later_profile_activation_does_not_reset_global_user_preferences(self):
        self.arm();apply_declared_defaults(self.resource,self.data)
        self.put(self.data/'boss_timer_settings.ini','[settings]\nschedule_color_data_enabled=false\n')
        self.apply()
        self.assertIn('=false',(self.data/'boss_timer_settings.ini').read_text())
        self.assertEqual((self.profile/'init/schedule_fixed_bosses.txt').read_bytes(),(self.resource/'init/schedule_fixed_bosses.txt').read_bytes())

    def test_changed_payload_and_corrupt_manifest_fail_before_data_writes(self):
        self.arm();before=self.snapshot()
        self.put(self.resource/'init/schedule_fixed_bosses.txt','변경|시간|요일|주기|기준|사용|색|색\n')
        with self.assertRaisesRegex(ValueError,'선언 후'):
            self.apply()
        self.assertEqual(self.snapshot(),before)
        self.put(self.resource/DECLARATION,'{"schema":1,"enabled":true,"groups":["schedule_state"]}')
        with self.assertRaises(ValueError):self.apply()
        self.assertEqual(self.snapshot(),before)

    def test_failure_restores_files_and_does_not_record_success_then_retry_works(self):
        self.arm();before=self.snapshot()
        def fail(path,raw):
            if Path(path)==self.profile/'init/schedule_fixed_bosses.txt':
                raise PermissionError('fixture locked')
            atomic_write(path,raw)
        with patch('distribution_defaults.atomic_write',side_effect=fail),self.assertRaises(PermissionError):
            self.apply()
        self.assertEqual(self.snapshot(),before)
        self.assertFalse((self.data/STATE).exists())
        self.assertTrue(self.apply()['applied'])

    def test_profile_path_escape_is_rejected(self):
        self.arm();before=self.snapshot()
        with self.assertRaises(ValueError):
            apply_declared_defaults(self.resource,self.data,season='season_18',server='../other')
        self.assertEqual(self.snapshot(),before)

    def test_build_may_change_unrelated_ini_values_but_not_declared_keys(self):
        self.arm()
        self.put(self.resource/'init/default_settings.ini','[settings]\nschedule_color_data_enabled=true\nfixed_boss_color_data_enabled=true\nprivate=changed\nfont=larger\n')
        self.assertIsNotNone(read_declaration(self.resource))
        self.put(self.resource/'init/default_settings.ini','[settings]\nschedule_color_data_enabled=false\nfixed_boss_color_data_enabled=true\n')
        with self.assertRaises(ValueError):read_declaration(self.resource)

    def test_spec_uses_declared_spectrum_instead_of_old_appdata(self):
        self.arm(groups=('spectrum',))
        tree=ast.parse(Path('boss_timer_gui.spec').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='build_distribution_boss_spectrum_seed')
        ns=dict(Path=Path,json=json)
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<spec-default-test>','exec'),ns)
        staging=self.root/'unused-stage'
        self.assertEqual(ns[fn.name](self.resource,str(self.root/'profile'),staging),
                         [(str(self.resource/'web_assets/boss_spectrum.json'),'web_assets')])
        self.assertFalse(staging.exists())


class DistributionStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree=ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))

    def test_application_precedes_loading_runtime_settings_and_profile_definitions(self):
        constructor=next(n for n in ast.walk(self.tree) if isinstance(n,ast.FunctionDef) and n.name=='__init__'
                         and '_apply_declared_distribution_defaults' in ast.unparse(n))
        source=ast.unparse(constructor)
        self.assertEqual(source.count('self._apply_declared_distribution_defaults()'),1)
        self.assertLess(source.index('self._apply_declared_distribution_defaults()'),source.index('self._load_settings()'))
        self.assertLess(source.index('self._apply_declared_distribution_defaults()'),source.index('self._load_discord_bot_settings()'))
        activation=next(n for n in ast.walk(self.tree) if isinstance(n,ast.FunctionDef) and n.name=='_activate_schedule_server_profile')
        source=ast.unparse(activation)
        self.assertEqual(source.count('self._apply_declared_distribution_defaults()'),1)
        self.assertLess(source.index('self._apply_declared_distribution_defaults()'),source.index('self._load_schedule_boss_definitions()'))

    def test_source_execution_does_not_consume_a_distribution_declaration(self):
        fn=next(n for n in ast.walk(self.tree) if isinstance(n,ast.FunctionDef) and n.name=='_apply_declared_distribution_defaults')
        ns=dict(sys=NS(frozen=False))
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<startup-default-test>','exec'),ns)
        with patch('distribution_defaults.apply_declared_defaults') as apply:
            ns[fn.name](NS())
        apply.assert_not_called()


if __name__=='__main__':
    unittest.main()
