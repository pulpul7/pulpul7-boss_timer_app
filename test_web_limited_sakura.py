"""Offline blossom integrity, private asset caching and failure isolation checks."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from web_https_app import HTTPSApplication
from web_limited_sakura import ASSET_PATH, ATLAS_SHA256, theme_asset
from web_service_worker import LocalHandler


class LimitedSakuraTests(unittest.TestCase):
    def app(self, authenticated=True):
        context = dict(authenticated=authenticated, blocked=False, cookies=[])
        access = SimpleNamespace(context=Mock(return_value=context), read_wait=Mock(return_value=0))
        assets = {name: b'asset' for name in ('index.html', 'login.html', 'style.css', 'schedule.js', 'login.js')}
        return HTTPSApplication('test.duckdns.org', 8766, Mock(), access, None,
                                assets=assets, authority_check=lambda: True)

    def call(self, app, path=ASSET_PATH, extra=None):
        result = {}
        env = dict(REQUEST_METHOD='GET', PATH_INFO=path, REMOTE_ADDR='127.0.0.1',
                   HTTP_HOST='test.duckdns.org:8766', **{'wsgi.url_scheme': 'https'})
        env.update(extra or {})
        def start(status, headers):
            result.update(code=int(status.split()[0]), headers=dict(headers))
        result['body'] = b''.join(app(env, start))
        return result

    def test_atlas_is_bounded_rgba_and_matches_immutable_hash(self):
        raw, mime = theme_asset(ASSET_PATH)
        self.assertEqual(mime, 'image/png')
        self.assertLess(len(raw), 4 * 1024 * 1024)
        self.assertEqual(raw[25], 6)  # PNG RGBA, not a flattened background.
        self.assertEqual(hashlib.sha256(raw).hexdigest(), ATLAS_SHA256)

    def test_only_the_exact_sprite_path_is_served(self):
        for path in ('/themes/../index.html', '/themes/%2e%2e/index.html',
                     ASSET_PATH + '/extra', '/themes/sources.json'):
            self.assertIsNone(theme_asset(path))
            self.assertEqual(self.call(self.app(), path)['code'], 404)

    def test_https_requires_login_and_returns_304_for_cached_atlas(self):
        self.assertEqual(self.call(self.app(False))['code'], 401)
        app = self.app();reply = self.call(app)
        self.assertEqual(reply['code'], 200)
        self.assertEqual(reply['headers']['Content-Type'], 'image/png')
        self.assertIn("img-src 'self' blob:", reply['headers']['Content-Security-Policy'])
        self.assertEqual(reply['headers']['Cache-Control'], 'private, no-cache')
        cached = self.call(app, extra={'HTTP_IF_NONE_MATCH': reply['headers']['ETag']})
        self.assertEqual(cached['code'], 304)
        self.assertEqual(cached['body'], b'')

    def test_missing_art_does_not_interrupt_schedule(self):
        app = self.app()
        with patch('web_limited_sakura.theme_asset', side_effect=OSError('missing')):
            self.assertEqual(self.call(app)['code'], 503)
        app.schedule.payload.return_value = (200, dict(events=[]))
        self.assertEqual(self.call(app, '/api/schedule')['code'], 200)

    def test_altered_atlas_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='boss-blossom-unit-') as temporary:
            folder = Path(temporary)
            (folder / 'botanical-atlas-v1.png').write_bytes(b'not the atlas')
            with patch('web_limited_sakura.bundle_folder', return_value=folder):
                with self.assertRaises(ValueError): theme_asset(ASSET_PATH)

    def test_local_listener_has_identical_asset_and_authentication(self):
        handler = object.__new__(LocalHandler)
        handler.server = SimpleNamespace(assets={}, logger=None)
        handler._context = Mock(return_value=dict(authenticated=True, cookies=[]))
        handler._authorized = Mock(return_value=True)
        handler._send = Mock();handler._json = Mock()
        handler.path = ASSET_PATH;handler._record_visit = False
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[:3], (200, *theme_asset(ASSET_PATH)))
        handler._send.reset_mock();handler._authorized.return_value = False
        handler.do_GET();handler._send.assert_not_called()


if __name__ == '__main__':
    unittest.main()
