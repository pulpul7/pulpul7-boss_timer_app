"""Automatic handover paths with real guards/preflight and an in-memory repo."""
from copy import deepcopy
from datetime import datetime, timezone
from types import MethodType, SimpleNamespace as NS
import threading
import time
import unittest
from unittest.mock import Mock, patch

from discord_authority import AuthorityRecord
from web_delegated_hosting import delegation_info
from web_handover_bridge import WebHandoverBridge, WebAuthorityTransition, WebOperationCancelled
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

    def test_offline_standalone_session_does_not_block_first_publication(self):
        data = self.configured_record()
        data.update(owner='', owner_runtime='', phase='idle')
        data['members'][HOST]['online'] = False
        data['web_standalone'] = dict(protocol=1, id='e'*32, owner=HOST,
                                      runtime=HOST_SESSION, state='active', **SCOPE)
        repo = MemoryRepository(data)
        requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(SOURCE, SOURCE_SESSION, '777', resume_web=True)
        self.assertEqual(requested['phase'], 'joining')
        self.assertNotIn('web_standalone', requested)
        self.assertNotIn('web_delegate', requested)

    def test_probed_replacement_of_remote_session_retires_only_matching_old_mode(self):
        data = self.configured_record()
        data.update(owner='', owner_runtime='', phase='idle')
        data['web_standalone'] = dict(protocol=1, id='e'*32, owner=HOST,
                                      runtime='9'*32, state='active', **SCOPE)
        for ident, retained in (('e'*32, False), ('1'*32, True)):
            with self.subTest(mode_id=ident):
                repo = MemoryRepository(data)
                requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(
                    SOURCE, SOURCE_SESSION, '777', resume_web=True,
                    verified_web_replacement=(ident, HOST, HOST_SESSION))
                self.assertEqual('web_standalone' in requested, retained)

    def test_restarted_local_session_reaches_preparation_without_force(self):
        data = self.configured_record()
        data.update(owner='', owner_runtime='', phase='idle')
        data['web_standalone'] = dict(protocol=1, id='e'*32, owner=SOURCE,
                                      runtime='9'*32, state='active', **SCOPE)
        self.repo = MemoryRepository(data)
        record = AuthorityRecord(self.repo.get, self.repo.put, SCOPE)
        requested = record.request(SOURCE, SOURCE_SESSION, '777', resume_web=True)
        self.assertNotIn('web_standalone', requested)
        active = record.change(requested['request'], SOURCE, phases={'joining'}, phase='active')
        relay, manager, coordinator, policy = self.node('source')
        policy.update(authority_generation=active['generation'], authority_request=active['request'])
        bridge = relay.bridge
        bridge.delegation = relay
        bridge.closed = threading.Event()
        bridge.retry_serial = 0
        bridge.retained_host = None
        bridge.standalone = StandaloneHosting(bridge)
        manager.ddns_status = lambda: dict(configured=False)
        coordinator._ui = lambda callback: callback()
        for name in ('_guard', '_read_owner', '_prepare'):
            setattr(bridge, name, MethodType(getattr(WebHandoverBridge, name), bridge))
        key = (SCOPE['guild'], SOURCE, SOURCE_SESSION, active['generation'])
        with patch('web_schedule_snapshot.collect_snapshot', return_value=public_snapshot('local')):
            bridge._prepare(key, coordinator, active, '18')
        self.assertEqual(bridge.ready_key, key)
        self.assertEqual(manager.pending_snapshot['events'][0]['name'], 'local')
        self.assertTrue(all(call.args[0].startswith('data/handover/') for call in self.repo.get.call_args_list))

    def test_web_worker_queues_handover_without_gui_snapshot_callback(self):
        relay, manager, coordinator, _ = self.node('source')
        bridge = relay.bridge
        bridge.delegation = relay
        bridge.closed = threading.Event()
        bridge.retry_serial = 0
        bridge.standalone = NS(resume_stopped=Mock(return_value=False),
                               suppresses_normal=lambda: False, publish_shared=Mock())
        bridge.recovery = NS(monitor=Mock())
        bridge.wake = NS(wait=lambda _: bridge.closed.set(), clear=Mock())
        coordinator._web_publication_configured = lambda: True
        for _ in range(2):
            bridge.closed.clear()
            WebHandoverBridge._loop(bridge)
        self.assertEqual(len(relay.requests), 1)
        self.assertEqual(relay.requests[0]['operation'], 'return')
        self.repo.get.assert_not_called()
        self.repo.put.assert_not_called()

    def test_first_connection_prepares_tls_during_join_and_publishes_after_join(self):
        self.repo.data.pop('web_delegate')
        self.repo.data.update(phase='joining', receiver=SOURCE, receiver_runtime=SOURCE_SESSION)
        relay, manager, coordinator, policy = self.node('source')
        policy['authority_active'] = False
        bridge = relay.bridge
        bridge.delegation = relay
        bridge.closed = threading.Event()
        bridge.failed_key = bridge.ready_key = bridge.retained_host = None
        bridge.retry_serial = 0
        bridge.standalone = NS(resume_stopped=Mock(return_value=False),
                               suppresses_normal=lambda: False, publish_shared=Mock())
        bridge.recovery = NS(monitor=Mock(), observe=Mock(), fail=Mock())
        bridge.wake = NS(wait=lambda _: bridge.closed.set(), clear=Mock())
        bridge._publish_shared = Mock()
        manager.ddns_status = lambda: dict(configured=False)
        manager.prepare_local_https = Mock()
        coordinator._ui = lambda callback: callback()
        coordinator._web_publication_configured = lambda: True
        for name in ('_guard', '_read_owner', '_prepare', '_keep_retained_host'):
            setattr(bridge, name, MethodType(getattr(WebHandoverBridge, name), bridge))
        manager.set_authority.reset_mock()
        with patch('web_schedule_snapshot.collect_snapshot', return_value=public_snapshot('local')):
            WebHandoverBridge._loop(bridge)
        manager.prepare_local_https.assert_called_once()
        manager.set_authority.assert_not_called()
        bridge._publish_shared.assert_not_called()
        self.assertEqual(manager.pending_snapshot['events'][0]['name'], 'local')
        self.repo.data['phase'] = 'active'
        policy['authority_active'] = True
        bridge.closed.clear()
        WebHandoverBridge._loop(bridge)
        self.assertTrue(manager.set_authority.call_args.kwargs['ready'])
        bridge._publish_shared.assert_called_once()
        bridge.recovery.fail.assert_not_called()

    def test_cancelled_old_worker_does_not_rollback_replacement_with_same_voice_key(self):
        for failure in (WebOperationCancelled('old operation'), OSError('old request failed')):
            with self.subTest(error_type=type(failure).__name__):
                self.repo.data.pop('web_delegate', None)
                relay, manager, coordinator, _ = self.node('source')
                bridge = relay.bridge
                bridge.delegation = relay
                bridge.closed = threading.Event()
                bridge.failed_key = bridge.ready_key = bridge.retained_host = None
                bridge.retry_serial = 0
                bridge.standalone = NS(resume_stopped=Mock(return_value=False),
                                       suppresses_normal=lambda: False, publish_shared=Mock())
                bridge.recovery = NS(monitor=Mock(), observe=Mock(), fail=Mock())
                bridge.wake = NS(wait=lambda _: bridge.closed.set(), clear=Mock())
                bridge._publish_shared = Mock()
                manager.ddns_status = lambda: dict(configured=False)
                coordinator._ui = lambda callback: callback()
                coordinator._web_publication_configured = lambda: True
                for name in ('_guard', '_prepare', '_keep_retained_host'):
                    setattr(bridge, name, MethodType(getattr(WebHandoverBridge, name), bridge))
                def obsolete_read(*_args):
                    bridge.retry_serial += 1  # The delegated return starts a new attempt.
                    raise failure
                bridge._read_owner = obsolete_read
                manager.set_authority.reset_mock()
                WebHandoverBridge._loop(bridge)
                bridge.recovery.fail.assert_not_called()
                manager.set_authority.assert_not_called()
                self.assertIsNone(bridge.failed_key)
                bridge._read_owner = MethodType(WebHandoverBridge._read_owner, bridge)
                bridge.closed.clear()
                with patch('web_schedule_snapshot.collect_snapshot', return_value=public_snapshot('local')):
                    WebHandoverBridge._loop(bridge)
                self.assertTrue(manager.set_authority.call_args.kwargs['ready'])
                bridge.recovery.fail.assert_not_called()

    def test_explicit_retry_verifies_current_state_instead_of_old_thread_local_serial(self):
        self.repo.data.pop('web_delegate')
        relay, manager, coordinator, _ = self.node('source')
        bridge = relay.bridge
        bridge.closed = threading.Event()
        bridge.retry_serial = 2
        bridge.operation_local.serial = 1
        bridge.delegation = relay
        bridge.standalone = NS(suppresses_normal=lambda: False)
        bridge.refresh_context = Mock()
        bridge._keep_retained_host = Mock(return_value=False)
        bridge._guard = MethodType(WebHandoverBridge._guard, bridge)
        WebHandoverBridge.retry(bridge)
        self.assertEqual(bridge.retry_serial, 3)
        self.assertIsNone(bridge.failed_key)

    def test_current_preparation_failure_still_denies_publication_and_recovers(self):
        self.repo.data.pop('web_delegate')
        relay, manager, coordinator, _ = self.node('source')
        bridge = relay.bridge
        bridge.delegation = relay
        bridge.closed = threading.Event()
        bridge.failed_key = bridge.ready_key = None
        bridge.retry_serial = 0
        bridge.standalone = NS(resume_stopped=Mock(return_value=False),
                               suppresses_normal=lambda: False, publish_shared=Mock())
        bridge.recovery = NS(monitor=Mock(), observe=Mock(), fail=Mock())
        bridge.wake = NS(wait=lambda _: bridge.closed.set(), clear=Mock())
        bridge._read_owner = lambda *_: deepcopy(self.repo.data)
        bridge._prepare = Mock(side_effect=ValueError('invalid shared credentials'))
        bridge._keep_retained_host = Mock(return_value=False)
        coordinator._web_publication_configured = lambda: True
        manager.set_authority.reset_mock()
        WebHandoverBridge._loop(bridge)
        self.assertEqual(manager.set_authority.call_args.args[0], ())
        self.assertEqual(bridge.failed_key, bridge.context[0])
        bridge.recovery.fail.assert_called_once()

    def test_reuniting_existing_web_host_does_not_pause_publication_while_joining(self):
        self.repo.data.pop('web_delegate')
        self.repo.data.update(phase='joining', receiver=SOURCE, receiver_runtime=SOURCE_SESSION)
        relay, manager, coordinator, policy = self.node('source')
        policy['authority_active'] = False
        bridge = relay.bridge
        bridge.delegation = relay
        bridge.closed = threading.Event()
        bridge.failed_key = bridge.ready_key = None
        bridge.retry_serial = 0
        bridge._keep_retained_host = Mock(return_value=True)
        bridge._guard = Mock()
        bridge._read_owner = lambda *_: deepcopy(self.repo.data)
        bridge._prepare = Mock()
        bridge.standalone = NS(resume_stopped=Mock(return_value=False),
                               suppresses_normal=lambda: False, publish_shared=Mock())
        bridge.recovery = NS(monitor=Mock(), observe=Mock(), fail=Mock())
        bridge.wake = NS(wait=lambda _: bridge.closed.set(), clear=Mock())
        manager.prepare_local_https = Mock()
        coordinator._web_publication_configured = lambda: True
        manager.set_authority.reset_mock()
        WebHandoverBridge._loop(bridge)
        bridge._prepare.assert_not_called()
        manager.prepare_local_https.assert_not_called()
        manager.set_authority.assert_not_called()

    def test_new_host_assignment_seen_before_discord_hint_keeps_public_lease(self):
        data = self.configured_record()
        self.repo = MemoryRepository(data)
        relay, manager, coordinator, policy = self.node('host')
        policy.update(authority_active=True, standby=False, authority_generation=data['generation'])
        bridge = relay.bridge
        bridge.delegation = relay
        bridge.closed = threading.Event()
        bridge.retained_host = None
        bridge.retry_serial = 0
        bridge.standalone = StandaloneHosting(bridge)
        key = (SCOPE['guild'], HOST, HOST_SESSION, data['generation'])
        bridge.context = (key, coordinator, '18')
        for name in ('_guard', '_read_owner'):
            setattr(bridge, name, MethodType(getattr(WebHandoverBridge, name), bridge))
        requested = coordinator.record.request(SOURCE, SOURCE_SESSION, '777', resume_web=True)
        self.assertEqual(requested['web_delegate']['host'], HOST)
        manager.set_authority.reset_mock()
        with self.assertRaises(WebAuthorityTransition):
            bridge._read_owner(key, coordinator)
        self.assertEqual(relay.role, 'host')
        self.assertTrue(all(call.args[0] for call in manager.set_authority.call_args_list))
        self.assertTrue(manager.set_authority.call_args.kwargs['ready'])

    def test_local_web_preparation_ignores_all_canonical_upload_states(self):
        for state in ('failed', 'pending', 'ready', None):
            with self.subTest(upload_state=state):
                relay, manager, coordinator, _ = self.node('source')
                bridge = relay.bridge
                bridge.retained_host = None
                bridge.retry_serial = 0
                bridge._guard = Mock()
                bridge._read_owner = Mock()
                bridge._shared_path = MethodType(WebHandoverBridge._shared_path, bridge)
                manager.ddns_status = lambda: dict(configured=True)
                coordinator._ui = lambda callback: callback()
                data = deepcopy(self.repo.data)
                data['previous_owner'] = [HOST, HOST_SESSION, 1, 'e'*32]
                if state is not None:
                    data['web_transfer'] = dict(request=data['request'], state=state,
                        failure_reason='server=.008 PC=.007', entry=dict(schedule='data/schedules/9.json'))
                data_get = self.repo.get.call_count
                with patch('web_schedule_snapshot.collect_snapshot', return_value=public_snapshot('local')):
                    WebHandoverBridge._prepare(bridge, bridge.context[0], coordinator, data, '18')
                self.assertEqual(bridge.ready_key, bridge.context[0])
                self.assertEqual(manager.pending_snapshot['events'][0]['name'], 'local')
                self.assertEqual(self.repo.get.call_count, data_get + 1)
                self.assertTrue(self.repo.get.call_args.args[0].startswith('data/web/'))
                bridge._sync.assert_not_called()

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

    def test_handover_standby_blocks_publication_without_saving_off(self):
        manager = NS(public_paused=False, authority_ready=True, authority_message='',
            settings=dict(enabled=True), lock=threading.RLock(), authority_key=('owned',),
            authority_until=time.monotonic()+30, _flush_authority=Mock(), set_enabled=Mock(), _log=Mock(),
            ddns=NS(set_active=Mock()), https=NS(set_active=Mock()),
            public=NS(set_active=Mock(), status=lambda: dict(enabled=True)))
        WebServiceManager.emergency_stop(manager, standby=True)
        manager.set_enabled.assert_not_called()
        self.assertTrue(manager.settings['enabled'])
        self.assertFalse(WebServiceManager.has_web_authority(manager))
        manager.ddns.set_active.assert_called_once_with(False)
        manager.public.set_active.assert_called_once_with(False)
        node = object.__new__(DiscordAuthority)
        manager.status = lambda: dict(enabled=True)
        manager.ddns_status = lambda: dict(configured=True)
        node.app = NS(web_service_manager=manager)
        self.assertTrue(node._web_publication_configured())

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

    def test_failed_canonical_upload_still_preflights_releases_and_activates_local_web(self):
        data = self.configured_record()
        self.repo = MemoryRepository(data)
        record = AuthorityRecord(self.repo.get, self.repo.put, SCOPE)
        requested = record.request(SOURCE, SOURCE_SESSION, '777', resume_web=True)
        record.acknowledge_stop(requested['request'], HOST, HOST_SESSION)
        record.accept_stopped(requested['request'], SOURCE, SOURCE_SESSION)
        active = deepcopy(record.read()[0])  # Voice joining: web preparation must already start.
        active['web_transfer'] = dict(request=active['request'], state='failed', content_hash='verified',
                                     failure_reason='server=.008 PC=.007',
                                     entry=dict(id='9', schedule='data/schedules/9.json'), source_season='18')
        self.repo.data = active
        self.repo.files['data/schedules/9.json'] = dict(kind='schedule', dataVersion='2026.10.12.002', payload=dict(boss='new'))
        source, manager, coordinator, policy = self.node('source')
        policy.update(authority_active=False, authority_generation=active['generation'], authority_request=active['request'])
        bridge = source.bridge
        bridge.context = ((SCOPE['guild'], SOURCE, SOURCE_SESSION, active['generation']), coordinator, '18')
        bridge.closed = threading.Event()
        bridge.delegation = source
        bridge.retained_host = bridge.failed_key = bridge.ready_key = None
        bridge.retry_serial = 0
        bridge.transfer_state = bridge.shared_signature = None
        bridge.next_shared_check = 0
        bridge.standalone = StandaloneHosting(bridge)
        bridge.recovery = WebReturnRecovery(bridge)
        for name in ('_guard', '_read_owner', '_prepare', '_shared_path', '_keep_retained_host',
                     'refresh_context', 'retry', 'observe_transfer'):
            setattr(bridge, name, MethodType(getattr(WebHandoverBridge, name), bridge))
        manager.ddns_status = lambda: dict(configured=True)
        manager.https_status = lambda: dict(https_state='running', domain='example.invalid', port=8766)
        manager.prepare_local_https = Mock()
        app = source.app
        app.local_schedule = dict(boss='old')
        app._apply_loaded_schedule_shared_payload = Mock()
        app._save_schedule_state = Mock()
        coordinator._ui = lambda callback: callback()
        source.take_over_when_configured(True)
        with patch('web_direct_hosting.check_local_https'), \
             patch('web_schedule_snapshot.collect_snapshot', return_value=public_snapshot('old')):
            source._step()
        self.assertEqual(self.repo.data['phase'], 'joining')
        self.assertEqual(self.repo.data['web_delegate']['state'], 'active')
        source._step()  # Prepared once, existing web remains available while voice joins.
        self.assertEqual(manager.prepare_local_https.call_count, 2)  # Renew the local TLS lease only.
        manager.offer_snapshot.assert_called_once()
        active = record.change(active['request'], SOURCE, phases={'joining'}, phase='active')
        policy['authority_active'] = True
        source.observe(active, coordinator)
        source._step()
        self.assertEqual(self.repo.data['web_delegate']['state'], 'releasing')
        self.assertEqual(app.local_schedule, dict(boss='old'))
        self.assertEqual(manager.pending_snapshot['events'][0]['name'], 'old')
        self.assertEqual(manager.prepare_local_https.call_count, 2)
        host, host_manager, host_coordinator, _ = self.node('host')
        host_manager.ddns_status = lambda: dict(configured=False)
        host_manager.ddns = NS(status=lambda: dict(busy=False))
        host_manager.emergency_stop = Mock()
        host_manager.public.status.return_value.update(state='off', busy=False)
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
        app._apply_loaded_schedule_shared_payload.assert_not_called()
        app._save_schedule_state.assert_not_called()
        self.assertTrue(all(not call.args[0].startswith('data/schedules/')
                            for call in self.repo.get.call_args_list))


if __name__ == '__main__':
    unittest.main()
