"""Offline font ownership, packaging and read-only HTTP endpoint checks."""
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from web_font_library import WebFontLibrary, bundle_folder
from web_https_app import HTTPSApplication
from web_service_common import write_json
from web_service_worker import LocalHandler


class WebFontTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='boss-web-font-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.library = WebFontLibrary(self.root)

    def test_twenty_fonts_eight_sites_and_license_files(self):
        from urllib.parse import urlsplit
        catalog = json.loads((bundle_folder() / 'catalog.json').read_text(encoding='utf-8'))
        self.assertEqual(len(catalog['fonts']), 20)
        self.assertGreaterEqual(len({urlsplit(row['source']).hostname for row in catalog['fonts']}), 7)
        for row in catalog['fonts']:
            raw = (bundle_folder() / row['file']).read_bytes()
            self.assertEqual(raw[:4], b'wOF2')
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row['sha256'])
            self.assertTrue((bundle_folder() / row['license_file']).is_file())

    def test_public_catalog_is_read_only_and_hides_local_paths(self):
        catalog = self.library.public_catalog()
        self.assertEqual(catalog['default'], 'cafe24-dongdong')
        self.assertEqual(next(row['number'] for row in catalog['fonts'] if row['id'] == catalog['default']), 15)
        self.assertTrue(all(row['protected'] for row in catalog['fonts']))
        self.assertNotIn('folder', json.dumps(catalog))
        self.assertNotIn(str(self.root), json.dumps(catalog))
        self.assertIsNone(self.library.asset('/fonts/../../catalog.json'))
        self.assertIsNone(self.library.asset('/fonts/missing.woff2'))
        self.assertIsNone(self.library.asset('/fonts/catalog.json/anything'))
        first = catalog['fonts'][0]
        license_bytes, mime = self.library.asset(first['license_url'])
        self.assertIn(b'SIL OPEN FONT LICENSE', license_bytes)
        self.assertTrue(mime.startswith('text/plain'))
        self.assertIsNone(self.library.asset('/fonts/licenses/../../catalog.json'))

    def test_default_persists_and_cannot_be_removed(self):
        key = self.library.public_catalog()['fonts'][0]['id']
        self.library.set_default(key)
        self.assertEqual(WebFontLibrary(self.root).public_catalog()['default'], key)
        for protected in ('system', key):
            with self.assertRaises(ValueError):
                self.library.delete(protected)
        self.library.set_default('system')
        self.library.delete(key)
        self.assertNotIn(key, [row['id'] for row in self.library.public_catalog()['fonts']])

    def test_admin_add_delete_and_duplicate_rejection_preserve_file(self):
        source = self.root / 'personal.ttf'
        source.write_bytes(b'\x00\x01\x00\x00' + b'font-fixture' * 5)
        key = self.library.add(source)
        with self.assertRaises(ValueError):
            self.library.add(source)
        catalog = self.library.public_catalog()
        added = next(row for row in catalog['fonts'] if row['id'] == key)
        self.assertEqual(added['number'], 21)
        self.assertTrue(added['protected'])
        filename = added['url'].split('/fonts/')[1].split('?')[0]
        self.assertEqual(self.library.asset('/fonts/' + filename)[0], source.read_bytes())
        self.library.delete(key)
        self.assertTrue((self.root / 'fonts' / filename).exists())
        self.assertIsNone(self.library.asset('/fonts/' + filename))

    def test_export_makes_choice_and_custom_font_the_next_bundle_default(self):
        source = self.root / 'custom.otf';source.write_bytes(b'OTTO' + b'font-fixture')
        key = self.library.add(source);self.library.set_default(key)
        destination = self.root / 'fonts-export';destination.mkdir()
        self.library.export(destination)
        exported = WebFontLibrary(self.root / 'next-admin', bundled=destination)
        self.assertEqual(exported.public_catalog()['default'], key)
        self.assertEqual(len(exported.public_catalog()['fonts']), 21)
        # The original manager can read its exported bundle without duplicate private entries.
        self.assertEqual(WebFontLibrary(self.root, bundled=destination).public_catalog()['default'], key)

    def test_bad_hash_or_manifest_path_never_serves_a_font(self):
        folder = self.root / 'bundle';folder.mkdir()
        rows = json.loads((bundle_folder() / 'catalog.json').read_text(encoding='utf-8'))
        first = rows['fonts'][0];first['file'] = '../../secret.ttf'
        write_json(folder / 'catalog.json', rows)
        with self.assertRaises(ValueError):
            WebFontLibrary(self.root, bundled=folder).public_catalog()

    def test_empty_export_keeps_system_and_does_not_reuse_old_numbers(self):
        self.library.set_default('system')
        for row in self.library.public_catalog()['fonts']:
            self.library.delete(row['id'])
        destination = self.root / 'empty-export';destination.mkdir()
        self.library.export(destination)
        next_library = WebFontLibrary(self.root / 'next-manager', bundled=destination)
        self.assertEqual(next_library.public_catalog()['fonts'], [])
        self.assertEqual(next_library.public_catalog()['default'], 'system')
        source = self.root / 'new.ttf';source.write_bytes(b'\x00\x01\x00\x00font-fixture')
        next_library.add(source)
        self.assertEqual(next_library.public_catalog()['fonts'][0]['number'], 21)

    def app(self, authenticated=True):
        context = dict(authenticated=authenticated, blocked=False, cookies=[])
        access = SimpleNamespace(root=self.root, context=Mock(return_value=context), read_wait=Mock(return_value=0))
        assets = {name: b'asset' for name in ('index.html', 'login.html', 'style.css', 'schedule.js', 'login.js')}
        return HTTPSApplication('test.duckdns.org', 8766, Mock(), access, None,
                                assets=assets, authority_check=lambda: True)

    def call(self, app, path='/fonts/catalog.json', extra=None):
        result = {}
        environ = dict(REQUEST_METHOD='GET', PATH_INFO=path, REMOTE_ADDR='127.0.0.1',
                       HTTP_HOST='test.duckdns.org:8766', **{'wsgi.url_scheme': 'https'})
        environ.update(extra or {})
        def start(status, headers):
            result.update(code=int(status.split()[0]), headers=dict(headers))
        result['body'] = b''.join(app(environ, start))
        return result

    def test_https_requires_login_and_returns_etag_and_font_csp(self):
        self.assertEqual(self.call(self.app(False))['code'], 401)
        app = self.app();result = self.call(app)
        self.assertEqual(result['code'], 200)
        self.assertEqual(len(json.loads(result['body'])['fonts']), 20)
        self.assertIn("font-src 'self' blob:", result['headers']['Content-Security-Policy'])
        cached = self.call(app, extra={'HTTP_IF_NONE_MATCH': result['headers']['ETag']})
        self.assertEqual(cached['code'], 304)
        self.assertEqual(cached['body'], b'')

    def test_font_failure_is_isolated_from_schedule(self):
        app = self.app()
        with patch('web_font_library.font_asset', side_effect=ValueError('bad font')):
            self.assertEqual(self.call(app)['code'], 503)
        app.schedule.payload.return_value = (200, dict(events=[]))
        self.assertEqual(self.call(app, '/api/schedule')['code'], 200)

    def test_local_route_serves_same_catalog_without_starting_server(self):
        handler = object.__new__(LocalHandler)
        context = dict(authenticated=True, cookies=[])
        handler.server = SimpleNamespace(access=SimpleNamespace(root=self.root), assets={}, logger=None)
        handler._context = Mock(return_value=context)
        handler._authorized = Mock(return_value=True)
        handler._send = Mock();handler._json = Mock()
        handler.path = '/fonts/catalog.json';handler._record_visit = False
        handler.client_address = ('127.0.0.1', 12345)
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], 200)
        self.assertEqual(len(json.loads(handler._send.call_args.args[1])['fonts']), 20)


if __name__ == '__main__':
    unittest.main()
