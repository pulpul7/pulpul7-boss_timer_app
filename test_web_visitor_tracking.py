"""Offline visitor accounting checks; no live server or user files."""
import hashlib
from email.message import Message
from pathlib import Path
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import Mock, patch

from web_access_runtime import AccessRuntime, records_visit
from web_access_settings import ITERATIONS, validate_settings
from web_direct_hosting import DirectHostingService, PublicError
from web_firewall import FirewallError
from web_https_app import HTTPSApplication
from web_service_worker import LocalHandler


class VisitorTrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        salt = '1' * 32
        cls.policy = validate_settings(dict(schema=1, revision=1, iterations=ITERATIONS,
            key='2' * 64, epoch='3' * 32, salt=salt,
            verifier=hashlib.pbkdf2_hmac('sha256', b'1234', bytes.fromhex(salt), ITERATIONS).hex(),
            banned_ips={}, banned_visitors={}))

    def setUp(self):
        with patch('web_access_runtime.read_json', side_effect=FileNotFoundError):
            self.runtime = AccessRuntime(Path('unused'), 'a' * 32, None)
        self.runtime.settings = validate_settings(self.policy)
        self.runtime.available = True
        self.runtime.dirty = False

    def context(self, path, cookie='', ip='127.0.0.1', method='GET'):
        return self.runtime.context(cookie, ip, 'Chrome Windows',
            record_visit=records_visit(method, path, ip, cookie))

    def test_repeated_stateless_probes_and_assets_do_not_create_visitors(self):
        for ip in ('127.0.0.1', '::1'):
            for _ in range(30):
                result = self.context('/api/session', ip=ip)
                self.assertTrue(result['configured'])
                self.assertTrue(result['available'])
                self.assertFalse(result['authenticated'])
                self.assertEqual(result['visitor'], '')
                self.assertEqual(result['cookies'], [])
        for path in ('/style.css', '/schedule.js', '/login.js', '/favicon.ico', '/sw.js', '/unknown'):
            self.assertEqual(self.context(path, ip='192.0.2.1')['cookies'], [])
        self.context('/', method='OPTIONS')
        self.assertEqual(self.runtime.visitors, {})
        self.assertFalse(self.runtime.dirty)

    def test_browser_and_external_session_requests_still_record(self):
        local = self.context('/')
        external = self.context('/api/session', ip='192.0.2.1')
        self.assertNotEqual(local['visitor'], external['visitor'])
        self.assertEqual(len(self.runtime.visitors), 2)
        self.assertTrue(records_visit('GET', '/api/session', '127.0.0.1', 'bt_visitor=bad'))
        self.assertTrue(records_visit('GET', '/api/schedule', '127.0.0.1', ''))

    def test_asset_authentication_pin_expiry_and_bans_remain_enforced(self):
        first = self.context('/')
        cookie = first['cookies'][0].split(';', 1)[0]
        code, _, cookies = self.runtime.login(first, '1234', '127.0.0.1', nickname='guest')
        self.assertEqual(code, 200)
        cookie += '; ' + cookies[0].split(';', 1)[0]
        row = dict(self.runtime.visitors[first['visitor']])
        self.runtime.dirty = False
        self.assertTrue(self.context('/style.css', cookie)['authenticated'])
        self.assertEqual(self.runtime.visitors[first['visitor']], row)
        self.assertFalse(self.runtime.dirty)
        self.runtime.settings['epoch'] = '4' * 32
        expired = self.context('/style.css', cookie)
        self.assertFalse(expired['authenticated'])
        self.assertTrue(expired['reauth_required'])
        self.assertIn('Max-Age=0', expired['cookies'][0])
        self.runtime.settings['banned_visitors'][first['visitor']] = 'blocked'
        self.assertTrue(self.context('/style.css', cookie)['blocked'])
        self.runtime.settings['banned_ips']['127.0.0.1'] = 'blocked'
        self.assertTrue(self.context('/api/session')['blocked'])

    def test_login_failure_and_display_distinguish_unauthed_visitor(self):
        first = self.context('/')
        self.assertEqual(self.runtime.login(first, '9999', '127.0.0.1')[0], 401)
        self.assertEqual(self.runtime.visitors[first['visitor']]['failures'], 1)
        with patch('web_access_runtime.write_json') as writer:
            self.runtime.flush(force=True)
        self.assertEqual(writer.call_args_list[-1].args[1]['visitors'][0]['nickname'], '\uBBF8\uC778\uC99D \uBC29\uBB38')
        self.assertEqual(self.runtime.visitors[first['visitor']]['nickname'], 'guest')
        self.assertEqual(self.runtime.login(first, '1234', '127.0.0.1', nickname='guest')[0], 200)
        with patch('web_access_runtime.write_json') as writer:
            self.runtime.flush(force=True)
        self.assertEqual(writer.call_args_list[-1].args[1]['visitors'][0]['nickname'], 'guest')

    def test_https_probe_skips_accounting_but_cannot_read_schedule(self):
        assets = {name: b'asset' for name in ('index.html', 'login.html', 'style.css', 'login.js', 'schedule.js')}
        app = HTTPSApplication('test.duckdns.org', 8766, Mock(), self.runtime, None, assets=assets)
        response = {}
        def start(status, headers):
            response.update(status=status, headers=headers)
        def get(path):
            return app(dict(REQUEST_METHOD='GET', PATH_INFO=path, REMOTE_ADDR='127.0.0.1',
                HTTP_HOST='test.duckdns.org:8766', **{'wsgi.url_scheme': 'https'}), start)
        with patch.object(self.runtime, 'read_wait', return_value=0) as limiter:
            for _ in range(10):
                get('/api/session')
                self.assertTrue(response['status'].startswith('200'))
                self.assertFalse(any(k == 'Set-Cookie' for k, _ in response['headers']))
            limiter.assert_not_called()
            get('/api/schedule')
            self.assertTrue(response['status'].startswith('401'))
            limiter.assert_called_once()
        app.schedule.payload.assert_not_called()

    def test_http_probe_uses_same_exclusion(self):
        handler = LocalHandler.__new__(LocalHandler)
        handler.path, handler.command = '/api/session', 'GET'
        handler.client_address, handler.headers = ('127.0.0.1', 1000), Message()
        handler.headers['Host'] = '127.0.0.1:8765'
        handler.server = NS(access=self.runtime, server_port=8765, assets={},
                            counter_lock=threading.Lock(), request_count=0)
        handler._send, handler._json = Mock(), Mock()
        with patch.object(self.runtime, 'read_wait') as limiter:
            handler.do_GET()
            limiter.assert_not_called()
        self.assertFalse(handler._record_visit)
        self.assertEqual(self.runtime.visitors, {})


class PublicationDiagnosticTests(unittest.TestCase):
    def test_cleanup_retains_resources_and_firewall_reason(self):
        service = DirectHostingService.__new__(DirectHostingService)
        service.resources = dict(firewall={'owned': True}, mapping=None)
        service.resources_path, service.logger = Mock(), None
        service._policy = Mock()
        with patch('web_direct_hosting.firewall_apply', side_effect=FirewallError('administrator_required')), \
             patch('web_direct_hosting.write_json'), patch('web_direct_hosting.diagnostic') as log:
            with self.assertRaises(PublicError) as raised:
                service._cleanup()
        self.assertEqual(raised.exception.code, 'administrator_required')
        self.assertIsNotNone(service.resources['firewall'])
        service.resources_path.unlink.assert_not_called()
        self.assertEqual(log.call_args.kwargs['code'], 'administrator_required')


if __name__ == '__main__':
    unittest.main()
