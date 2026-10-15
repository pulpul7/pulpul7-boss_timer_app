"""First-paint theme restoration and strict CSP checks, without browser/network/builds."""
import base64
import hashlib
from http.client import HTTPMessage
from io import BytesIO
from pathlib import Path
import re
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from web_https_app import HTTPSApplication
from web_service_worker import LocalHandler
from web_theme_bootstrap import SCRIPT, SCRIPT_SOURCE, render_theme_page


PAGE = b'<html lang="ko"><head><meta charset="utf-8"><link rel="stylesheet" href="/style.css"><script src="/schedule.js" defer></script></head><body></body></html>'


class ThemeBootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='boss-theme-bootstrap-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'BossTimer' / 'web_service'
        self.root.mkdir(parents=True)
        self.context = dict(visitor='a'*32, authenticated=True, blocked=False, cookies=[])
        self.access = NS(root=self.root, lock=threading.RLock(), visitors={'a'*32: dict(nickname='other')},
                         context=Mock(return_value=self.context), read_wait=Mock(return_value=0))
        self.assets = {name: PAGE if name.endswith('.html') else b'asset'
                       for name in ('index.html', 'login.html', 'style.css', 'schedule.js', 'login.js')}

    def test_fixed_script_precedes_stylesheet_and_hash_matches_exact_bytes(self):
        page = render_theme_page(PAGE)
        self.assertLess(page.index(b'id="bt-theme-bootstrap"'), page.index(b'rel="stylesheet"'))
        inline = re.search(rb'<script id="bt-theme-bootstrap">(.*?)</script>', page, re.S)[1]
        self.assertEqual(inline, SCRIPT)
        self.assertEqual(SCRIPT_SOURCE, "'sha256-" + base64.b64encode(hashlib.sha256(inline).digest()).decode() + "'")
        self.assertIn(b'data-theme-master="false"', page)
        self.assertIn(b'src="/schedule.js" defer', page)

    def test_downloaded_master_flag_is_replaced_by_server_decision(self):
        page = render_theme_page(PAGE.replace(b'lang="ko"', b'lang="ko" data-theme-master="true"'), False)
        self.assertNotIn(b'data-theme-master="true"', page)
        self.assertEqual(page.count(b'data-theme-master='), 1)
        self.assertIn(b'data-theme-master="true"', render_theme_page(PAGE, True))

    def call(self):
        reply = {}
        app = HTTPSApplication('test.duckdns.org', 8766, Mock(), self.access, None,
                               authority_check=lambda: True, assets=self.assets)
        env = dict(REQUEST_METHOD='GET', PATH_INFO='/', HTTP_HOST='test.duckdns.org:8766', REMOTE_ADDR='127.0.0.1',
                   **{'wsgi.url_scheme': 'https', 'wsgi.input': BytesIO()})
        def start(status, headers):reply.update(code=int(status.split()[0]), headers=dict(headers))
        reply['body'] = b''.join(app(env, start))
        return reply

    def test_https_first_page_has_approved_script_and_no_general_inline_permission(self):
        reply = self.call()
        self.assertEqual(reply['code'], 200)
        self.assertIn(SCRIPT, reply['body'])
        policy = reply['headers']['Content-Security-Policy']
        self.assertIn(SCRIPT_SOURCE, policy)
        self.assertNotIn('unsafe-inline', policy)
        self.assertEqual(int(reply['headers']['Content-Length']), len(reply['body']))
        self.assertEqual(reply['headers']['Cache-Control'], 'no-store')

    def test_master_bootstrap_is_verified_before_async_font_catalog(self):
        (self.root.parent / 'master_developer.ini').write_text('[master_developer]\nenabled=1\n', encoding='utf-8')
        self.assertIn(b'data-theme-master="true"', self.call()['body'])
        self.context['authenticated'] = False
        self.assertIn(b'data-theme-master="false"', self.call()['body'])

    def test_local_worker_first_page_uses_same_bootstrap(self):
        handler = LocalHandler.__new__(LocalHandler)
        handler.path = '/';handler.client_address = ('127.0.0.1', 12345)
        handler.server = NS(access=self.access, assets=self.assets)
        handler._context = Mock(return_value=self.context);handler._send = Mock()
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], 200)
        self.assertIn(SCRIPT, handler._send.call_args.args[1])
        self.assertIn(b'data-theme-master="false"', handler._send.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
