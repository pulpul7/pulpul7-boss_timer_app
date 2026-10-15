"""Offline font-size authorization, persistence and deployment data checks. No builds."""
import ast
import hashlib
from io import BytesIO
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from web_font_library import WebFontLibrary, font_developer_allowed
from web_https_app import HTTPSApplication
from web_service_common import write_json


class FontDefaultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='boss-font-default-unit-')
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.project = self.folder / 'project'
        self.bundle = self.project / 'web_assets' / 'fonts'
        self.bundle.mkdir(parents=True)
        rows = []
        for key in ('font-a', 'font-b'):
            raw = b'wOF2' + key.encode()
            (self.bundle / (key+'.woff2')).write_bytes(raw)
            rows.append(dict(id=key, number=len(rows)+1, name=key, file=key+'.woff2',
                             sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)))
        write_json(self.bundle / 'catalog.json', dict(schema=1, default='system', fonts=rows))
        write_json(self.bundle / 'size_defaults.json', dict(schema=1, sizes={'font-a': 2, 'font-b': -1}))
        self.root = self.folder / 'profile' / 'BossTimer' / 'web_service'
        self.root.mkdir(parents=True)
        patched = patch('web_font_library.bundle_folder', return_value=self.bundle)
        patched.start();self.addCleanup(patched.stop)
        self.library = WebFontLibrary(self.root)
        self.visitor = 'a' * 32
        self.context = dict(visitor=self.visitor, authenticated=True, blocked=False, cookies=[])
        self.access = NS(root=self.root, lock=threading.RLock(), visitors={self.visitor: dict(nickname='ordinary')},
                         context=Mock(return_value=self.context), read_wait=Mock(return_value=0))
        assets = {name: b'asset' for name in ('index.html', 'login.html', 'style.css', 'schedule.js', 'login.js')}
        self.app = HTTPSApplication('test.duckdns.org', 8766, Mock(), self.access, None,
                                    assets=assets, authority_check=lambda: True)

    def call(self, path='/fonts/catalog.json', payload=None, ip='192.0.2.44', origin=None):
        reply = {}
        raw = json.dumps(payload).encode() if payload is not None else b''
        env = dict(REQUEST_METHOD='POST' if payload is not None else 'GET', PATH_INFO=path,
                   HTTP_HOST='test.duckdns.org:8766', REMOTE_ADDR=ip,
                   HTTP_ORIGIN=origin or 'https://test.duckdns.org:8766', CONTENT_TYPE='application/json',
                   CONTENT_LENGTH=str(len(raw)), **{'wsgi.url_scheme': 'https', 'wsgi.input': BytesIO(raw)})
        def start(status, headers):reply.update(code=int(status.split()[0]), headers=dict(headers))
        reply['body'] = b''.join(self.app(env, start))
        return reply

    def test_ordinary_admin_cannot_enable_or_forge_default_save(self):
        self.assertFalse(json.loads(self.call()['body'])['can_save_default'])
        denied = self.call('/api/fonts/default-size', dict(font_id='font-a', size=3, nickname='나츠', is_master=True))
        self.assertEqual(denied['code'], 403)
        self.assertFalse((self.root / 'font_settings.json').exists())

    def test_natsu_default_save_persists_and_changes_catalog_etag(self):
        self.access.visitors[self.visitor]['nickname'] = '나츠'
        before = self.call()
        self.assertTrue(json.loads(before['body'])['can_save_default'])
        saved = self.call('/api/fonts/default-size', dict(font_id='font-a', size=3))
        self.assertEqual(saved['code'], 200)
        self.assertEqual(WebFontLibrary(self.root).public_catalog()['size_defaults']['font-a'], 3)
        self.assertEqual(self.library.public_catalog()['size_defaults']['font-b'], -1)
        self.assertNotEqual(before['headers']['ETag'], self.call()['headers']['ETag'])

    def test_master_is_local_pc_only_and_normal_mode_is_denied(self):
        path = self.root.parent / 'master_developer.ini'
        path.write_text('[master_developer]\nenabled=0\n', encoding='utf-8')
        self.assertFalse(font_developer_allowed(self.access, self.context, '127.0.0.1'))
        path.write_text('[master_developer]\nenabled=1\n', encoding='utf-8')
        self.assertTrue(font_developer_allowed(self.access, self.context, '127.0.0.1'))
        with patch('web_font_library.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('192.0.2.1', 0))]):
            self.assertTrue(font_developer_allowed(self.access, self.context, '192.0.2.1'))
            self.assertFalse(font_developer_allowed(self.access, self.context, '192.0.2.44'))

    def test_unauthorized_origin_and_invalid_sizes_do_not_write(self):
        self.access.visitors[self.visitor]['nickname'] = '나츠'
        self.assertEqual(self.call('/api/fonts/default-size', dict(font_id='font-a', size=3),
                                   origin='https://another.test')['code'], 403)
        for key, value in (('font-a', True), ('font-a', 7), ('font-a', -5), ('missing', 2), ('../path', 2)):
            self.assertEqual(self.call('/api/fonts/default-size', dict(font_id=key, size=value))['code'], 400)
        self.assertFalse((self.root / 'font_settings.json').exists())
        self.context['authenticated'] = False
        self.assertEqual(self.call('/api/fonts/default-size', dict(font_id='font-a', size=3))['code'], 401)

    def test_export_includes_sizes_and_next_install_uses_them(self):
        self.library.set_size_default('system', 1)
        self.library.set_size_default('font-a', 4)
        destination = self.folder / 'export';destination.mkdir()
        self.library.export(destination)
        next_library = WebFontLibrary(self.folder / 'next-install', bundled=destination)
        self.assertEqual(next_library.public_catalog()['size_defaults'], {'system': 1, 'font-a': 4, 'font-b': -1})

    def test_build_seed_is_only_numbers_for_fonts_in_distribution(self):
        write_json(self.root / 'font_settings.json', dict(schema=1, token='must-not-copy',
            font_size_defaults={'font-a': 3, 'font-b': True, 'unbundled-font': 6}))
        spec = ast.parse(Path('boss_timer_gui.spec').read_text(encoding='utf-8'))
        function = next(n for n in spec.body if isinstance(n, ast.FunctionDef) and n.name=='build_distribution_font_size_seed')
        namespace = dict(Path=Path, json=json)
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<font-size-seed-test>', 'exec'), namespace)
        staging = self.folder / 'stage';staging.mkdir()
        datas = namespace[function.name](self.project, str(self.folder / 'profile'), staging)
        self.assertEqual(datas, [(str(staging / 'size_defaults.json'), 'web_assets/fonts')])
        raw = (staging / 'size_defaults.json').read_text(encoding='utf-8')
        self.assertNotIn('token', raw);self.assertNotIn('must-not-copy', raw)
        self.assertEqual(json.loads(raw), {'schema': 1, 'sizes': {'font-a': 3, 'font-b': -1}})


if __name__ == '__main__':
    unittest.main()
