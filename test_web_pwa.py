"""PWA metadata and production handlers. No live account, router or browser."""
from io import BytesIO
import json
import struct
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from urllib.request import urlopen
import threading

from web_asset_store import FILES, bundled_assets
from web_https_app import HTTPSApplication
from web_push import push_asset
from web_service_worker import LocalWebServer


def access(authenticated=False):
    return NS(context=Mock(return_value=dict(authenticated=authenticated, cookies=[], blocked=False,
              configured=True, available=True, reauth_required=False)), read_wait=Mock(return_value=0))


class PwaTests(unittest.TestCase):
    def setUp(self):
        self.files = bundled_assets()
        self.application = HTTPSApplication('localhost', 8766, NS(), access(), Mock(),
                                            assets=self.files, authority_check=lambda: True)

    def get(self, path, **extra):
        response = {}
        environ = dict(REQUEST_METHOD='GET', PATH_INFO=path, HTTP_HOST='localhost:8766',
                       REMOTE_ADDR='127.0.0.1', **{'wsgi.url_scheme': 'https', 'wsgi.input': BytesIO()})
        environ.update(extra)
        body = self.application(environ, lambda code, headers: response.update(code=code, headers=dict(headers)))
        return response, b''.join(body)

    def test_install_identity_scope_and_display_are_stable(self):
        manifest = json.loads(push_asset('/manifest.webmanifest')[0])
        self.assertTrue(manifest['name'])
        self.assertTrue(manifest['short_name'])
        for field in ('id', 'start_url', 'scope'):
            self.assertEqual(manifest[field], '/')
        self.assertEqual(manifest['display'], 'standalone')
        self.assertFalse(manifest['prefer_related_applications'])
        self.assertEqual(manifest['related_applications'], [dict(platform='webapp', url='/manifest.webmanifest', id='/')])
        self.assertEqual({row['sizes'] for row in manifest['icons'] if row['purpose'] == 'any'}, {'192x192', '512x512'})

    def test_both_real_png_sizes_decode_and_only_known_paths_are_public(self):
        for size in (192, 512):
            resource = push_asset(f'/pwa/icon-{size}.png')
            self.assertEqual(resource[1], 'image/png')
            self.assertEqual(resource[0][:8], b'\x89PNG\r\n\x1a\n')
            self.assertEqual(struct.unpack('>II', resource[0][16:24]), (size, size))
            self.assertLess(len(resource[0]), 30000)
        for path in ('/pwa/icon-1.png', '/pwa/../schedule_state.json', '/pwa/icon.svg'):
            self.assertIsNone(push_asset(path))

    def test_https_manifest_and_icons_without_login_have_correct_mime_and_no_redirect(self):
        for path, mime in (('/manifest.webmanifest', 'application/manifest+json'),
                           ('/pwa/icon-192.png', 'image/png'), ('/pwa/icon-512.png', 'image/png'),
                           ('/pwa/install.js', 'text/javascript'), ('/pwa/install.css', 'text/css')):
            response, raw = self.get(path)
            self.assertTrue(response['code'].startswith('200 '), (path, response))
            self.assertTrue(response['headers']['Content-Type'].startswith(mime))
            self.assertNotIn('Location', response['headers'])
            self.assertEqual(raw, push_asset(path)[0])
            self.assertIn("manifest-src 'self'", response['headers']['Content-Security-Policy'])
            self.assertIn("img-src 'self'", response['headers']['Content-Security-Policy'])

    def test_png_etag_revalidates_without_resending_body(self):
        response, _ = self.get('/pwa/icon-512.png')
        self.assertIn('ETag', response['headers'])
        cached, raw = self.get('/pwa/icon-512.png', HTTP_IF_NONE_MATCH=response['headers']['ETag'])
        self.assertTrue(cached['code'].startswith('304 '))
        self.assertEqual(raw, b'')

    def test_https_host_and_authority_fences_are_preserved(self):
        response, _ = self.get('/manifest.webmanifest', **{'wsgi.url_scheme': 'http'})
        self.assertTrue(response['code'].startswith('403 '))
        response, _ = self.get('/manifest.webmanifest', HTTP_HOST='evil.invalid')
        self.assertTrue(response['code'].startswith('403 '))
        self.application.authority_check = lambda: False
        response, _ = self.get('/manifest.webmanifest', REMOTE_ADDR='192.0.2.1')
        self.assertTrue(response['code'].startswith('503 '))

    def test_login_and_schedule_both_link_same_manifest_in_bundled_and_fallback_pages(self):
        from web_service_login import LOGIN_PAGE
        from web_service_page import PAGE
        for page in (self.files['index.html'], self.files['login.html'], PAGE, LOGIN_PAGE):
            self.assertIn(b'<link rel="manifest" href="/manifest.webmanifest">', page)
            self.assertIn(b'src="/pwa/install.js"', page)
            self.assertIn(b'href="/pwa/install.css"', page)
            self.assertEqual(page.count(b'src="/pwa/install.js"'), 1)
            self.assertEqual(page.count(b'href="/pwa/install.css"'), 1)
        self.assertEqual(self.files['index.html'], PAGE)
        self.assertEqual(self.files['login.html'], LOGIN_PAGE)
        response, page = self.get('/')
        self.assertTrue(response['code'].startswith('200 '))
        self.assertIn(b'href="/manifest.webmanifest"', page)
        self.assertIn(b'id="login"', page)

    def test_existing_push_worker_keeps_push_and_does_not_intercept_or_cache_requests(self):
        worker, mime = push_asset('/push-worker.js')
        self.assertIn(b"addEventListener('push'", worker)
        self.assertIn(b"addEventListener('notificationclick'", worker)
        self.assertNotIn(b"addEventListener('fetch'", worker)
        self.assertNotIn(b'caches.', worker)
        self.assertEqual(mime, 'text/javascript; charset=utf-8')
        self.assertEqual(FILES, ('index.html', 'style.css', 'schedule.js', 'login.html', 'login.js', 'metadata.json'))

    def test_existing_loopback_server_serves_same_public_install_resources(self):
        server = LocalWebServer(0, Mock(), NS(), access())
        server.assets = self.files
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path in ('/manifest.webmanifest', '/pwa/icon-192.png', '/pwa/icon-512.png', '/pwa/install.js', '/pwa/install.css'):
                with urlopen(f'http://127.0.0.1:{server.server_port}'+path, timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.read(), push_asset(path)[0])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
