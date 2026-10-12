"""Automatic handover paths with real guards/sync/preflight and an in-memory repo."""
from copy import deepcopy
from datetime import datetime, timezone
from types import MethodType, SimpleNamespace as NS
import threading
import time
import unittest
from unittest.mock import Mock, patch

from discord_authority import AuthorityRecord
from web_delegated_hosting import delegation_info
from web_handover_bridge import WebHandoverBridge
from web_return_recovery import WebReturnRecovery
from web_standalone_hosting import StandaloneHosting
from web_service_manager import WebServiceManager
from discord_authority import DiscordAuthority
import test_web_delegated_hosting as fixtures
from test_web_delegated_hosting import (
    HOST, HOST_SESSION, SOURCE, SOURCE_SESSION, SCOPE, MemoryRepository, initial_record, public_snapshot,
)


class AutoHandoverTests(unittest.TestCase):
    setUp = fixtures.DelegatedTests.setUp
    node = fixtures.DelegatedTests.node

    def configured_record(self):
        data = initial_record()
        for member in data['members'].values():
            member.update(web_public_available=True, web_recovery_protocol=1, web_standalone_protocol=1)
        return data

    def test_new_configured_handover_resets_previous_failure_hold(self):
        data = deepcopy(self.repo.data)
        data['web_delegate']['keep_host'] = True
        data['web_delegate']['restore_after_failure'] = True
        repo = MemoryRepository(data)
        requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(SOURCE, SOURCE_SESSION, '777', resume_web=True)
        self.assertFalse(requested['web_delegate']['keep_host'])
        self.assertEqual(requested['web_delegate']['host'], HOST)

    def test_new_explicit_split_and_unconfigured_receiver_keep_previous_host(self):
        for options in (dict(preserve_web=True), dict(automatic_preserve_web=True)):
            data = deepcopy(self.repo.data)
            data['web_delegate']['keep_host'] = True
            repo = MemoryRepository(data)
            requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(SOURCE, SOURCE_SESSION, '777', **options)
            self.assertTrue(requested['web_delegate']['keep_host'])

    def test_stopped_manual_mode_does_not_block_configured_normal_handover(self):
        data = self.configured_record()
        data['web_standalone'] = dict(protocol=1, id='e'*32, owner=HOST, runtime=HOST_SESSION, state='stopped', **SCOPE)
        repo = MemoryRepository(data)
        requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(SOURCE, SOURCE_SESSION, '777', resume_web=True)
        self.assertNotIn('web_standalone', requested)
        self.assertIn('web_standalone_previous', requested)
        self.assertNotIn('web_delegate', requested)

    def test_existing_standalone_host_takes_voice_without_stopping_its_web(self):
        data = self.configured_record()
        data.update(owner=SOURCE, owner_runtime=SOURCE_SESSION)
        data['web_standalone'] = dict(protocol=1, id='e'*32, owner=HOST,
                                      runtime=HOST_SESSION, state='active', **SCOPE)
        self.repo = MemoryRepository(data)
        relay, manager, coordinator, _ = self.node('host')
        bridge = relay.bridge
        bridge.delegation = relay
        bridge.closed = threading.Event()
        bridge.retained_host = None
        bridge.standalone = StandaloneHosting(bridge)
        for name in ('_keep_retained_host', 'retain_web_host'):
            setattr(bridge, name, MethodType(getattr(WebHandoverBridge, name), bridge))
        relay.observe(data, coordinator)
        requested = coordinator.record.request(HOST, HOST_SESSION, '777', resume_web=True)
        self.assertNotIn('web_delegate', requested)
        manager.set_authority.reset_mock()
        relay.observe(requested, coordinator)
        self.assertIsNotNone(bridge.retained_host)
        self.assertTrue(manager.delegated_snapshot_only)
        self.assertTrue(all(call.args[0] for call in manager.set_authority.call_args_list))

    def test_active_standalone_host_is_retained_until_receiver_preflight(self):
        data = self.configured_record()
        # The web host can differ from the voice owner.
        data.update(owner=SOURCE, owner_runtime=SOURCE_SESSION)
        third, third_session = '1'*32, '2'*32
        data['members'][third] = dict(deepcopy(data['members'][SOURCE]), runtime=third_session)
        data['web_standalone'] = dict(protocol=1, id='e'*32, owner=HOST, runtime=HOST_SESSION, state='active', **SCOPE)
        repo = MemoryRepository(data)
        requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(third, third_session, '777', resume_web=True)
        self.assertNotIn('web_standalone', requested)
        self.assertEqual(delegation_info(requested)['host'], HOST)
        self.assertFalse(requested['web_delegate']['keep_host'])
        self.assertEqual(requested['owner'], SOURCE)  # Voice still waits for stop proof.

    def test_automatic_voice_reconnect_keeps_standalone_mode(self):
        data = self.configured_record()
        data['web_standalone'] = dict(protocol=1, id='e'*32, owner=SOURCE, runtime=SOURCE_SESSION, state='active', **SCOPE)
        repo = MemoryRepository(data)
        requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(HOST, HOST_SESSION, '777',
            expected_owner=(HOST_SESSION, 1), resume_web=True)
        self.assertEqual(requested['web_standalone'], data['web_standalone'])

    def test_unknown_host_session_never_revokes_working_standalone(self):
        data = self.configured_record()
        data['web_standalone'] = dict(protocol=1, id='e'*32, owner=HOST, runtime='9'*32, state='active', **SCOPE)
        repo = MemoryRepository(data)
        requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(SOURCE, SOURCE_SESSION, '777', resume_web=True)
        self.assertEqual(requested['web_standalone'], data['web_standalone'])

    def test_voice_join_failure_restores_existing_standalone_web(self):
        data = self.configured_record()
        manual = dict(protocol=1, id='e'*32, owner=HOST, runtime=HOST_SESSION,
                      state='active', **SCOPE)
        data['web_standalone'] = deepcopy(manual)
        repo = MemoryRepository(data)
        record = AuthorityRecord(repo.get, repo.put, SCOPE)
        requested = record.request(SOURCE, SOURCE_SESSION, '777', resume_web=True)
        record.acknowledge_stop(requested['request'], HOST, HOST_SESSION)
        record.accept_stopped(requested['request'], SOURCE, SOURCE_SESSION)
        node = object.__new__(DiscordAuthority)
        node.client, node.runtime = SOURCE, SOURCE_SESSION
        node.lock, node.record = threading.RLock(), record
        node.policy = NS(block_request=Mock())
        node._wait_local_stop = node._notify = Mock()
        node._release(request_id=requested['request'], failure='voice join failed')
        self.assertEqual(repo.data['web_standalone'], manual)
        self.assertNotIn('web_standalone_previous', repo.data)
        self.assertNotIn('web_delegate', repo.data)
        self.assertEqual(repo.data['owner'], '')

    def test_checked_service_after_logout_clears_hidden_pause_and_allows_auto_handover(self):
        manager = NS(closed=threading.Event(), settings=dict(enabled=False, port=8765),
            root=Mock(), settings_path='in-memory-settings', public_paused=True,
            page_updater=NS(start=Mock()), _request=Mock())
        with patch('web_service_manager.write_json'):
            WebServiceManager.set_enabled(manager, True)
        self.assertFalse(manager.public_paused)
        node = object.__new__(DiscordAuthority)
        manager.status = lambda: dict(enabled=True)
        manager.ddns_status = lambda: dict(configured=True)
        manager.public = NS(status=lambda: dict(enabled=True))
        node.app = NS(web_service_manager=manager)
        self.assertTrue(node._web_publication_configured())

    def test_checked_public_after_logout_clears_hidden_pause(self):
        manager = NS(public_paused=True, public_preference_touched=False,
                     public=NS(configure=Mock()))
        WebServiceManager.configure_public(manager, True)
        self.assertFalse(manager.public_paused)
        manager.public.configure.assert_called_once_with(True)

    def test_switching_off_does_not_clear_existing_stop(self):
        manager = NS(public_paused=True, public_preference_touched=False,
                     public=NS(configure=Mock()))
        WebServiceManager.configure_public(manager, False)
        self.assertTrue(manager.public_paused)

    def test_service_on_resumes_stopped_mode_without_rejoining_voice(self):
        from test_web_standalone_hosting import StandaloneTests
        from discord_connection_policy import ConnectionPolicy, CONTROL_PROTOCOL
        fixture = StandaloneTests()
        manual, manager, coordinator, public = fixture.node(owner=SOURCE)
        fixture.repo.data['web_standalone'] = dict(protocol=1, id='e'*32, owner=HOST,
                                                  runtime=HOST_SESSION, state='stopped', **SCOPE)
        manual.observe(fixture.repo.data, coordinator)
        manager.status.return_value['enabled'] = True
        public['enabled'] = True
        state = dict(ConnectionPolicy._empty(), protocol=CONTROL_PROTOCOL, runtime_id=SOURCE_SESSION,
                     authority_active=True, standby=False, authority_needs_sync=False,
                     authority_until=time.monotonic()+100)
        coordinator.policy.snapshot = lambda: deepcopy(state)
        key = (SCOPE['guild'], SOURCE, SOURCE_SESSION, fixture.repo.data['generation'])
        before = AuthorityRecord.owner_stamp(fixture.repo.data)
        self.assertFalse(manual.resume_stopped(key, coordinator))
        manager.public_resume_serial = 1  # A new ON action after the stop record.
        self.assertTrue(manual.resume_stopped(key, coordinator))
        self.assertNotIn('web_standalone', fixture.repo.data)
        self.assertEqual(AuthorityRecord.owner_stamp(fixture.repo.data), before)
        self.assertFalse(manual.resume_stopped(key, coordinator))

    def test_actual_sync_and_preflight_release_then_activate_without_force(self):
        data = self.configured_record()
        self.repo = MemoryRepository(data)
        record = AuthorityRecord(self.repo.get, self.repo.put, SCOPE)
        requested = record.request(SOURCE, SOURCE_SESSION, '777', resume_web=True)
        record.acknowledge_stop(requested['request'], HOST, HOST_SESSION)
        record.accept_stopped(requested['request'], SOURCE, SOURCE_SESSION)
        active = record.change(requested['request'], SOURCE, phases={'joining'}, phase='active')
        active['web_transfer'] = dict(request=active['request'], state='ready', content_hash='verified',
                                     entry=dict(id='9', schedule='data/schedules/9.json'), source_season='18')
        self.repo.data = active
        self.repo.files['data/schedules/9.json'] = dict(kind='schedule', dataVersion='2026.10.12.002', payload=dict(boss='new'))
        source, manager, coordinator, policy = self.node('source')
        policy.update(authority_generation=active['generation'], authority_request=active['request'])
        bridge = source.bridge
        bridge.context = ((SCOPE['guild'], SOURCE, SOURCE_SESSION, active['generation']), coordinator, '18')
        bridge.closed = threading.Event()
        bridge.delegation = source
        bridge.retained_host = bridge.failed_key = bridge.ready_key = None
        bridge.retry_serial = 0
        bridge.transfer_state = bridge.shared_signature = None
        bridge.next_shared_check = 0
        bridge.synced_requests = set()
        bridge.standalone = StandaloneHosting(bridge)
        bridge.recovery = WebReturnRecovery(bridge)
        for name in ('_guard', '_read_owner', '_sync', '_prepare', '_shared_path', '_keep_retained_host',
                     'refresh_context', 'retry', 'observe_transfer'):
            setattr(bridge, name, MethodType(getattr(WebHandoverBridge, name), bridge))
        manager.ddns_status = lambda: dict(configured=True)
        manager.https_status = lambda: dict(https_state='running', domain='example.invalid', port=8766)
        manager.prepare_local_https = Mock()
        app = source.app
        app.schedule_last_import_meta = {}
        app.local_schedule = dict(boss='old')
        app._create_schedule_restore_snapshot = lambda: deepcopy(app.local_schedule)
        app._create_schedule_full_state_snapshot = app._create_schedule_restore_snapshot
        app._get_schedule_restore_snapshot_signature = lambda value: repr(value)
        app._get_schedule_state_storage_path = lambda: 'in-memory-profile'
        app._get_github_schedule_content_hash = lambda _: 'verified'
        app._unwrap_github_schedule_payload = lambda value: deepcopy(value['payload'])
        def apply(payload, **_):
            app.local_schedule = deepcopy(payload)
            return True
        app._apply_loaded_schedule_shared_payload = Mock(side_effect=apply)
        app._append_current_schedule_restore_history_marker = Mock()
        app._save_schedule_state = app._save_github_local_payload_cache = Mock()
        app._restore_schedule_full_state_snapshot = lambda value: setattr(app, 'local_schedule', value)
        coordinator._ui = lambda callback: callback()
        source.take_over_when_configured(True)
        with patch('web_direct_hosting.check_local_https'), \
             patch('web_schedule_snapshot.collect_snapshot', return_value=public_snapshot('new')):
            source._step()
        self.assertEqual(self.repo.data['web_delegate']['state'], 'releasing')
        self.assertEqual(app.local_schedule, dict(boss='new'))
        self.assertIn(active['request'], bridge.synced_requests)
        manager.prepare_local_https.assert_called_once()
        host, host_manager, host_coordinator, _ = self.node('host')
        host_manager.ddns_status = lambda: dict(configured=False)
        host_manager.ddns = NS(status=lambda: dict(busy=False))
        host_manager.emergency_stop = Mock()
        host_manager._flush_authority = Mock()
        from pathlib import Path
        host_manager.root = Path('unused-offline-web-root')
        with patch('web_delegated_hosting.read_json', return_value=dict(enabled=False)):
            host._release_host(deepcopy(host.info))
        source.observe(self.repo.data, coordinator)
        source._finish_return(deepcopy(source.info))
        self.assertNotIn('web_delegate', self.repo.data)
        self.assertIn('web_return_pending', self.repo.data)
        self.assertEqual(self.repo.data['owner'], SOURCE)
        self.assertEqual(app._apply_loaded_schedule_shared_payload.call_count, 1)


if __name__ == '__main__':
    unittest.main()
