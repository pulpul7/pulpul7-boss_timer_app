"""Offline checks for reload limits and cached assets; no listeners or user files."""
from io import BytesIO
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from web_request_limits import ReadLimiter
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
