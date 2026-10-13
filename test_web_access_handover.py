"""Offline authentication handover checks; no network, Tk, or user files."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import json
import threading
import unittest
from unittest.mock import Mock, patch

import web_access_settings as policy
from web_access_runtime import AccessRuntime, VISITOR_COOKIE, SESSION_COOKIE
from web_service_manager import WebServiceManager
from web_shared_state import encrypt_state, decrypt_state, export_state, import_state


class AccessHandoverTests(unittest.TestCase):
    def setUp(self):
        self.account = 'first'
        self.secrets = {}
        def protect(value):
            blob = 'opaque-' + str(len(self.secrets))
            self.secrets[blob] = (self.account, value)
            return blob
        def unprotect(blob):
            account, value = self.secrets[blob]
            if account != self.account:
                raise policy.SecretStorageError('different account')
            return value
        for name, function in [('protect_secret', protect), ('unprotect_secret', unprotect)]:
            mocked = patch.object(policy, name, side_effect=function)
            mocked.start()
            self.addCleanup(mocked.stop)
        with patch.object(policy, 'read_json', side_effect=FileNotFoundError):
            self.access = policy.AccessSettings(Path('unused'))
        self.access.saved = True
        writer = patch.object(policy, 'write_json')
        writer.start()
        self.addCleanup(writer.stop)
        self.access.change('pin', '1234')
        self.access.change('guest_pin', '5678')

    def runtime(self, data):
        with patch('web_access_runtime.read_json', side_effect=FileNotFoundError):
            runtime = AccessRuntime(Path('unused'), 'run', None)
        runtime.settings = policy.validate_settings(data)
        runtime.available = True
        return runtime

    def cookies(self, data, role):
        runtime = self.runtime(data)
        visitor = 'a' * 32
        epoch = data['guest_epoch' if role == 'guest' else 'epoch']
        return (VISITOR_COOKIE + '=' + runtime._sign('visitor', visitor) + '; ' +
                SESSION_COOKIE + '=' + runtime._sign('session', visitor + ':' + epoch))

    def authorized(self, data, cookies):
        return self.runtime(data).context(cookies, '192.0.2.1', 'Chrome', secure=True)['authenticated']

    def test_same_pin_resave_and_legacy_upgrade_keep_both_logins(self):
        before = deepcopy(self.access.data)
        member = self.cookies(before, 'member')
        guest = self.cookies(before, 'guest')
        self.access.data.pop('protected_pin')
        self.assertEqual(policy.readable_pins(self.access.data)['member']['state'], 'legacy')
        message = self.access.change('pin', '1234')
        self.access.change('guest_pin', '5678')
        for field in ('key', 'epoch', 'guest_epoch', 'salt', 'verifier', 'guest_salt', 'guest_verifier'):
            self.assertEqual(before[field], self.access.data[field])
        self.assertIn('유지', message)
        self.assertTrue(self.authorized(self.access.data, member))
        self.assertTrue(self.authorized(self.access.data, guest))
        self.assertEqual(policy.readable_pins(self.access.data)['member']['value'], '1234')

    def test_changed_pin_expires_only_that_role(self):
        member = self.cookies(self.access.data, 'member')
        guest = self.cookies(self.access.data, 'guest')
        self.access.change('guest_pin', '9012')
        self.assertTrue(self.authorized(self.access.data, member))
        self.assertFalse(self.authorized(self.access.data, guest))
        guest = self.cookies(self.access.data, 'guest')
        self.access.change('pin', '3456')
        self.assertFalse(self.authorized(self.access.data, member))
        self.assertTrue(self.authorized(self.access.data, guest))

    def test_disable_guest_removes_display_but_keeps_member(self):
        member = self.cookies(self.access.data, 'member')
        guest = self.cookies(self.access.data, 'guest')
        self.access.change('guest_disable')
        self.assertNotIn('guest_protected_pin', self.access.data)
        self.assertEqual(policy.readable_pins(self.access.data)['guest']['state'], 'unset')
        self.assertTrue(self.authorized(self.access.data, member))
        self.assertFalse(self.authorized(self.access.data, guest))

    def test_shared_envelope_reprotects_on_other_pc_and_keeps_cookies(self):
        before = deepcopy(self.access.data)
        access, pins = policy.export_access(before)
        self.assertNotIn('protected_pin', access)
        self.assertNotIn('guest_protected_pin', access)
        envelope = encrypt_state(dict(access=access, pins=pins), 'test-token', '100', 'test.duckdns.org')
        self.assertEqual(set(envelope), {'schema', 'domain', 'guild', 'encrypted'})
        self.assertNotIn('pins', envelope)
        data = decrypt_state(envelope, 'test-token', '100', 'test.duckdns.org')
        self.account = 'second'
        after = policy.import_access(data['access'], data['pins'])
        self.assertNotEqual(after['protected_pin'], before['protected_pin'])
        self.assertEqual(policy.readable_pins(after)['guest']['value'], '5678')
        for role in ('member', 'guest'):
            self.assertTrue(self.authorized(after, self.cookies(before, role)))
        self.assertEqual(after['key'], before['key'])
        self.assertEqual(after['epoch'], before['epoch'])
        self.assertEqual(after['guest_epoch'], before['guest_epoch'])

    def test_legacy_transfer_and_display_failure_do_not_reset_authentication(self):
        before = deepcopy(self.access.data)
        self.account = 'second'
        after = policy.import_access(before)  # Old format, including foreign DPAPI metadata.
        self.assertNotIn('protected_pin', after)
        for role in ('member', 'guest'):
            self.assertTrue(self.authorized(after, self.cookies(before, role)))
        with patch.object(policy, 'protect_secret', side_effect=policy.SecretStorageError):
            after = policy.import_access(before, dict(member='1234', guest='5678'))
        self.assertTrue(self.authorized(after, self.cookies(before, 'member')))
        self.assertEqual(policy.readable_pins(after)['member']['state'], 'legacy')
        self.assertEqual(policy.readable_pins(before)['member']['state'], 'unavailable')

    def test_wrong_transferred_pin_and_duplicate_roles_are_rejected(self):
        before = deepcopy(self.access.data)
        with self.assertRaises(ValueError):
            policy.import_access(before, dict(member='0000'))
        with self.assertRaises(ValueError):
            self.access.change('guest_pin', '1234')
        self.assertEqual(self.access.data, before)

    def test_same_verified_certificate_is_reused_but_new_or_broken_certificate_is_replaced(self):
        access, pins = policy.export_access(self.access.data)
        domain = 'test.duckdns.org'
        payload = dict(owner=['100', 'a'*32, 'b'*32, 1], access=access, pins=pins,
                       chain='verified certificate', key='verified private key',
                       external_port=8766, mapping_owner='e'*32, public_enabled=True)
        for existing_chain, broken, expected_publications in ((b'verified certificate', False, 0),
                                                              (b'older certificate', False, 1),
                                                              (b'verified certificate', True, 1)):
            manager = NS(root=Path('unused'), lock=threading.RLock(), access=self.access,
                access_view={}, https=NS(settings={}, lock=threading.RLock(), view={}),
                pin_public_port=Mock(), public_preference_touched=True, public=NS(configure=Mock()), _log=Mock())
            local_certificate = Mock(return_value=(dict(domain=domain), NS(read_bytes=lambda: existing_chain), None, None))
            if broken:
                local_certificate.side_effect = ValueError('invalid local certificate')
            with patch('web_shared_state.read_json', return_value=dict(domain=domain, protected_token='opaque')), \
                    patch('web_shared_state.unprotect_secret', return_value='test-token'), \
                    patch('web_shared_state.decrypt_state', return_value=payload), \
                    patch('cryptography.hazmat.primitives.serialization.load_pem_private_key'), \
                    patch('web_certificate_store.inspect_certificate'), \
                    patch('web_shared_state.load_certificate', local_certificate), \
                    patch('web_shared_state.publish_certificate') as publish, \
                    patch('web_shared_state.public_certificate', return_value={}), \
                    patch('web_shared_state.write_json'):
                import_state(manager, {}, ('100', 'c'*32, 'd'*32, 2), Mock())
            self.assertEqual(publish.call_count, expected_publications)
            self.assertEqual(policy.readable_pins(manager.access.data)['member']['value'], '1234')

    def test_display_is_private_cached_and_survives_restart(self):
        saved = deepcopy(self.access.data)
        with patch.object(policy, 'read_json', return_value=saved):
            restarted = policy.AccessSettings(Path('unused'))
        self.assertEqual(policy.readable_pins(restarted.data)['member']['value'], '1234')
        self.assertNotIn('1234', json.dumps(restarted.public_view()))
        manager = WebServiceManager.__new__(WebServiceManager)
        manager.lock = threading.RLock()
        manager.access = restarted
        manager.desktop_pins = {}
        manager.desktop_pin_signature = None
        manager.wake = threading.Event()
        manager._ensure_coordinator = Mock()
        manager._log = Mock()
        self.assertEqual(manager.desktop_pin_status()['member']['state'], 'checking')
        manager._refresh_desktop_pins()
        with patch('web_service_manager.readable_pins', side_effect=AssertionError('uncached decode')):
            manager._refresh_desktop_pins()
            self.assertEqual(manager.desktop_pin_status()['member']['value'], '1234')
        manager.access.data.pop('protected_pin')
        self.assertEqual(manager.desktop_pin_status()['member']['state'], 'checking')

    def test_actual_shared_export_import_preserves_policy(self):
        before = deepcopy(self.access.data)
        root = Path('unused')
        domain = 'test.duckdns.org'
        settings = dict(domain=domain, protected_token='token')
        pem = Mock()
        pem.private_bytes.return_value = b'private-key'
        chain = Mock()
        chain.read_text.return_value = 'certificate'
        key_path = Mock()
        key_path.read_bytes.return_value = b'private-key'
        first = NS(root=root, settings=dict(external_port=8766),
                   public=NS(settings=dict(owner='f' * 32), status=lambda: dict(enabled=True)),
                   https=NS(settings=dict(terms_agreed_at='accepted')))
        def read(path):
            if path.name == 'ddns_settings.json':
                return settings
            if path.name == 'access_settings.json':
                return before
            raise FileNotFoundError
        with patch('web_shared_state.read_json', side_effect=read), \
                patch('web_shared_state.unprotect_secret', return_value='test-token'), \
                patch('web_shared_state.load_certificate', return_value=(dict(domain=domain), chain, key_path, 'password')), \
                patch('cryptography.hazmat.primitives.serialization.load_pem_private_key', return_value=pem):
            envelope = export_state(first, ('100', 'a' * 32, 'b' * 32, 5))
        self.account = 'second'
        second = NS(root=root, lock=threading.RLock(), access=self.access, access_view={},
                    https=NS(settings={}, lock=threading.RLock(), view={}),
                    pin_public_port=Mock(), previous_mapping_owner='',
                    public_preference_touched=True, public=NS(configure=Mock()))
        held, acquired = [], []
        class OrderedLock:
            def __init__(self, name): self.name = name
            def __enter__(self):
                held.append(self.name)
                acquired.append(self.name)
            def __exit__(self, *_): held.pop()
        second.lock = OrderedLock('manager')
        second.delegation = NS(lock=OrderedLock('relay'), bridge=NS(lock=OrderedLock('bridge')))
        def guarded():
            self.assertEqual(held, ['relay', 'bridge', 'manager'])
        writes = {}
        with patch('web_shared_state.read_json', return_value=settings), \
                patch('web_shared_state.unprotect_secret', return_value='test-token'), \
                patch('cryptography.hazmat.primitives.serialization.load_pem_private_key', return_value=pem), \
                patch('web_certificate_store.inspect_certificate'), \
                patch('web_shared_state.publish_certificate'), \
                patch('web_shared_state.public_certificate', return_value={}), \
                patch('web_shared_state.write_json', side_effect=lambda path, data: writes.update({path.name: data})):
            import_state(second, envelope, ('100', 'c' * 32, 'd' * 32, 6), guarded)
        self.assertEqual(acquired, ['relay', 'bridge', 'manager'])
        self.assertEqual(held, [])
        stored = writes['access_settings.json']
        self.assertEqual(policy.readable_pins(stored)['member']['value'], '1234')
        for role in ('member', 'guest'):
            self.assertTrue(self.authorized(stored, self.cookies(before, role)))


if __name__ == '__main__':
    unittest.main()
