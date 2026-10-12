"""Offline web takeover checks. No GUI, sockets, router, or user-data access."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
import threading
import time
import unittest
from unittest.mock import Mock, patch

from discord_authority import AuthorityRecord
from web_handover_bridge import WebHandoverBridge
from web_return_recovery import WebReturnRecovery, pending_return, recovery_info
import test_web_delegated_hosting as fixtures
from test_web_delegated_hosting import (
    MemoryRepository, initial_record, public_snapshot,
    HOST, SOURCE, HOST_SESSION, SOURCE_SESSION, SCOPE,
)
import web_return_recovery as recovery_module


class ReturnTests(unittest.TestCase):
    setUp = fixtures.DelegatedTests.setUp
    node = fixtures.DelegatedTests.node

    def recovery_node(self, role='source'):
        relay, manager, coordinator, policy = self.node(role)
        manager.authority_key = relay.bridge.context[0]
        manager.authority_ready = False
        manager.root = Path('unused-offline-web-root')
        manager.emergency_stop = Mock()
        manager._flush_authority = Mock()
        manager.retry_public = Mock()
        manager.ddns = NS(active=True, status=Mock(return_value=dict(busy=False, state='ready',
            last_updated=datetime.fromtimestamp(time.time()+1, timezone.utc).isoformat())))
        manager.ddns_status = Mock(return_value=dict(configured=False))
        manager.https_status = Mock(return_value=dict(https_state='running', domain='example.duckdns.org', port=8766))
        manager.prepare_local_https = Mock()
        bridge = relay.bridge
        bridge.delegation = relay
        bridge.closed = threading.Event()
        bridge.refresh_context = Mock()
        bridge.retry_serial = 0
        bridge._read_owner = Mock(side_effect=lambda *_: deepcopy(self.repo.data))
        bridge._prepare = Mock()
        recovery = bridge.recovery = WebReturnRecovery(bridge)
        for item in self.repo.data['members'].values():
            item['web_recovery_protocol'] = 1
        relay.observe(self.repo.data, coordinator)
        return relay, manager, coordinator, policy, recovery

    def begin_trial(self):
        relay, manager, coordinator, policy, recovery = self.recovery_node()
        self.repo.data['web_return_pending'] = pending_return(relay.info)
        self.repo.data.pop('web_delegate')
        relay.observe(self.repo.data, coordinator)
        return relay, manager, coordinator, policy, recovery

    def test_configured_receiver_keeps_host_before_preflight(self):
        data = initial_record()
        for item in data['members'].values():
            item.update(web_recovery_protocol=1, web_public_available=True)
        repo = MemoryRepository(data)
        requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(SOURCE, SOURCE_SESSION, '777')
        self.assertEqual(requested['web_delegate']['host'], HOST)
        self.assertFalse(requested['web_delegate']['keep_host'])

    def test_reclaim_after_release_needs_no_previous_manager(self):
        data = initial_record()
        data.update(owner='', owner_runtime='', phase='idle')
        repo = MemoryRepository(data)
        requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(
            HOST, HOST_SESSION, '777', preserve_web=True)
        self.assertEqual(requested['phase'], 'joining')
        self.assertEqual(requested['owner'], HOST)
        self.assertNotIn('web_delegate', requested)

    def test_settings_become_ready_queues_one_automatic_return(self):
        relay, _, _, _, _ = self.recovery_node()
        relay.take_over_when_configured(False)
        self.assertEqual(len(relay.requests), 0)
        for _ in range(20):
            relay.take_over_when_configured(True)
        self.assertEqual(len(relay.requests), 1)

    def test_explicit_separate_host_never_auto_returns(self):
        relay, _, coordinator, _, _ = self.recovery_node()
        self.repo.data['web_delegate']['keep_host'] = True
        relay.observe(self.repo.data, coordinator)
        relay.take_over_when_configured(True)
        self.assertEqual(len(relay.requests), 0)
        relay.return_web_here()  # Explicit UI/Discord request is still allowed.
        self.assertEqual(len(relay.requests), 1)

    def test_saved_credentials_retry_preflight_but_respect_explicit_split(self):
        relay, _, coordinator, _, _ = self.recovery_node()
        relay.auto_return_started = True
        relay.configuration_saved()
        self.assertEqual(len(relay.requests), 1)
        relay.requests.clear()
        self.repo.data['web_delegate']['keep_host'] = True
        relay.observe(self.repo.data, coordinator)
        relay.configuration_saved()
        self.assertEqual(len(relay.requests), 0)

    def test_failed_preflight_does_not_stop_or_release_host(self):
        relay, manager, coordinator, _, recovery = self.recovery_node()
        relay.bridge._prepare.side_effect = ValueError('upload failed')
        original = deepcopy(self.repo.data)
        relay._return_request(deepcopy(relay.info))
        self.assertEqual(self.repo.data, original)
        manager.emergency_stop.assert_not_called()
        self.assertEqual(recovery.stage, 'preflight_failed')
        self.assertEqual(self.repo.writes, 0)

    def test_preflight_checks_local_tls_before_release(self):
        relay, manager, coordinator, _, recovery = self.recovery_node()
        with patch('web_direct_hosting.check_local_https') as probe:
            relay._return_request(deepcopy(relay.info))
        probe.assert_called_once_with('example.duckdns.org', 8766)
        manager.prepare_local_https.assert_called_once()
        manager.emergency_stop.assert_not_called()
        self.assertEqual(self.repo.data['web_delegate']['state'], 'releasing')

    def test_return_saves_backup_and_event_for_failed_first_delivery(self):
        relay, manager, coordinator, _, recovery = self.recovery_node()
        self.repo.data['web_delegate']['state'] = 'stopped'
        relay.observe(self.repo.data, coordinator)
        coordinator._notify.side_effect = OSError('Discord unavailable')
        relay._finish_return(deepcopy(relay.info))
        self.assertNotIn('web_delegate', self.repo.data)
        self.assertEqual(self.repo.data['web_return_pending']['backup']['host'], HOST)
        self.assertIsNotNone(relay.notice)
        relay.bridge.retry.assert_called_once()

    def test_connecting_and_stale_error_do_not_trigger_fallback(self):
        relay, manager, coordinator, _, recovery = self.begin_trial()
        manager.public.status.return_value = dict(state='waiting', enabled=True)
        for _ in range(20):
            recovery.monitor(relay.bridge.context[0], coordinator)
        manager.public.status.return_value['state'] = 'error'
        recovery.monitor(relay.bridge.context[0], coordinator)  # Not activated yet.
        manager.emergency_stop.assert_not_called()
        self.repo.get.assert_not_called()
        self.repo.put.assert_not_called()

    def test_public_failure_restores_old_web_without_reverting_voice(self):
        relay, manager, coordinator, _, recovery = self.begin_trial()
        manager.authority_ready = True
        manager.public.status.return_value = dict(state='error', enabled=True)
        order = []
        manager.emergency_stop.side_effect = lambda: order.append('stop')
        manager._flush_authority.side_effect = lambda **_: order.append('deny')
        manager.ddns.status.side_effect = lambda: order.append('drain') or dict(busy=False)
        with patch.object(recovery_module, 'read_json', return_value=dict(enabled=False)):
            recovery.monitor(relay.bridge.context[0], coordinator)
        self.assertEqual(order[-3:], ['stop', 'deny', 'drain'])
        info = self.repo.data['web_delegate']
        self.assertEqual(info['host'], HOST)
        self.assertTrue(info['restore_after_failure'])
        self.assertTrue(info['keep_host'])
        self.assertEqual(self.repo.data['owner'], SOURCE)
        self.assertEqual(self.repo.data['owner_runtime'], SOURCE_SESSION)
        self.assertEqual(self.repo.data['phase'], 'active')
        self.assertNotIn('web_return_pending', self.repo.data)
        self.assertIsNotNone(relay.notice)
        self.assertTrue(relay.force)

    def test_restore_is_not_acknowledged_while_ddns_is_still_busy(self):
        relay, manager, coordinator, _, recovery = self.begin_trial()
        manager.ddns.status.return_value = dict(busy=True)
        with patch.object(recovery_module, 'read_json', return_value=dict(enabled=False)), \
             patch.object(manager.closed, 'wait', return_value=True):
            recovery.fail(relay.bridge.context[0], coordinator, ValueError('failed'))
        self.assertNotIn('web_delegate', self.repo.data)
        self.assertIn('web_return_pending', self.repo.data)
        self.repo.put.assert_not_called()

    def test_late_failure_cannot_stop_a_newer_voice_session(self):
        relay, manager, coordinator, policy, recovery = self.begin_trial()
        key = relay.bridge.context[0]
        policy['authority_generation'] += 1
        recovery.fail(key, coordinator, ValueError('late'))
        manager.emergency_stop.assert_not_called()
        self.repo.put.assert_not_called()

    def test_newer_durable_request_cannot_be_overwritten(self):
        relay, manager, coordinator, _, recovery = self.begin_trial()
        key = relay.bridge.context[0]
        self.repo.data.update(request='e' * 32, generation=3)
        recovery.fail(key, coordinator, ValueError('late'))
        manager.emergency_stop.assert_not_called()
        self.repo.put.assert_not_called()

    def test_trial_success_clears_backup_without_restart(self):
        relay, manager, coordinator, _, recovery = self.begin_trial()
        manager.authority_ready = True
        recovery.monitor(relay.bridge.context[0], coordinator)
        self.assertNotIn('web_return_pending', self.repo.data)
        self.assertNotIn('web_delegate', self.repo.data)
        manager.emergency_stop.assert_not_called()
        self.assertEqual(recovery.stage, 'completed')
        self.assertIsNotNone(relay.notice)

    def test_old_ddns_success_does_not_complete_new_trial(self):
        relay, manager, coordinator, _, recovery = self.begin_trial()
        manager.authority_ready = True
        manager.ddns.status.return_value['last_updated'] = '2026-01-01T00:00:00+00:00'
        recovery.monitor(relay.bridge.context[0], coordinator)
        self.assertIn('web_return_pending', self.repo.data)
        self.repo.put.assert_not_called()
        manager.emergency_stop.assert_not_called()

    def test_ddns_failure_restores_previous_host(self):
        relay, manager, coordinator, _, recovery = self.begin_trial()
        manager.authority_ready = True
        manager.ddns.status.return_value.update(state='error')
        with patch.object(recovery_module, 'read_json', return_value=dict(enabled=False)):
            recovery.monitor(relay.bridge.context[0], coordinator)
        self.assertEqual(self.repo.data['web_delegate']['host'], HOST)
        self.assertEqual(self.repo.data['owner'], SOURCE)

    def test_restored_host_restarts_once_without_voice_authority(self):
        relay, manager, coordinator, policy, _ = self.recovery_node('host')
        self.repo.data['web_delegate']['restore_after_failure'] = True
        relay.observe(self.repo.data, coordinator)
        for _ in range(5):
            relay._step()
        manager.retry_public.assert_called_once_with()
        self.assertFalse(policy['authority_active'])

    def test_malformed_pending_record_is_ignored(self):
        relay, _, _, _, recovery = self.begin_trial()
        key = relay.bridge.context[0]
        for value in ('bad', {}, dict(schema=1, id='e'*32, deadline=float('nan'))):
            data = dict(self.repo.data, web_return_pending=value)
            recovery.observe(data)
            self.assertIsNone(recovery_info(data, key))

    def test_ui_snapshot_guard_captures_current_delegation(self):
        relay, manager, coordinator, _, _ = self.recovery_node()
        bridge = relay.bridge
        bridge.operation_local.delegated_info = relay.info
        bridge.retained_host = None
        bridge._guard = Mock()
        # Mimic Tk's distinct thread-local state when it runs the closure.
        def ui(callback):
            saved = bridge.operation_local.delegated_info
            bridge.operation_local.delegated_info = None
            try:
                return callback()
            finally:
                bridge.operation_local.delegated_info = saved
        coordinator._ui = ui
        with patch('web_schedule_snapshot.collect_snapshot', return_value=public_snapshot()):
            WebHandoverBridge._prepare(bridge, *bridge.context[:2], self.repo.data, '18')
        self.assertEqual(bridge._guard.call_args.kwargs['delegated_info'], relay.info)

    def test_successful_preparation_replaces_retained_web_snapshot_on_tk(self):
        relay, manager, coordinator, _, _ = self.recovery_node()
        bridge = relay.bridge
        bridge._guard = Mock()
        bridge.retained_host = ('f'*32, coordinator, relay.info, public_snapshot('old'))
        manager.delegated_snapshot_only = True
        coordinator._ui = lambda callback: callback()
        fresh = public_snapshot('fresh')
        with patch('web_schedule_snapshot.collect_snapshot', return_value=fresh):
            WebHandoverBridge._prepare(bridge, *bridge.context[:2], self.repo.data, '18')
        self.assertEqual(manager.offer_snapshot.call_args.args[0], fresh)
        self.assertEqual(manager.offer_snapshot.call_args.kwargs['origin'], 'delegated')
        self.assertIsNone(bridge.retained_host)
        self.assertFalse(manager.delegated_snapshot_only)


if __name__ == '__main__':
    unittest.main()
