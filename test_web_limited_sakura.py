"""Offline blossom integrity, private asset caching and failure isolation checks."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from web_https_app import HTTPSApplication
from web_limited_sakura import ASSET_PATH, ATLAS_SHA256, BUTTERFLY_PATH, BUTTERFLY_SHA256, theme_asset
from web_service_worker import LocalHandler
from sync_web_sakura import compact_module, compact_embedded_indent


class LimitedSakuraTests(unittest.TestCase):
    def test_readable_animation_matches_compact_web_module(self):
        root = Path(__file__).resolve().parent
        script = (root / 'web_assets/schedule.js').read_text(encoding='utf-8')
        embedded = script.split('// BEGIN WEB LIMITED SAKURA MODULE\n', 1)[1].split(
            '\n// END WEB LIMITED SAKURA MODULE', 1)[0]
        self.assertEqual(embedded, compact_module(
            (root / 'web_limited_sakura.js').read_text(encoding='utf-8')))
        self.assertLessEqual(len(script.encode('utf-8')), 1024 * 1024)

    def test_compaction_keeps_dom_fields_and_literal_text(self):
        result = compact_module('let canvas; canvas.width=72; canvas.height=72; '
                                'const message="canvas active petals"; // comment\n'
                                'const value=`url("${URL.createObjectURL(blob)}")`;')
        self.assertIn('.width=72', result)
        self.assertIn('.height=72', result)
        self.assertIn('"canvas active petals"', result)
        self.assertIn('`url("${URL.createObjectURL(blob)}")`', result)
        self.assertNotIn('// comment', result)

    def test_embedded_indent_keeps_single_line_literal_content(self):
        code = '// BEGIN WEB FONT MODULE\n  const text=`  keep spaces  `;\n// END WEB FONT MODULE'
        result = compact_embedded_indent(code)
        self.assertIn('\nconst text=`  keep spaces  `;', result)
        self.assertEqual(compact_embedded_indent(result), result)

    def test_embedded_indent_does_not_rewrite_multiline_literals(self):
        code = '// BEGIN WEB FONT MODULE\n  const text=`line\n  keep literal indentation`;\n// END WEB FONT MODULE'
        self.assertEqual(compact_embedded_indent(code), code)

    def test_scrolling_keeps_inner_attached_flowers_visible(self):
        css = (Path(__file__).resolve().parent / 'web_assets/style.css').read_text(encoding='utf-8')
        self.assertNotIn('data-sakura-scrolling', css)
        source = (Path(__file__).resolve().parent / 'web_limited_sakura.js').read_text(encoding='utf-8')
        self.assertNotIn('data-sakura-scrolling', source)
        self.assertIn('html[data-theme="spring"] .sakura-anchor{position:relative}', css)
        self.assertIn('steps(var(--sakura-strip-steps,83),end)', css)
        self.assertNotIn('steps(35,end)', css)

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

    def test_butterfly_script_is_bounded_and_matches_its_hash(self):
        raw, mime = theme_asset(BUTTERFLY_PATH)
        self.assertEqual(mime, 'text/javascript; charset=utf-8')
        self.assertLessEqual(len(raw), 64 * 1024)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), BUTTERFLY_SHA256)
        self.assertIn(b'window.WebSakuraButterfly=', raw)
        css = (Path(__file__).resolve().parent / 'web_assets/style.css').read_text(encoding='utf-8')
        self.assertIn('transform:scale(var(--sakura-insect-scale,.5))', css)
        self.assertIn('transition:transform .7s ease', css)
        self.assertIn('sakura-butterfly[data-role="boss"]', css)
        self.assertIn('.sakura-butterfly-layer{position:fixed;inset:0;overflow:hidden;overflow:clip;', css)
        self.assertIn('contain:layout paint size;pointer-events:none;', css)
        self.assertIn('animation-duration:2.8s', css)
        self.assertIn('.spawn-blink{animation:schedule-spawn-pulse', css)

    def test_butterfly_script_authentication_and_own_etag(self):
        self.assertEqual(self.call(self.app(False), BUTTERFLY_PATH)['code'], 401)
        app = self.app()
        reply = self.call(app, BUTTERFLY_PATH)
        self.assertEqual(reply['code'], 200)
        self.assertIn("script-src 'self'", reply['headers']['Content-Security-Policy'])
        self.assertEqual(reply['headers']['ETag'], '"' + BUTTERFLY_SHA256 + '"')
        self.assertEqual(self.call(app, BUTTERFLY_PATH, {'HTTP_IF_NONE_MATCH': reply['headers']['ETag']})['code'], 304)
        self.assertEqual(self.call(app, BUTTERFLY_PATH, {'HTTP_IF_NONE_MATCH': '"' + ATLAS_SHA256 + '"'})['code'], 200)
        self.assertEqual(self.call(app, BUTTERFLY_PATH + '/extra')['code'], 404)

    def test_butterfly_hash_accepts_windows_source_newlines(self):
        original, _ = theme_asset(BUTTERFLY_PATH)
        with tempfile.TemporaryDirectory(prefix='boss-butterfly-unit-') as temporary:
            folder = Path(temporary)
            (folder / 'butterfly.js').write_bytes(original.replace(b'\n', b'\r\n'))
            with patch('web_limited_sakura.bundle_folder', return_value=folder):
                self.assertEqual(theme_asset(BUTTERFLY_PATH)[0], original)

    def test_altered_butterfly_script_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='boss-butterfly-unit-') as temporary:
            folder = Path(temporary)
            (folder / 'butterfly.js').write_bytes(b'alert("changed");')
            with patch('web_limited_sakura.bundle_folder', return_value=folder):
                with self.assertRaises(ValueError):
                    theme_asset(BUTTERFLY_PATH)

    def test_local_listener_serves_authenticated_butterfly_script(self):
        handler = object.__new__(LocalHandler)
        handler.server = SimpleNamespace(assets={}, logger=None)
        handler._context = Mock(return_value=dict(authenticated=True, cookies=[]))
        handler._authorized = Mock(return_value=True)
        handler._send = Mock(); handler._json = Mock()
        handler.path = BUTTERFLY_PATH; handler._record_visit = False
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[:3], (200, *theme_asset(BUTTERFLY_PATH)))
        handler._send.reset_mock(); handler._authorized.return_value = False
        handler.do_GET(); handler._send.assert_not_called()

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
