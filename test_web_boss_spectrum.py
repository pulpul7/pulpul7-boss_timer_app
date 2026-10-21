"""Offline developer authorization and deployment tests for web boss colors."""
import ast
from http.client import HTTPMessage
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from web_boss_spectrum import BossSpectrumStore, validate_themes
from web_https_app import HTTPSApplication
from web_service_common import write_json
from web_service_worker import LocalHandler as Handler


class BossSpectrumTests(unittest.TestCase):
    def test_checked_invasion_distribution_color_is_28(self):
        from web_schedule_snapshot import _catalog_id
        from web_boss_spectrum import read_numbers
        numbers = read_numbers(Path('web_assets/boss_spectrum.json'))
        self.assertEqual(numbers[_catalog_id('boss', '체크된침공')], 28)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='boss-spectrum-unit-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.root = self.folder / 'profile' / 'BossTimer' / 'web_service'
        self.root.mkdir(parents=True)
        self.project = self.folder / 'project'
        (self.project / 'web_assets').mkdir(parents=True)
        self.seed = self.project / 'web_assets' / 'boss_spectrum.json'
        self.a, self.b = 'a' * 24, 'b' * 24
        write_json(self.seed, dict(schema=1, themes={'spring': {self.a: 4}, 'rain': {self.b: 8}}))
        replacement = patch('web_boss_spectrum.bundle_path', return_value=self.seed)
        replacement.start();self.addCleanup(replacement.stop)
        self.store = BossSpectrumStore(self.root)
        self.catalog = [dict(id=self.a), dict(id=self.b)]
        self.context = dict(visitor='c' * 32, authenticated=True, blocked=False, cookies=[], available=True)
        self.access = NS(root=self.root, lock=threading.RLock(),
                         visitors={self.context['visitor']: dict(nickname='ordinary')},
                         context=Mock(return_value=self.context), read_wait=Mock(return_value=0))
        self.schedule = NS(payload=Mock(return_value=(200, dict(boss_catalog=self.catalog))))
        assets = {name: b'asset' for name in ('index.html', 'login.html', 'style.css', 'schedule.js', 'login.js')}
        self.app = HTTPSApplication('test.duckdns.org', 8766, self.schedule, self.access, None,
                                    assets=assets, authority_check=lambda: True)

    def call(self, payload=None, origin='https://test.duckdns.org:8766'):
        raw = json.dumps(payload).encode() if payload is not None else b''
        env = dict(REQUEST_METHOD='POST' if payload is not None else 'GET', PATH_INFO='/api/boss-spectrum',
                   HTTP_HOST='test.duckdns.org:8766', REMOTE_ADDR='127.0.0.1', HTTP_ORIGIN=origin,
                   CONTENT_TYPE='application/json', CONTENT_LENGTH=str(len(raw)),
                   **{'wsgi.url_scheme': 'https', 'wsgi.input': BytesIO(raw)})
        reply = {}
        def start(status, headers):reply.update(code=int(status.split()[0]), headers=dict(headers))
        reply['body'] = json.loads(b''.join(self.app(env, start)))
        return reply

    def developer(self):
        self.access.visitors[self.context['visitor']]['nickname'] = '나츠'

    def test_save_uses_one_number_per_boss_for_all_light_themes(self):
        self.store.save('spring', [self.b], 20, self.catalog)
        self.assertEqual(self.store.numbers(), {self.a: 4, self.b: 20})
        self.assertEqual(BossSpectrumStore(self.root).numbers(), self.store.numbers())

    def test_reset_does_not_resurrect_bundled_assignment(self):
        self.store.save('spring', [self.a], 0, self.catalog)
        self.assertEqual(BossSpectrumStore(self.root).numbers(), {self.b: 8})
        self.assertEqual(self.store.numbers(), {self.b: 8})

    def checked_seed(self):
        from web_boss_spectrum import CHECKED_INVASION
        write_json(self.seed, dict(schema=2, numbers={self.a: 4, CHECKED_INVASION: 28}))
        self.catalog.append(dict(id=CHECKED_INVASION))
        return CHECKED_INVASION

    def test_split_checked_invasion_missing_runtime_default_is_visible_without_writes(self):
        from fixed_boss_aliases import catalog_id
        checked = self.checked_seed()
        whole = catalog_id('boss', '침공')
        write_json(self.store.path, dict(schema=2, numbers={self.a: 12, self.b: 8, whole: 28}))
        before = self.store.path.read_bytes()
        self.assertEqual(self.call()['body']['numbers'], {self.a: 12, self.b: 8, whole: 28, checked: 28})
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_checked_invasion_explicit_change_and_reset_survive_new_store(self):
        checked = self.checked_seed()
        self.store.save('gray', [checked], 15, self.catalog)
        self.assertEqual(BossSpectrumStore(self.root).numbers()[checked], 15)
        self.store.save('gray', [checked], 0, self.catalog)
        self.assertEqual(BossSpectrumStore(self.root).numbers(), {self.a: 4})

    def test_checked_default_preserves_existing_assignment_and_other_save(self):
        checked = self.checked_seed()
        write_json(self.store.path, dict(schema=2, numbers={checked: 17, self.b: 8}))
        self.assertEqual(self.store.numbers(), {checked: 17, self.b: 8})
        self.store.save('gray', [self.b], 24, self.catalog)
        self.assertEqual(BossSpectrumStore(self.root).numbers(), {checked: 17, self.b: 24})

    def test_renamed_fixed_color_is_migrated_without_read_writes_and_reset_sticks(self):
        from fixed_boss_aliases import catalog_id
        from test_fixed_boss_aliases import old_id
        identity, old = catalog_id('fixed', '알브 닻'), old_id('fixed', '알브하임 닻')
        write_json(self.store.path, dict(schema=2, numbers={old: 29, self.a: 4}))
        before = self.store.path.read_bytes()
        self.assertEqual(self.store.numbers(), {identity: 29, self.a: 4})
        self.assertEqual(self.store.path.read_bytes(), before)
        self.store.save('spring', [identity], 0, [dict(id=identity)])
        self.assertEqual(BossSpectrumStore(self.root).numbers(), {self.a: 4})
        self.assertNotIn(old, json.loads(self.store.path.read_text())['numbers'])

    def test_invalid_requests_do_not_write(self):
        for theme, bosses, number in [('unknown', [self.a], 1), ('default', [self.a], 1), ('spring', [self.a], True),
                                      ('spring', [self.a], 31), ('spring', [], 2),
                                      ('spring', ['d'*24], 3), ('spring', ['../token'], 1)]:
            with self.assertRaises(ValueError):self.store.save(theme, bosses, number, self.catalog)
        self.assertFalse(self.store.path.exists())
        with self.assertRaises(ValueError):validate_themes({'spring': {self.a: False}})

    def test_color_thirty_can_be_saved_and_reloaded(self):
        self.developer()
        reply = self.call(dict(theme='spring', bosses=[self.a], number=30))
        self.assertEqual(reply['code'], 200)
        self.assertEqual(BossSpectrumStore(self.root).numbers()[self.a], 30)

    def test_new_color_theme_names_are_accepted_with_shared_numbers(self):
        self.developer()
        for theme in ('sky', 'wood', 'green', 'blue', 'magenta', 'ivory', 'canary', 'forsythia', 'purple', 'gray'):
            reply = self.call(dict(theme=theme, bosses=[self.a], number=27))
            self.assertEqual(reply['code'], 200, theme)
            self.assertEqual(reply['body']['numbers'][self.a], 27)
            self.assertEqual(reply['body']['target'], 'background')

    def test_old_theme_maps_migrate_without_dark_assignments(self):
        write_json(self.store.path, dict(schema=1, themes={'spring': {self.a: 4},
            'rain': {self.b: 8, self.a: 12}, 'default': {self.a: 20}}))
        self.assertEqual(self.store.numbers(), {self.a: 12, self.b: 8})
        self.store.save('winter', [self.b], 2, self.catalog)
        self.assertEqual(json.loads(self.store.path.read_text(encoding='utf-8')),
                         {'schema': 2, 'target': 'background', 'numbers': {self.a: 12, self.b: 2}})

    def test_new_runtime_reset_preserves_empty_shared_map(self):
        self.store.save('sakura', [self.a, self.b], 0, self.catalog)
        self.assertEqual(BossSpectrumStore(self.root).public()['numbers'], {})

    def test_normal_admin_can_read_but_cannot_forge_developer_save(self):
        reply = self.call()
        self.assertEqual(reply['code'], 200)
        self.assertFalse(reply['body']['can_save'])
        denied = self.call(dict(theme='spring', bosses=[self.a], number=10, nickname='나츠', is_master=True))
        self.assertEqual(denied['code'], 403)
        self.assertFalse(self.store.path.exists())

    def test_developer_save_origin_auth_and_limit(self):
        self.developer()
        job = dict(theme='spring', bosses=[self.a], number=10)
        self.assertEqual(self.call(job, origin='https://other.test')['code'], 403)
        self.context['authenticated'] = False
        self.assertEqual(self.call(job)['code'], 401)
        self.context['authenticated'] = True
        self.access.read_wait.return_value = 2
        self.assertEqual(self.call(job)['code'], 429)
        self.access.read_wait.return_value = 0
        self.assertEqual(self.call(job)['code'], 200)
        self.assertEqual(self.store.numbers()[self.a], 10)
        self.assertTrue(self.call()['body']['can_save'])

    def test_worker_routes_share_validation_and_authorization(self):
        handler = Handler.__new__(Handler)
        handler.path = '/api/boss-spectrum'
        handler.client_address = ('127.0.0.1', 12345)
        handler.server = NS(access=self.access, schedule=self.schedule, logger=None, assets={})
        handler._context = Mock(return_value=self.context)
        handler._json = Mock()
        job = dict(theme='rain', bosses=[self.a], number=5)
        raw = json.dumps(job).encode()
        handler.headers = HTTPMessage()
        for key, value in [('Host', '127.0.0.1:8765'), ('Origin', 'http://127.0.0.1:8765'),
                           ('Content-Type', 'application/json'), ('Content-Length', str(len(raw)))]:
            handler.headers[key] = value
        handler.rfile = BytesIO(raw);handler.do_POST()
        self.assertEqual(handler._json.call_args.args[0], 403)
        self.developer();handler.rfile = BytesIO(raw);handler.do_POST()
        self.assertEqual(handler._json.call_args.args[0], 200)
        handler.do_GET()
        self.assertEqual(handler._json.call_args.args[1]['numbers'][self.a], 5)

    def test_broken_color_file_is_isolated_from_schedule_api(self):
        self.store.path.write_text('{invalid', encoding='utf-8')
        self.assertEqual(self.call()['code'], 503)
        env = dict(REQUEST_METHOD='GET', PATH_INFO='/api/schedule', HTTP_HOST='test.duckdns.org:8766',
                   REMOTE_ADDR='127.0.0.1', **{'wsgi.url_scheme': 'https'})
        reply = {}
        self.app(env, lambda status, headers:reply.update(code=int(status.split()[0])))
        self.assertEqual(reply['code'], 200)

    def test_distribution_seed_contains_only_valid_color_numbers(self):
        write_json(self.store.path, dict(schema=1, token='must-not-copy',
            themes={'spring': {self.a: 30, self.b: True}, 'rain': {}, 'unknown': {self.a: 3}}))
        tree = ast.parse(Path('boss_timer_gui.spec').read_text(encoding='utf-8'))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'build_distribution_boss_spectrum_seed')
        namespace = dict(Path=Path, json=json)
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<spectrum-seed-test>', 'exec'), namespace)
        stage = self.folder / 'stage';stage.mkdir()
        datas = namespace[function.name](self.project, str(self.folder / 'profile'), stage)
        self.assertEqual(datas, [(str(stage / 'boss_spectrum.json'), 'web_assets')])
        raw = (stage / 'boss_spectrum.json').read_text(encoding='utf-8')
        self.assertNotIn('token', raw)
        self.assertNotIn('must-not-copy', raw)
        self.assertEqual(json.loads(raw), {'schema': 2, 'target': 'background', 'numbers': {self.a: 30}})
        self.assertEqual(BossSpectrumStore(self.folder/'fresh', bundled=stage/'boss_spectrum.json').numbers(),
                         {self.a: 30})


if __name__ == '__main__':
    unittest.main()
