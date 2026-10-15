"""Offline setup checks: no workers, sockets, Windows crypto or user-data writes."""
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import Mock, patch

from web_service_manager import WebServiceManager
from web_ddns_service import DDNSService
import web_ddns_service as ddns_module
import web_access_settings as access_module


class SetupTests(unittest.TestCase):
    def new_access(self):
        with patch.object(access_module, 'read_json', side_effect=FileNotFoundError):
            return access_module.AccessSettings(Path('unused-offline-root'))

    def initialize_access(self, access):
        with patch.object(Path, 'mkdir'), patch.object(access_module, 'write_json') as write, \
             patch.object(access_module, 'protect_secret', side_effect=lambda pin: 'protected-' + pin):
            access.ensure_saved()
        return deepcopy(write.call_args.args[1])

    def test_new_policy_has_no_default_password_and_does_not_generate_one(self):
        access = self.new_access()
        before = deepcopy(access.data)
        with patch.object(access_module.hashlib, 'pbkdf2_hmac', side_effect=AssertionError('password generated')):
            stored = self.initialize_access(access)
        self.assertEqual(stored, before)
        self.assertFalse(access_module.pin_matches(stored, '', '1111'))
        self.assertFalse(access_module.pin_matches(stored, 'guest_', '0000'))
        self.assertEqual(before['key'], stored['key'])
        self.assertEqual(before['epoch'], stored['epoch'])
        self.assertEqual(before['guest_epoch'], stored['guest_epoch'])
        with self.assertRaisesRegex(ValueError, '회원용'):
            access.require_member_pin()
        self.assertFalse(access.public_view()['member_configured'])
        self.assertFalse(access.public_view()['guest_configured'])
        self.assertEqual(access_module.readable_pins(stored)['member']['state'], 'unset')
        self.assertEqual(access_module.readable_pins(stored)['guest']['state'], 'unset')
        with patch.object(access_module, 'write_json') as write, \
             patch.object(access_module.hashlib, 'pbkdf2_hmac', side_effect=AssertionError('defaults repeated')):
            access.ensure_saved()
        write.assert_not_called()

    def test_saved_member_password_and_disabled_guest_are_preserved(self):
        access = self.new_access()
        self.initialize_access(access)
        with patch.object(access_module, 'write_json'), patch.object(access_module, 'protect_secret', return_value='protected'):
            access.change('pin', '2468')
        stored = deepcopy(access.data)
        with patch.object(access_module, 'read_json', return_value=stored):
            restarted = access_module.AccessSettings(Path('unused-offline-root'))
        with patch.object(access_module, 'write_json') as write:
            restarted.ensure_saved()
        self.assertEqual(stored, restarted.data)
        write.assert_not_called()
        restarted.require_member_pin()
        self.assertFalse(restarted.public_view()['guest_configured'])

    def test_existing_unset_policy_is_not_silently_reset_and_explains_missing_password(self):
        empty = self.new_access().data
        with patch.object(access_module, 'read_json', return_value=empty):
            access = access_module.AccessSettings(Path('unused-offline-root'))
        with patch.object(access_module, 'write_json') as write:
            access.ensure_saved()
        write.assert_not_called()
        with self.assertRaisesRegex(ValueError, '회원용'):
            access.require_member_pin()

    def test_unreadable_policy_is_preserved_and_reports_access_error(self):
        with patch.object(access_module, 'read_json', side_effect=ValueError('corrupt fixture')):
            access = access_module.AccessSettings(Path('unused-offline-root'))
        with patch.object(access_module, 'write_json') as write:
            with self.assertRaises(ValueError):
                access.ensure_saved()
        write.assert_not_called()
        with self.assertRaisesRegex(ValueError, '진단 로그'):
            access.require_member_pin()

    def test_failed_initial_write_does_not_mark_policy_saved(self):
        access = self.new_access()
        before = deepcopy(access.data)
        with patch.object(Path, 'mkdir'), patch.object(access_module, 'write_json', side_effect=OSError('denied')), \
             patch.object(access_module, 'protect_secret', return_value='protected'):
            with self.assertRaises(OSError):
                access.ensure_saved()
        self.assertFalse(access.saved)
        self.assertEqual(before, access.data)

    def test_voice_grant_immediately_refreshes_web_context_and_isolates_optional_errors(self):
        from discord_authority import DiscordAuthority
        node = object.__new__(DiscordAuthority)
        node.client = 'a'*32
        node.lock = threading.RLock()
        node.releasing = False
        node._assert_profile = Mock()
        node.policy = NS(snapshot=lambda: {}, update=Mock())
        node.record = NS(online_members=lambda _: {}, query_members=lambda _: [])
        refresh = Mock()
        relay = NS(observe=Mock(), bridge=NS(refresh_context=refresh))
        node.app = NS(web_service_manager=NS(delegation=relay), _append_debug_log=Mock())
        record = dict(owner=node.client, owner_runtime='b'*32, phase='joining', generation=7)
        node._observe(record, dict(runtime_id='b'*32, online=True))
        refresh.assert_called_once()
        self.assertFalse(node.policy.update.call_args.kwargs['authority_active'])
        refresh.side_effect = OSError('web module unavailable')
        node._observe(record, dict(runtime_id='b'*32, online=True))
        self.assertEqual(node.policy.update.call_count, 2)
        node.app._append_debug_log.assert_called_once()

    def manager(self, role=''):
        return NS(closed=threading.Event(), public_paused=True,
            status=Mock(return_value=dict(enabled=False)), set_enabled=Mock(),
            public=NS(status=Mock(return_value=dict(enabled=False, state='off'))),
            configure_public=Mock(), publication_allowed=Mock(return_value=False),
            authority_retry=Mock(), _log=Mock(),
            delegation=NS(status=Mock(return_value=dict(role=role)), configuration_saved=Mock()))

    def test_successful_credentials_save_arms_web_and_publication(self):
        manager = self.manager()
        WebServiceManager._on_ddns_saved(manager, dict(enabled=True))
        manager.set_enabled.assert_called_once_with(True)
        manager.configure_public.assert_called_once_with(True)
        manager.authority_retry.assert_called_once_with()
        self.assertFalse(manager.public_paused)

    def test_source_setup_prepares_return_through_existing_relay(self):
        manager = self.manager('source')
        WebServiceManager._on_ddns_saved(manager, dict(enabled=True))
        manager.delegation.configuration_saved.assert_called_once_with()
        manager.authority_retry.assert_not_called()

    def test_local_readiness_before_credentials_does_not_skip_settings_import(self):
        manager = self.manager()
        manager.status.return_value = dict(enabled=True)
        manager.public.status.return_value = dict(enabled=True, state='waiting')
        manager.publication_allowed.return_value = True
        WebServiceManager._on_ddns_saved(manager, dict(enabled=True))
        manager.authority_retry.assert_called_once_with()

    def test_standby_needs_no_authority_confirmation_dialog(self):
        manager = self.manager()
        manager.authority_retry.side_effect = ValueError('no voice authority yet')
        message = WebServiceManager._on_ddns_saved(manager, dict(enabled=True))
        self.assertIn('웹실행', message)
        manager.set_enabled.assert_called_once_with(True)

    def test_active_host_is_not_restarted_on_save(self):
        manager = self.manager('host')
        manager.status.return_value = dict(enabled=True)
        manager.public.status.return_value = dict(enabled=True, state='prepared')
        WebServiceManager._on_ddns_saved(manager, dict(enabled=True))
        manager.set_enabled.assert_not_called()
        manager.configure_public.assert_not_called()
        manager.authority_retry.assert_not_called()

    def test_explicitly_disabled_ddns_does_not_arm_hosting(self):
        manager = self.manager()
        WebServiceManager._on_ddns_saved(manager, dict(enabled=False))
        manager.set_enabled.assert_not_called()
        manager.configure_public.assert_not_called()
        self.assertTrue(manager.public_paused)

    def test_callback_runs_only_after_valid_credentials_are_persisted(self):
        callback = Mock(return_value='automatically armed')
        with patch.object(ddns_module, 'read_json', side_effect=FileNotFoundError):
            service = DDNSService(Path('unused-offline-root'), on_saved=callback)
        order = []
        callback.side_effect = lambda saved: order.append('armed') or 'automatically armed'
        with patch.object(ddns_module, 'protect_secret', return_value='protected'), \
             patch.object(ddns_module, 'unprotect_secret', return_value='a'*32), \
             patch.object(ddns_module, 'write_json', side_effect=lambda *args: order.append('saved')), \
             patch.object(ddns_module, 'diagnostic'), patch.object(Path, 'mkdir'):
            message = service._save(dict(enabled=True, domain='example', token='a'*32))
        self.assertEqual(order, ['saved', 'armed'])
        self.assertIn('automatically armed', message)
        self.assertTrue(service.status()['configured'])

    def test_save_failure_never_arms_hosting(self):
        callback = Mock()
        with patch.object(ddns_module, 'read_json', side_effect=FileNotFoundError):
            service = DDNSService(Path('unused-offline-root'), on_saved=callback)
        with patch.object(ddns_module, 'protect_secret', return_value='protected'), \
             patch.object(ddns_module, 'unprotect_secret', return_value='a'*32), \
             patch.object(ddns_module, 'write_json', side_effect=OSError('denied')), \
             patch.object(Path, 'mkdir'):
            with self.assertRaises(OSError):
                service._save(dict(enabled=True, domain='example', token='a'*32))
        callback.assert_not_called()

    def test_new_install_defaults_are_enabled_without_starting_workers(self):
        with patch('web_service_manager.read_json', side_effect=FileNotFoundError), \
             patch('web_direct_hosting.read_json', side_effect=FileNotFoundError), \
             patch.object(ddns_module, 'read_json', side_effect=FileNotFoundError):
            manager = WebServiceManager('unused-offline-root')
        self.assertTrue(manager.status()['enabled'])
        self.assertTrue(manager.public.status()['enabled'])
        self.assertTrue(manager.ddns_status()['enabled'])
        self.assertIsNone(manager.thread)
        self.assertIsNone(manager.process)
        self.assertIsNone(manager.public.thread)
        self.assertFalse(manager.publication_allowed())


if __name__ == '__main__':
    unittest.main()
