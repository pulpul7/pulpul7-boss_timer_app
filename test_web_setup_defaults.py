"""Offline setup checks: no workers, sockets, Windows crypto or user-data writes."""
from pathlib import Path
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import Mock, patch

from web_service_manager import WebServiceManager
from web_ddns_service import DDNSService
import web_ddns_service as ddns_module


class SetupTests(unittest.TestCase):
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
