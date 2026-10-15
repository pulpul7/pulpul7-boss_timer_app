"""Offline checks for reload limits and cached assets; no listeners or user files."""
from io import BytesIO
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from web_request_limits import CooldownLimiter, ReadLimiter
from web_access_runtime import AccessRuntime
from web_access_settings import validate_settings, ITERATIONS
from pathlib import Path
import hashlib
from web_https_app import HTTPSApplication
from web_service_worker import LocalHandler


class ReadLimitTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.limiter = ReadLimiter(clock=lambda: self.now)

    def test_browser_burst_wait_and_normal_polling(self):
        for _ in range(8):
            self.assertEqual(self.limiter.wait('visitor', '192.0.2.1'), 0)
        self.assertEqual(self.limiter.wait('visitor', '192.0.2.1'), 2)
        self.now += 1
        self.assertEqual(self.limiter.wait('visitor', '192.0.2.1'), 1)
        self.now += 1
        self.assertEqual(self.limiter.wait('visitor', '192.0.2.1'), 0)
        for _ in range(100):
            self.now += 15
            self.assertEqual(self.limiter.wait('visitor', '192.0.2.1'), 0)

    def test_same_ip_with_new_cookie_cannot_bypass_ip_limit(self):
        for index in range(120):
            self.assertEqual(self.limiter.wait(str(index), '192.0.2.1'), 0)
        self.assertGreater(self.limiter.wait('another', '192.0.2.1'), 0)
        self.now = 1
        self.assertEqual(self.limiter.wait('another', '192.0.2.1'), 0)

    def test_many_normal_clients_and_bounded_identity_cache(self):
        for _ in range(20):
            for index in range(50):
                self.assertEqual(self.limiter.wait(str(index), '192.0.2.1'), 0)
            self.now += 15
        for index in range(10000):
            self.limiter.wait(str(index), str(index))
        self.assertLessEqual(len(self.limiter.buckets), 4096)
        self.assertGreater(self.limiter.wait('new', 'new'), 0)
        self.now += 200
        self.assertEqual(self.limiter.wait('fresh', '192.0.2.1'), 0)
        self.assertLessEqual(len(self.limiter.buckets), 3)


class CooldownTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.limiter = CooldownLimiter(clock=lambda: self.now)

    def make_access(self):
        with patch('web_access_runtime.read_json', side_effect=FileNotFoundError):
            access = AccessRuntime(Path('unused'), 'a' * 32, None)
        access.cooldowns = self.limiter
        salt = '1' * 32
        access.settings = validate_settings(dict(schema=1, revision=1, iterations=ITERATIONS,
            key='2' * 64, epoch='3' * 32, salt=salt,
            verifier=hashlib.pbkdf2_hmac('sha256', b'1234', bytes.fromhex(salt), ITERATIONS).hex(),
            banned_ips={}, banned_visitors={}))
        access.available = True
        return access

    def test_rejected_reloads_do_not_extend_five_second_window(self):
        key = ('reload', 'a', True)
        self.assertEqual(self.limiter.claim([key], 5), 0)
        for at, wait in ((0, 5), (1.1, 4), (4.99, 1), (5, 0)):
            self.now = at
            self.assertEqual(self.limiter.claim([key], 5), wait)

    def test_separate_visitors_and_bounded_storage(self):
        for index in range(10000):
            self.limiter.claim([('reload', str(index), True)], 5)
        self.assertLessEqual(len(self.limiter.deadlines), 4096)
        self.assertGreater(self.limiter.claim([('reload', 'new', True)], 5), 0)
        self.now = 11
        self.assertEqual(self.limiter.claim([('reload', 'new', True)], 5), 0)
        self.assertEqual(len(self.limiter.deadlines), 1)

    def test_logout_blocks_login_without_password_work_and_expires_at_two_seconds(self):
        access = self.make_access()
        first = access.context('', '192.0.2.1', 'test')
        code, _, issued = access.login(first, '1234', '192.0.2.1')
        self.assertEqual(code, 200)
        cookies = '; '.join(cookie.split(';')[0] for cookie in [*first['cookies'], *issued])
        authenticated = access.context(cookies, '192.0.2.1', 'test')
        self.assertTrue(authenticated['authenticated'])
        access.logout(authenticated)
        with patch('web_access_runtime.hashlib.pbkdf2_hmac') as hash_pin:
            self.assertEqual(access.login(authenticated, '1234', '192.0.2.1')[0], 429)
            self.now = 1.999
            self.assertEqual(access.login(first, '1234', '192.0.2.1')[0], 429)
            hash_pin.assert_not_called()
        self.assertFalse(access.context(cookies, '192.0.2.1', 'test')['authenticated'])
        self.now = 2
        self.assertEqual(access.login(first, '1234', '192.0.2.1')[0], 200)

    def test_page_gate_does_not_throttle_normal_read_api_or_other_known_viewer(self):
        with patch('web_access_runtime.read_json', side_effect=FileNotFoundError):
            access = AccessRuntime(Path('unused'), 'a' * 32, None)
        access.cooldowns = self.limiter
        first = dict(visitor='a', authenticated=True)
        second = dict(visitor='b', authenticated=True)
        self.assertEqual(access.read_wait(first, '192.0.2.1', path='/'), 0)
        self.assertEqual(access.read_wait(first, '192.0.2.1', path='/'), 5)
        self.assertEqual(access.read_wait(first, '192.0.2.1', path='/api/status'), 0)
        self.assertEqual(access.read_wait(second, '192.0.2.1', path='/'), 0)
        access.logout(first)
        first['authenticated'] = False
        self.assertEqual(access.read_wait(first, '192.0.2.1', path='/'), 0)

    def test_https_login_logout_redirects_are_not_mistaken_for_manual_reloads(self):
        import json
        access = self.make_access()
        app = HTTPSApplication('test.duckdns.org', 8766, Mock(), access, None, authority_check=lambda: True,
            assets={name: b'asset' for name in ('index.html', 'login.html', 'schedule.js', 'login.js', 'style.css')})
        cookie_values = {}

        def send(path, payload=None):
            reply = {}
            def start(status, headers):
                reply.update(code=int(status.split()[0]), headers=headers)
                for name, value in headers:
                    if name == 'Set-Cookie':
                        key, content = value.split(';')[0].split('=', 1)
                        cookie_values[key] = content
            raw = json.dumps(payload).encode() if payload is not None else b''
            body = app(dict(REQUEST_METHOD='POST' if payload is not None else 'GET', PATH_INFO=path,
                HTTP_HOST='test.duckdns.org:8766', REMOTE_ADDR='192.0.2.1',
                HTTP_COOKIE='; '.join(f'{k}={v}' for k, v in cookie_values.items()),
                HTTP_ORIGIN='https://test.duckdns.org:8766', CONTENT_TYPE='application/json',
                CONTENT_LENGTH=str(len(raw)), **{'wsgi.url_scheme': 'https', 'wsgi.input': BytesIO(raw)}), start)
            reply['body'] = b''.join(body)
            return reply

        self.assertEqual(send('/')['code'], 200)
        self.assertEqual(send('/api/login', {'pin': '1234'})['code'], 200)
        self.assertEqual(send('/')['code'], 200)
        throttled = send('/')
        self.assertEqual(throttled['code'], 429)
        self.assertIn(('Retry-After', '5'), throttled['headers'])
        self.assertEqual(send('/api/logout', {})['code'], 200)
        self.assertEqual(send('/')['code'], 200)
        denied = send('/api/login', {'pin': '1234'})
        self.assertEqual(denied['code'], 429)
        self.assertIn(('Retry-After', '2'), denied['headers'])
        self.now = 2
        self.assertEqual(send('/api/login', {'pin': '1234'})['code'], 200)
        self.assertEqual(send('/')['code'], 200)


