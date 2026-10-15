"""Offline fast-publication checks: every socket, router, file write and firewall is mocked."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import Mock, patch

import web_direct_hosting as hosting


class FastConnectTests(unittest.TestCase):
    def test_local_https_distinguishes_unset_pin_from_unreadable_access_policy(self):
        for data, expected in (
                (dict(available=True, configured=False), '웹 비밀번호를 먼저 설정'),
                (dict(available=False, configured=False), '웹 접속 설정을 읽지 못했습니다'),
                (dict(available=True, configured=True), None)):
            with self.subTest(data=data):
                response = NS(status=200, read=Mock(return_value=json.dumps(data).encode()))
                class Connection:
                    def __init__(self, *args, **kwargs):
                        pass
                    request = Mock()
                    getresponse = Mock(return_value=response)
                    close = Mock()
                with patch.object(hosting.http.client, 'HTTPSConnection', Connection), \
                     patch.object(hosting, 'create_verified_ssl_context'), \
                     patch.object(hosting.socket, 'create_connection') as connect:
                    if expected:
                        with self.assertRaisesRegex(hosting.PublicError, expected):
                            hosting.check_local_https('example.duckdns.org', 8766)
                    else:
                        hosting.check_local_https('example.duckdns.org', 8766)
                connect.assert_not_called()
                Connection.close.assert_called_once()

    def service(self):
        self.domain = 'example.duckdns.org'
        self.context = dict(https_state='running', run_id='local-run', preferred_external_port=443)
        with patch.object(hosting, 'read_json', side_effect=FileNotFoundError):
            service = hosting.DirectHostingService(Path('unused-offline-root'), lambda: deepcopy(self.context))
        service.active = True
        service.closed = threading.Event()
        self.route = dict(local='192.168.0.4', gateway='192.168.0.1', prefix=24, index=7)
        self.router = dict(control='http://192.168.0.1:1234/control',
            service='urn:schemas-upnp-org:service:WANIPConnection:1', wan='113.52.205.156')
        self.mapping = dict(control=self.router['control'], service=self.router['service'],
            gateway=self.route['gateway'], client=self.route['local'], internal_port=8766,
            external_port=443, description='BossTimerWeb-' + service.settings['owner'])
        self.cache = dict(schema=1, owner=service.settings['owner'], domain=self.domain, port=8766,
            route=self.route, router=self.router, mapping=self.mapping)
        return service

    def start_patches(self, service, cached):
        def read(path):
            if path.name == 'access_settings.json': return dict(verifier='configured')
            if path.name == 'https_settings.json': return dict(domain=self.domain, port=8766)
            if path.name == 'ddns_settings.json': return dict(domain='example', enabled=True)
            raise FileNotFoundError
        patches = (
            patch.object(hosting, 'read_json', side_effect=read),
            patch.object(hosting, 'validate_settings', side_effect=lambda value: value),
            patch.object(hosting, 'load_certificate', return_value=(dict(domain=self.domain), None, None, None)),
            patch.object(hosting, 'check_local_https'),
            patch.object(hosting, 'diagnostic'),
            patch.object(service, '_cached_network', return_value=cached),
            patch.object(hosting, '_windows_network', return_value='inventory'),
            patch.object(hosting, '_route', return_value=self.route),
            patch.object(hosting, '_upnp', return_value=self.router),
            patch.object(hosting, '_public_ipv4', return_value=self.router['wan']),
            patch.object(service, '_publish'),
            patch.object(service, '_cleanup'),
            patch.object(Path, 'unlink'),
        )
        mocks = [self.enterContext(item) for item in patches]
        return NS(inventory=mocks[6], discover=mocks[8], public_ip=mocks[9],
                  publish=mocks[10], cleanup=mocks[11])

    def test_successful_fast_attempt_skips_inventory_discovery_and_public_ip_lookup(self):
        service = self.service()
        mocks = self.start_patches(service, (self.route, self.router))
        service._start(service.generation)
        mocks.publish.assert_called_once()
        mocks.inventory.assert_not_called()
        mocks.discover.assert_not_called()
        mocks.public_ip.assert_not_called()

    def test_failed_fast_attempt_cleans_up_then_runs_full_diagnostics_once(self):
        service = self.service()
        mocks = self.start_patches(service, (self.route, self.router))
        order = []
        def publish(*_args):
            order.append('publish')
            if len(order) == 1: raise hosting.PublicError('cached endpoint failed')
        mocks.publish.side_effect = publish
        mocks.cleanup.side_effect = lambda: order.append('cleanup')
        service._start(service.generation)
        self.assertEqual(order, ['publish', 'cleanup', 'publish'])
        mocks.inventory.assert_called_once()
        mocks.discover.assert_called_once()
        mocks.public_ip.assert_called_once()

    def test_cancelled_attempt_or_firewall_permission_failure_never_runs_network_fallback(self):
        service = self.service()
        mocks = self.start_patches(service, (self.route, self.router))
        for error in (hosting.PublicCancelled(), hosting.FirewallError('administrator_required')):
            with self.subTest(error_type=type(error).__name__):
                mocks.publish.side_effect = error
                with self.assertRaises(type(error)): service._start(service.generation)
        mocks.inventory.assert_not_called()
        mocks.cleanup.assert_not_called()

    def test_failed_cleanup_blocks_the_fallback_attempt(self):
        service = self.service()
        mocks = self.start_patches(service, (self.route, self.router))
        mocks.publish.side_effect = hosting.PublicError('failed')
        mocks.cleanup.side_effect = hosting.PublicError('cannot remove owned rules')
        with self.assertRaises(hosting.PublicError): service._start(service.generation)
        mocks.inventory.assert_not_called()
        self.assertEqual(mocks.publish.call_count, 1)

    def test_cache_checks_current_route_and_router_wan_without_network_inventory(self):
        service = self.service()
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock()
        probe.getsockname.return_value = ('192.168.0.4', 12345)
        with patch.object(hosting, 'read_json', return_value=self.cache), \
             patch.object(hosting.socket, 'socket', return_value=probe), \
             patch.object(hosting, 'soap', return_value='response') as soap, \
             patch.object(hosting, '_text', return_value=self.router['wan']), \
             patch.object(hosting, '_windows_network') as inventory:
            self.assertEqual(service._cached_network(service.generation, self.domain, 8766, 443),
                             (self.route, self.router))
            inventory.assert_not_called()
            soap.assert_called_once_with(self.mapping, 'GetExternalIPAddress', {})
            probe.getsockname.return_value = ('192.168.1.4', 12345)
            self.assertIsNone(service._cached_network(service.generation, self.domain, 8766, 443))
            self.assertEqual(soap.call_count, 1)

    def test_stale_or_foreign_cache_never_contacts_its_router(self):
        service = self.service()
        for change in (dict(owner='wrong'), dict(domain='other.duckdns.org'), dict(port=9000),
                       dict(mapping=dict(self.mapping, control='http://203.0.113.1/control'))):
            with self.subTest(change=change):
                cached = dict(deepcopy(self.cache), **change)
                with patch.object(hosting, 'read_json', return_value=cached), \
                     patch.object(hosting.socket, 'socket') as socket, patch.object(hosting, 'soap') as soap:
                    self.assertIsNone(service._cached_network(service.generation, self.domain, 8766, 443))
                    socket.assert_not_called()
                    soap.assert_not_called()

    def test_changed_or_private_router_wan_rejects_the_cache(self):
        service = self.service()
        probe = Mock()
        probe.__enter__ = Mock(return_value=probe)
        probe.__exit__ = Mock()
        probe.getsockname.return_value = ('192.168.0.4', 12345)
        with patch.object(hosting, 'read_json', return_value=self.cache), \
             patch.object(hosting.socket, 'socket', return_value=probe), \
             patch.object(hosting, 'soap', return_value='response'), patch.object(hosting, 'diagnostic'):
            for wan in ('113.52.205.157', '192.168.1.1', '2001:4860::1'):
                with self.subTest(wan=wan), patch.object(hosting, '_text', return_value=wan):
                    self.assertIsNone(service._cached_network(service.generation, self.domain, 8766, 443))

    def test_waiting_for_local_https_does_not_sleep_ten_seconds_or_repeat_discovery(self):
        service = self.service()
        waits = []
        service.wake = NS(clear=Mock(), wait=lambda seconds: (waits.append(seconds), service.closed.set()))
        with patch.object(hosting, 'diagnostic_logger'), patch.object(hosting, 'close_logger'), \
             patch.object(service, '_cleanup'), patch.object(service, '_start', side_effect=hosting.HTTPSWaiting()), \
             patch.object(hosting, '_windows_network') as inventory, patch.object(hosting, '_upnp') as discovery:
            service._loop()
        self.assertEqual(waits, [0.5])
        inventory.assert_not_called()
        discovery.assert_not_called()

    def test_real_publication_still_checks_port_ownership_and_saves_cache_only_after_ready(self):
        service = self.service()
        self.context.update(https_public=True)
        def enable(_enabled, resource=None):
            if resource: self.context['https_activation'] = resource['activation']
        service._policy = Mock(side_effect=enable)
        service.context = lambda: deepcopy(self.context)
        with patch.object(hosting, 'write_json') as write, patch.object(hosting, 'firewall_apply'), \
             patch.object(hosting, 'specific', return_value=None) as specific, \
             patch.object(hosting, 'add', return_value=False) as add, \
             patch.object(hosting, 'check_local_https'), patch.object(hosting, 'diagnostic'):
            service._publish(service.generation, self.context, self.domain, 8766, self.route, self.router)
            self.assertEqual(service.status()['state'], 'prepared')
            specific.assert_called_once()
            add.assert_called_once()
            self.assertEqual(write.call_args.args[0], service.connection_cache_path)
            self.assertEqual(write.call_args.args[1]['route'], self.route)


if __name__ == '__main__':
    unittest.main()