class ReadEndpointTests(unittest.TestCase):
    def setUp(self):
        self.context = dict(cookies=[], blocked=False, authenticated=True,
                            available=True, configured=True, visitor='a' * 32, reauth_required=False)
        self.access = NS(context=Mock(return_value=self.context), read_wait=Mock(return_value=0))
        self.schedule = NS(payload=Mock(return_value=(200, dict(events=[]))), status=Mock())
        self.assets = {name: b'asset' for name in ('index.html', 'login.html', 'schedule.js', 'login.js', 'style.css')}
        self.app = HTTPSApplication('test.duckdns.org', 8766, self.schedule, self.access, None,
                                    authority_check=lambda: True, assets=self.assets)

    def request(self, path, **headers):
        response = {}
        def start(status, values):
            response.update(status=int(status.split()[0]), headers=values)
        body = self.app(dict(REQUEST_METHOD='GET', PATH_INFO=path, HTTP_HOST='test.duckdns.org:8766',
                             REMOTE_ADDR='192.0.2.1', **{'wsgi.url_scheme': 'https'}, **headers), start)
        response['body'] = b''.join(body)
        return response

    def test_https_status_passes_catalog_version_through_existing_poll(self):
        self.schedule.status.return_value = dict(schedule_updated_at=None, schedule_version='schedule', catalog_version='catalog')
        reply = self.request('/api/status', QUERY_STRING='major_version=old-major&catalog_version=old-catalog')
        self.assertEqual(reply['status'], 200)
        self.schedule.status.assert_called_once_with(known_major_version='old-major', known_catalog_version='old-catalog')

    def test_local_status_passes_catalog_version_through_existing_poll(self):
        handler = LocalHandler.__new__(LocalHandler)
        handler.path = '/api/status?major_version=old-major&catalog_version=old-catalog'
        handler.client_address = ('127.0.0.1', 10000)
        handler._context = Mock(return_value=self.context)
        handler._json = Mock()
        handler._send = Mock()
        self.schedule.status.return_value = dict(schedule_updated_at=None, schedule_version='schedule', catalog_version='catalog')
        handler.server = NS(assets=self.assets, access=self.access, schedule=self.schedule, started_at='now')
        handler.do_GET()
        self.assertEqual(handler._json.call_args.args[0], 200)
        self.schedule.status.assert_called_once_with(known_major_version='old-major', known_catalog_version='old-catalog')

    def test_https_throttle_preserves_session_and_skips_payload(self):
        self.access.read_wait.return_value = 2
        reply = self.request('/api/schedule')
        self.assertEqual(reply['status'], 429)
        self.assertIn(('Retry-After', '2'), reply['headers'])
        self.assertFalse(any(name == 'Set-Cookie' for name, _ in reply['headers']))
        self.schedule.payload.assert_not_called()
        self.access.read_wait.return_value = 0
        self.assertEqual(self.request('/api/schedule')['status'], 200)

    def test_https_repeated_reload_returns_friendly_page(self):
        self.access.read_wait.return_value = 2
        reply = self.request('/')
        self.assertEqual(reply['status'], 429)
        self.assertIn(b'<html', reply['body'])
        self.assertIn(('Retry-After', '2'), reply['headers'])

    def test_https_assets_revalidate_without_redownload_or_duplicate_cache_header(self):
        reply = self.request('/schedule.js')
        etag = dict(reply['headers'])['ETag']
        self.assertEqual([value for name, value in reply['headers'] if name == 'Cache-Control'], ['private, no-cache'])
        reply = self.request('/schedule.js', HTTP_IF_NONE_MATCH=etag)
        self.assertEqual(reply['status'], 304)
        self.assertEqual(reply['body'], b'')
        self.assertNotIn('Content-Length', dict(reply['headers']))
        self.access.read_wait.assert_not_called()
        self.app.assets['schedule.js'] = b'new asset'
        # A new application owns the next validated web asset version.
        other = HTTPSApplication('test.duckdns.org', 8766, self.schedule, self.access, None, assets=self.app.assets)
        self.assertNotEqual(other.asset_etags['/schedule.js'], etag)

    def test_loopback_asset_headers_and_304(self):
        handler = LocalHandler.__new__(LocalHandler)
        handler.path = '/style.css'
        handler.headers = {}
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = BytesIO()
        handler._send(200, b'css', 'text/css')
        etag = next(call.args[1] for call in handler.send_header.call_args_list if call.args[0] == 'ETag')
        handler.headers['If-None-Match'] = etag
        handler.wfile = BytesIO()
        handler.send_header.reset_mock()
        handler._send(200, b'css', 'text/css')
        handler.send_response.assert_called_with(304)
        self.assertEqual(handler.wfile.getvalue(), b'')
        self.assertFalse(any(call.args[0] == 'Content-Length' for call in handler.send_header.call_args_list))

    def test_loopback_throttle_skips_schedule_work(self):
        handler = LocalHandler.__new__(LocalHandler)
        handler.path = '/api/schedule'
        handler.client_address = ('127.0.0.1', 10000)
        handler._context = Mock(return_value=self.context)
        handler._send = Mock()
        self.access.read_wait.return_value = 2
        handler.server = NS(assets=self.assets, access=self.access, schedule=self.schedule)
        handler.do_GET()
        self.assertEqual(handler._send.call_args.args[0], 429)
        self.assertEqual(handler._send.call_args.args[-1], (('Retry-After', '2'),))
        self.schedule.payload.assert_not_called()


if __name__ == '__main__':
    unittest.main()
