"""Offline split-hosting safety checks; no GUI, network, or user files."""
from copy import deepcopy
from types import SimpleNamespace as NS
from pathlib import Path
from types import MethodType
import threading
import unittest
from unittest.mock import Mock, patch

from discord_authority import AuthorityRecord, DiscordAuthority
from discord_connection_policy import ConnectionPolicy, CONTROL_PROTOCOL
import web_delegated_hosting as relay_module
import discord_authority as authority_module
from web_delegated_hosting import DelegatedHosting, build_delegation, build_automatic_delegation, delegation_info, content_hash, edit_hash
from web_service_manager import WebServiceManager
from web_handover_bridge import WebHandoverBridge

HOST, SOURCE, HOST_SESSION, SOURCE_SESSION = ('a' * 32, 'b' * 32, 'c' * 32, 'd' * 32)
SCOPE = dict(guild='100', server='9', season='18')


def members():
    return {key: dict(runtime=session, online=True, presence_mode='event', protocol=CONTROL_PROTOCOL,
                      web_delegate_protocol=1, bot_user_id=bot_id, application_id=bot_id, name=name)
            for key, session, bot_id, name in ((HOST, HOST_SESSION, '11', 'host'),
                                             (SOURCE, SOURCE_SESSION, '22', 'source'))}


def initial_record():
    return dict(schema=CONTROL_PROTOCOL, scope=SCOPE, generation=1, request='f' * 32,
                owner=HOST, owner_runtime=HOST_SESSION, phase='active', members=members())


def public_snapshot(name='보스'):
    at = '2026-10-12T10:00:00+09:00'
    return dict(schema=1, season='18', server_name='9', guild_name='길드', revision=1,
                captured_at=at, updated_at=at, window_start=at, window_end='2026-10-26T23:59:59+09:00',
                active=[], events=[dict(id='boss', kind='boss', name=name, note='',
                                        scheduled_at=at, end_at=at, important=False)])


class MemoryRepository:
    def __init__(self, data):
        self.data, self.files, self.writes = deepcopy(data), {}, 0
        self.get = Mock(side_effect=self._get)
        self.put = Mock(side_effect=self._put)

    def _get(self, path, **_kwargs):
        value = self.data if path.startswith('data/handover/') else self.files.get(path)
        return deepcopy(value), str(self.writes), ''

    def _put(self, path, data, **_kwargs):
        if path.startswith('data/handover/'):
            self.data = deepcopy(data)
        else:
            self.files[path] = deepcopy(data)
        self.writes += 1
        return True, ''


class DelegatedTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.clock = patch.object(relay_module, 'time', NS(monotonic=lambda: self.now))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        data = initial_record()
        info = build_delegation(data, SOURCE, SOURCE_SESSION, SCOPE, 2, 'f' * 32)
        data.update(generation=2, owner=SOURCE, owner_runtime=SOURCE_SESSION, web_delegate=info)
        self.repo = MemoryRepository(data)

    def node(self, role):
        client, runtime = (HOST, HOST_SESSION) if role == 'host' else (SOURCE, SOURCE_SESSION)
        manager = NS(lock=threading.RLock(), closed=threading.Event(), pending_snapshot=public_snapshot(),
                     delegated_snapshot_only=False, public_paused=False, snapshot_version=1,
                     written_snapshot_version=1, _log=Mock(), set_authority=Mock(),
                     publication_allowed=Mock(return_value=True), set_enabled=Mock(),
                     status=Mock(return_value=dict(state='running', enabled=True)),
                     public=NS(status=Mock(return_value=dict(state='prepared', enabled=True)), configure=Mock()))
        def offer(payload, **_kwargs):
            manager.pending_snapshot = payload
            manager.snapshot_version += 1
            manager.written_snapshot_version = manager.snapshot_version
            return manager.snapshot_version
        manager.offer_snapshot = Mock(side_effect=offer)
        app = NS(current_season_no=18, _github_get_json_file=self.repo.get, _github_put_json_file=self.repo.put)
        bridge = NS(app=app, manager=manager, lock=threading.RLock(), wake=threading.Event(),
                    operation_local=threading.local(), _sync=Mock(), retry=Mock())
        with patch.object(relay_module.threading, 'Thread'):
            relay = DelegatedHosting(bridge)
        policy = dict(ConnectionPolicy._empty(), client_id=client, runtime_id=runtime, protocol=CONTROL_PROTOCOL,
                      authority_active=role == 'source', authority_needs_sync=False,
                      authority_generation=2, authority_request='f' * 32,
                      authority_until=10**12, standby=role != 'source', query_until=10**12)
        coordinator = NS(client=client, runtime=runtime, alive=True, scope=SCOPE,
                         policy=NS(snapshot=lambda: deepcopy(policy)), _assert_profile=Mock(),
                         _control=Mock(return_value=dict(web_protocol=1)), _status=Mock(return_value=dict(online=True)),
                         record=AuthorityRecord(self.repo.get, self.repo.put, SCOPE), lock=threading.RLock(), _notify=Mock())
        coordinator._process_record = lambda data, status: relay.observe(data, coordinator)
        app.discord_handover = coordinator
        bridge.context = (('100', client, runtime, 2), coordinator, '18')
        relay.observe(self.repo.data, coordinator)
        return relay, manager, coordinator, policy

    def test_web_off_receiver_preserves_existing_web_through_voice_grant(self):
        data = initial_record()
        data['members'][HOST]['web_public_available'] = True
        repo = MemoryRepository(data)
        record = AuthorityRecord(repo.get, repo.put, SCOPE)
        requested = record.request(SOURCE, SOURCE_SESSION, '777', automatic_preserve_web=True)
        self.assertEqual(delegation_info(requested)['host'], HOST)
        record.acknowledge_stop(requested['request'], HOST, HOST_SESSION)
        granted = record.accept_stopped(requested['request'], SOURCE, SOURCE_SESSION)
        self.assertEqual(granted['owner'], SOURCE)
        self.assertEqual(delegation_info(granted)['host'], HOST)

    def test_both_web_off_does_not_start_a_schedule_relay(self):
        data = initial_record()
        data['members'][HOST]['web_public_available'] = False
        repo = MemoryRepository(data)
        requested = AuthorityRecord(repo.get, repo.put, SCOPE).request(
            SOURCE, SOURCE_SESSION, '777', automatic_preserve_web=True)
        self.assertNotIn('web_delegate', requested)

    def test_verified_voice_stop_still_preserves_web_for_unconfigured_receiver(self):
        data = initial_record()
        data['members'][HOST]['web_public_available'] = True
        repo = MemoryRepository(data)
        record = AuthorityRecord(repo.get, repo.put, SCOPE)
        requested = record.request(SOURCE, SOURCE_SESSION, '777', automatic_preserve_web=True,
                                   verified_previous=record.owner_stamp(data))
        self.assertEqual(requested['phase'], 'joining')
        self.assertEqual(delegation_info(requested)['host'], HOST)

    def reunite_host(self, phase='active'):
        relay, manager, coordinator, policy = self.node('host')
        bridge = relay.bridge
        bridge.delegation = relay
        bridge.closed = threading.Event()
        bridge.retained_host = None
        bridge.retry_serial = 0
        bridge.synced_requests = set()
        bridge._keep_retained_host = MethodType(WebHandoverBridge._keep_retained_host, bridge)
        bridge.retain_web_host = MethodType(WebHandoverBridge.retain_web_host, bridge)
        data = dict(deepcopy(self.repo.data), generation=3, request='e'*32, phase=phase,
                    owner=HOST if phase == 'active' else SOURCE,
                    owner_runtime=HOST_SESSION if phase == 'active' else SOURCE_SESSION,
                    receiver=HOST, receiver_runtime=HOST_SESSION)
        data.pop('web_delegate')
        policy['authority_generation'] = 3
        manager.set_authority.reset_mock()
        relay.observe(data, coordinator)
        return relay, manager, coordinator, policy

    def test_existing_host_reunites_voice_without_disabling_web(self):
        for phase in ('requested', 'joining', 'active'):
            with self.subTest(phase=phase):
                relay, manager, coordinator, _ = self.reunite_host(phase)
                self.assertIsNotNone(relay.bridge.retained_host)
                self.assertTrue(all(call.args[0] for call in manager.set_authority.call_args_list))
                key = (SCOPE['guild'], HOST, HOST_SESSION, 3)
                self.assertTrue(relay.bridge._keep_retained_host(key, coordinator))

    def test_failed_initial_sync_keeps_existing_host_web(self):
        relay, manager, coordinator, _ = self.reunite_host()
        bridge = relay.bridge
        bridge._ui = lambda callback: callback()
        data = dict(deepcopy(relay.record), previous_owner=[SOURCE, SOURCE_SESSION, 2, 'f'*32],
                    web_transfer=dict(request='e'*32, state='failed'))
        manager.set_authority.reset_mock()
        with self.assertRaisesRegex(ValueError, '업로드에 실패'):
            WebHandoverBridge._sync(bridge, (SCOPE['guild'], HOST, HOST_SESSION, 3), coordinator, data, '18')
        self.assertTrue(all(call.args[0] for call in manager.set_authority.call_args_list))
        self.assertIsNotNone(bridge.retained_host)

    def test_retained_web_keeps_detached_schedule_until_sync_succeeds(self):
        relay, manager, _, _ = self.reunite_host()
        self.assertTrue(manager.delegated_snapshot_only)
        old_events = deepcopy(manager.pending_snapshot['events'])
        manager.pending_snapshot = public_snapshot('unsynced local schedule')
        kept = relay.host_snapshot()
        self.assertEqual(kept['events'], old_events)
        self.assertNotEqual(kept['events'], manager.pending_snapshot['events'])

    def test_retained_web_ends_on_release_expired_session_pause_or_new_request(self):
        for case in ('release', 'session', 'pause', 'request'):
            with self.subTest(case=case):
                relay, manager, coordinator, policy = self.reunite_host()
                if case == 'release': relay.record.update(phase='idle', owner='')
                elif case == 'session': policy['query_until'] = 0
                elif case == 'pause': manager.public_paused = True
                else: relay.record['request'] = '9'*32
                manager.set_authority.reset_mock()
                self.assertFalse(relay.bridge._keep_retained_host())
                manager.set_authority.assert_not_called()
                self.assertIsNone(relay.bridge.retained_host)
                self.assertFalse(manager.delegated_snapshot_only)

    def test_older_split_capable_host_with_unknown_web_state_is_preserved(self):
        data = initial_record()
        self.assertNotIn('web_public_available', data['members'][HOST])
        info = build_automatic_delegation(data, SOURCE, SOURCE_SESSION, SCOPE, 2, 'e' * 32)
        self.assertEqual(info['host'], HOST)

    def test_automatic_split_skips_missing_offline_and_wrong_profile_hosts(self):
        for case in ('missing', 'offline', 'wrong-season', 'legacy'):
            with self.subTest(case=case):
                data = initial_record()
                if case == 'missing': data.update(owner='', owner_runtime='', phase='idle')
                elif case == 'offline': data['members'][HOST]['online'] = False
                elif case == 'wrong-season': data['scope'] = dict(SCOPE, season='17')
                else: data['members'][HOST]['web_delegate_protocol'] = 0
                self.assertIsNone(build_automatic_delegation(data, SOURCE, SOURCE_SESSION, SCOPE, 2, 'e' * 32))

    def test_automatic_preservation_keeps_an_existing_split_host(self):
        requested = AuthorityRecord(self.repo.get, self.repo.put, SCOPE).request(
            SOURCE, SOURCE_SESSION, '777', automatic_preserve_web=True)
        self.assertEqual(delegation_info(requested)['host'], HOST)

    def test_web_configuration_availability_ignores_transient_connection_states(self):
        node = object.__new__(DiscordAuthority)
        manager = NS(public_paused=False, status=Mock(return_value=dict(enabled=True, state='starting')),
                     ddns_status=Mock(return_value=dict(configured=True)),
                     public=NS(status=Mock(return_value=dict(enabled=True, state='waiting'))))
        node.app = NS(web_service_manager=manager)
        self.assertTrue(node._web_publication_configured())
        manager.public.status.return_value['state'] = 'error'
        self.assertTrue(node._web_publication_configured())
        manager.status.return_value['enabled'] = False
        self.assertFalse(node._web_publication_configured())
        manager.status.return_value['enabled'] = True
        manager.ddns_status.return_value['configured'] = False
        self.assertFalse(node._web_publication_configured())
        manager.ddns_status.return_value['configured'] = True
        manager.public.status.return_value['enabled'] = False
        self.assertFalse(node._web_publication_configured())

    def test_web_presence_changes_do_not_create_periodic_remote_checks(self):
        node = object.__new__(DiscordAuthority)
        node.alive = True
        node._web_publication_configured = Mock(return_value=True)
        node.session_connected = Mock()
        for _ in range(10): node.refresh_web_presence()
        self.assertEqual(node.session_connected.call_count, 1)
        node._web_publication_configured.return_value = False
        for _ in range(10): node.refresh_web_presence()
        self.assertEqual(node.session_connected.call_count, 2)
        node.alive = False
        node._web_publication_configured.return_value = True
        node.refresh_web_presence()
        self.assertEqual(node.session_connected.call_count, 2)

    def test_default_request_does_not_split_and_explicit_request_survives_voice_grant(self):
        repo = MemoryRepository(initial_record())
        record = AuthorityRecord(repo.get, repo.put, SCOPE)
        plain = record.request(SOURCE, SOURCE_SESSION, '777')
        self.assertNotIn('web_delegate', plain)
        repo.data = initial_record()
        requested = record.request(SOURCE, SOURCE_SESSION, '777', preserve_web=True)
        self.assertEqual(delegation_info(requested)['host'], HOST)
        record.acknowledge_stop(requested['request'], HOST, HOST_SESSION)
        granted = record.accept_stopped(requested['request'], SOURCE, SOURCE_SESSION)
        self.assertEqual(delegation_info(granted)['generation'], granted['generation'])

    def test_split_rejects_other_season_and_old_peer(self):
        data = initial_record()
        with self.assertRaises(ValueError):
            build_delegation(data, SOURCE, SOURCE_SESSION, dict(SCOPE, season='19'), 2, 'f' * 32)
        data['members'][HOST]['web_delegate_protocol'] = 0
        with self.assertRaises(ValueError):
            build_delegation(data, SOURCE, SOURCE_SESSION, SCOPE, 2, 'f' * 32)

    def test_host_renews_web_without_voice_and_never_polls_schedule(self):
        relay, manager, coordinator, policy = self.node('host')
        self.assertFalse(ConnectionPolicy.has_authority(policy))
        for _ in range(5):
            relay.tick()
            relay._step()
        self.assertEqual(manager.set_authority.call_args.args[0][1], HOST)
        self.repo.get.assert_not_called()
        self.repo.put.assert_not_called()
        self.assertEqual(coordinator._control.call_count, 1)
        policy['query_until'] = 0
        manager.set_authority.reset_mock()
        relay.tick()
        manager.set_authority.assert_not_called()

    def test_pending_web_connection_never_changes_assignment(self):
        relay, manager, coordinator, _ = self.node('host')
        manager.public.status.return_value = dict(state='waiting', enabled=True)
        relay._step()
        self.assertEqual(relay.role, 'host')
        self.assertFalse(coordinator._control.call_args.kwargs['ready'])
        self.repo.put.assert_not_called()

    def test_debounce_no_repeated_upload_and_new_edit_resets_timer(self):
        relay, manager, coordinator, _ = self.node('source')
        relay.synced = True
        relay.offer_local(public_snapshot())
        relay._step()
        self.now += 89
        relay._step()
        self.repo.put.assert_not_called()
        self.now += 1
        relay._step()
        self.assertEqual(self.repo.put.call_count, 1)
        old_pending = relay.pending
        relay.offer_local(public_snapshot('수정 보스'))
        self.now += 1
        relay._step()
        self.assertIsNone(relay.pending)
        self.assertEqual(self.repo.put.call_count, 1)
        self.now += 90
        relay._step()
        self.assertEqual(self.repo.put.call_count, 2)
        self.assertGreater(relay.pending['sequence'], old_pending['sequence'])
        event = dict(kind='web', operation='ack', delegate=relay.info['id'], target=SOURCE,
                     target_runtime=SOURCE_SESSION, client=HOST, runtime=HOST_SESSION, sender_bot_id='11',
                     transfer=relay.pending['transfer'], sequence=relay.pending['sequence'], ok=True)
        relay.receive(event, self.repo.data)
        self.now += 1000
        relay._step()
        self.assertEqual(self.repo.put.call_count, 2)

    def test_failed_send_is_bounded_and_manual_retry_reuses_artifact(self):
        relay, manager, coordinator, _ = self.node('source')
        relay.synced = True
        relay.offer_local(public_snapshot())
        relay.sync_now()
        relay._step()
        for _ in range(3):
            self.now += 16
            relay._step()
        self.assertEqual(coordinator._control.call_count, 3)
        self.assertEqual(self.repo.put.call_count, 1)
        relay.sync_now()
        relay._step()
        self.assertEqual(coordinator._control.call_count, 4)
        self.assertEqual(self.repo.put.call_count, 1)

    def transfer(self, relay, sequence=1):
        envelope = dict(schema=1, delegate=relay.info['id'], source=SOURCE, source_runtime=SOURCE_SESSION,
                        sequence=sequence, snapshot=public_snapshot())
        transfer = '1' * 32
        self.repo.files[relay_module.artifact_path(relay.info['id'], transfer)] = envelope
        return dict(kind='web', operation='schedule', delegate=relay.info['id'], target=HOST,
                    target_runtime=HOST_SESSION, client=SOURCE, runtime=SOURCE_SESSION, sender_bot_id='22',
                    sequence=sequence, transfer=transfer, hash=content_hash(envelope))

    def test_host_applies_web_only_and_duplicate_ack_does_not_download_again(self):
        relay, manager, coordinator, _ = self.node('host')
        event = self.transfer(relay)
        original = deepcopy(manager.pending_snapshot)
        relay.receive(event, self.repo.data)
        relay._step()
        self.assertEqual(relay.applied_sequence, 1)
        self.assertEqual(manager.offer_snapshot.call_args.kwargs['origin'], 'delegated')
        self.assertEqual(original, public_snapshot())
        self.repo.get.reset_mock()
        coordinator._control.reset_mock()
        relay.receive(event, self.repo.data)
        relay._step()
        self.repo.get.assert_not_called()
        self.assertEqual(coordinator._control.call_args.kwargs['operation'], 'ack')

    def test_wrong_sender_or_session_cannot_request_download(self):
        relay, _, _, _ = self.node('host')
        event = self.transfer(relay)
        for values in (dict(sender_bot_id='99'), dict(runtime='0' * 32), dict(target_runtime='0' * 32)):
            relay.receive(dict(event, **values), self.repo.data)
        self.assertFalse(relay.requests)
        self.repo.get.assert_not_called()

    def test_late_download_does_not_overwrite_new_generation(self):
        relay, manager, coordinator, _ = self.node('host')
        event = self.transfer(relay)
        old_get = self.repo.get.side_effect
        def changed(path, **kw):
            result = old_get(path, **kw)
            updated = deepcopy(self.repo.data)
            updated['generation'] += 1
            updated['web_delegate']['generation'] = updated['generation']
            relay.observe(updated, coordinator)
            return result
        self.repo.get.side_effect = changed
        with self.assertRaises(ValueError):
            relay._host_download(deepcopy(relay.info), event)
        manager.offer_snapshot.assert_not_called()
        self.assertEqual(relay.applied_sequence, 0)

    def test_web_transfer_waits_for_confirmed_deny_and_never_prompts_host(self):
        relay, manager, coordinator, _ = self.node('host')
        self.repo.data['web_delegate']['state'] = 'releasing'
        relay.observe(self.repo.data, coordinator)
        manager.emergency_stop = Mock()
        manager.public.status.return_value.update(state='off', busy=False)
        manager.ddns_status = Mock(return_value=dict(configured=False))
        manager.ddns = NS(status=Mock(return_value=dict(busy=False)))
        manager._flush_authority = Mock()
        manager.root = Path('unused-offline-web-root')
        with patch.object(relay_module, 'read_json', return_value=dict(enabled=True)):
            with self.assertRaises(ValueError):
                relay._release_host(deepcopy(relay.info))
        self.assertEqual(self.repo.data['web_delegate']['state'], 'releasing')
        with patch.object(relay_module, 'read_json', return_value=dict(enabled=False)):
            relay._release_host(deepcopy(relay.info))
        self.assertEqual(self.repo.data['web_delegate']['state'], 'stopped')
        self.assertIsNotNone(relay.notice)
        self.assertEqual(manager.emergency_stop.call_count, 2)

    def test_local_snapshot_is_blocked_on_delegated_host(self):
        manager = NS(lock=threading.RLock(), closed=threading.Event(), view=dict(enabled=True),
                     delegated_snapshot_only=True, pending_snapshot=None, snapshot_version=0, wake=threading.Event())
        self.assertIsNone(WebServiceManager.offer_snapshot(manager, public_snapshot()))
        self.assertIsNone(manager.pending_snapshot)
        WebServiceManager.offer_snapshot(manager, public_snapshot(), origin='delegated')
        self.assertEqual(manager.snapshot_version, 1)

    def test_capture_timestamps_do_not_count_as_edits(self):
        original = public_snapshot()
        self.assertEqual(edit_hash(original), edit_hash(dict(original, captured_at='later', updated_at='later', revision=99)))

    def test_outgoing_stop_failure_has_no_host_confirmation_popup(self):
        relay, manager, _, _ = self.node('host')
        data = deepcopy(self.repo.data)
        data.update(owner=HOST, owner_runtime=HOST_SESSION, receiver=SOURCE,
                    receiver_runtime=SOURCE_SESSION, phase='requested')
        node = object.__new__(DiscordAuthority)
        node.client = HOST
        node.unattended_requests = set()
        node._progress = Mock()
        node._wait_local_stop = Mock(side_effect=ValueError('stop unavailable'))
        node._ui = lambda callback, **_kw: callback()
        node.app = NS(web_service_manager=NS(delegation=relay), _append_debug_log=Mock(),
                      _show_centered_messagebox=Mock(), root=None, schedule_window=None)
        node._outgoing(data, dict(id='9', name='9'))
        node.app._show_centered_messagebox.assert_not_called()
        self.assertIn(data['request'], node.unattended_requests)
        self.assertTrue(node._unattended_web_host(dict(failure_request=data['request'])))

    def test_source_initial_sync_failure_preserves_existing_web_until_edit(self):
        relay, manager, _, _ = self.node('source')
        relay.bridge._sync.side_effect = ValueError('upload not ready')
        relay.offer_local(public_snapshot())
        relay._step()
        relay.offer_local(public_snapshot())
        self.now += 121
        relay._step()
        self.repo.put.assert_not_called()
        relay.offer_local(public_snapshot('관리자가 수정함'))
        relay._step()
        self.now += 121
        relay._step()
        self.assertEqual(self.repo.put.call_count, 1)

    def test_initial_event_read_failure_retries_without_any_confirmation(self):
        node = object.__new__(DiscordAuthority)
        node.alive = True
        node.event_lock = node.lock = threading.RLock()
        node._assert_profile = Mock()
        node.record = NS(read=Mock(side_effect=ValueError('repository unavailable')))
        node._ui = lambda callback, **_kw: callback()
        node.app = NS(_append_debug_log=Mock(), schedule_status_var=NS(set=Mock()),
                      _show_centered_messagebox=Mock())
        with patch.object(authority_module.threading, 'Thread', side_effect=lambda target, **_kw: NS(start=target)), \
             patch.object(authority_module.threading, 'Timer') as timer:
            node.changed(dict(kind='changed'))
        node.app._show_centered_messagebox.assert_not_called()
        timer.return_value.start.assert_called_once()

    def test_stale_automatic_upload_retry_cannot_write_new_handover(self):
        node = object.__new__(DiscordAuthority)
        node.alive = True
        node.lock = threading.RLock()
        node._assert_profile = Mock()
        node.record = NS(read=lambda: (dict(request='new'), 'sha'))
        node.app = NS(_upload_current_schedule_to_github_data=Mock())
        node._retry_upload(dict(id='9'), {}, 1, expected_request='old')
        node.app._upload_current_schedule_to_github_data.assert_not_called()

    def test_ui_thread_sync_guard_allows_only_explicit_current_delegation(self):
        relay, _, coordinator, _ = self.node('source')
        bridge = relay.bridge
        bridge.closed = threading.Event()
        bridge.retry_serial = 0
        bridge.delegation = relay
        key = bridge.context[0]
        with self.assertRaises(ValueError):
            WebHandoverBridge._guard(bridge, key, coordinator)
        # Tk has no worker-thread local flags: the captured delegation must
        # be passed through the UI closure rather than silently rejecting it.
        WebHandoverBridge._guard(bridge, key, coordinator, delegated_info=deepcopy(relay.info))
        stale = dict(relay.info, generation=1)
        with self.assertRaises(ValueError):
            WebHandoverBridge._guard(bridge, key, coordinator, delegated_info=stale)

    def test_manual_sync_can_publish_after_failed_initial_import(self):
        relay, _, _, _ = self.node('source')
        relay.bridge._sync.side_effect = ValueError('not ready')
        relay.offer_local(public_snapshot())
        relay._step()
        relay.offer_local(public_snapshot())
        relay.sync_now()
        relay._step()
        self.assertEqual(self.repo.put.call_count, 1)

    def test_late_upload_success_resumes_failed_sync_and_web_preflight(self):
        relay, manager, coordinator, _ = self.node('source')
        relay.record['web_transfer'] = dict(request='f'*32, state='failed')
        relay.initial_sync_failed = relay.synced = relay.auto_return_started = True
        relay.local_payload = public_snapshot()
        relay.sent_hash = edit_hash(relay.local_payload)
        relay.bridge.recovery = NS(stage='preflight_failed', observe=Mock())
        data = dict(deepcopy(relay.record), web_transfer=dict(request='f'*32, state='ready'))
        relay.observe(data, coordinator)
        self.assertFalse(relay.synced)
        self.assertFalse(relay.initial_sync_failed)
        self.assertFalse(relay.auto_return_started)
        manager._log.assert_any_call('web_delegated_upload_ready', resume_sync=True, delegate=relay.info['id'])

    def test_late_upload_success_never_overwrites_local_edits_after_failed_sync(self):
        relay, _, coordinator, _ = self.node('source')
        relay.record['web_transfer'] = dict(request='f'*32, state='failed')
        relay.initial_sync_failed = relay.synced = relay.auto_return_started = True
        relay.sent_hash = edit_hash(public_snapshot())
        relay.local_payload = public_snapshot('local edit')
        relay.bridge.recovery = NS(stage='preflight_failed', observe=Mock())
        data = dict(deepcopy(relay.record), web_transfer=dict(request='f'*32, state='ready'))
        relay.observe(data, coordinator)
        self.assertTrue(relay.synced)
        self.assertTrue(relay.initial_sync_failed)
        self.assertTrue(relay.auto_return_started)

    def test_upload_ready_event_clears_only_matching_failed_web_attempt(self):
        relay, _, coordinator, _ = self.node('source')
        bridge = relay.bridge
        bridge.transfer_state = None
        bridge.failed_key = bridge.context[0]
        data = dict(deepcopy(relay.record), web_transfer=dict(request='f'*32, state='ready'))
        WebHandoverBridge.observe_transfer(bridge, data, coordinator)
        self.assertIsNone(bridge.failed_key)
        bridge.failed_key = bridge.context[0]
        WebHandoverBridge.observe_transfer(bridge, data, coordinator)  # Duplicate is a no-op.
        self.assertIsNotNone(bridge.failed_key)
        data.update(generation=99, request='e'*32, web_transfer=dict(request='e'*32, state='ready'))
        WebHandoverBridge.observe_transfer(bridge, data, coordinator)
        self.assertEqual(bridge.failed_key, bridge.context[0])

    def test_upload_retry_publishes_ready_event_without_extra_polling(self):
        node = object.__new__(DiscordAuthority)
        node.alive, node.lock = True, threading.RLock()
        self.repo.data['web_transfer'] = dict(request='f'*32, state='failed',
                                             content_hash='snapshot-hash', entry=dict(id='9'))
        node.record = AuthorityRecord(self.repo.get, self.repo.put, SCOPE)
        node._assert_profile = Mock()
        node._ui = lambda callback, **_: callback()
        node._notify = Mock()
        node.app = NS(_upload_current_schedule_to_github_data=Mock(return_value=(True, 'ready', dict(id='9'))),
                      _get_github_schedule_content_hash=lambda _: 'snapshot-hash',
                      schedule_status_var=Mock(), _append_debug_log=Mock())
        node._retry_upload(dict(id='9'), {}, 1, expected_request='f'*32)
        self.assertEqual(self.repo.data['web_transfer']['state'], 'ready')
        node._notify.assert_called_once()
        self.assertEqual(node._notify.call_args.args[0]['web_transfer']['state'], 'ready')

    def test_latest_encrypted_settings_are_prepared_before_web_stop(self):
        relay, manager, coordinator, _ = self.node('host')
        self.repo.data['web_delegate']['state'] = 'releasing'
        relay.observe(self.repo.data, coordinator)
        manager.ddns_status = Mock(return_value=dict(configured=True))
        manager.ddns = NS(status=Mock(return_value=dict(busy=False)))
        manager.emergency_stop = Mock()
        manager.public.status.return_value.update(state='off', busy=False)
        manager._flush_authority = Mock()
        manager.root = Path('unused-offline-web-root')
        encrypted = dict(schema=1, ciphertext='offline encrypted settings')
        with patch('web_shared_state.export_state', return_value=encrypted), \
             patch.object(relay_module, 'read_json', return_value=dict(enabled=False)):
            relay._release_host(deepcopy(relay.info))
        path = self.repo.data['web_transfer']['web_state_path']
        self.assertEqual(self.repo.files[path], encrypted)
        self.assertEqual(self.repo.data['web_delegate']['state'], 'stopped')

    def test_pending_local_context_refresh_is_not_an_initial_sync_failure(self):
        relay, _, coordinator, _ = self.node('source')
        current = relay.bridge.context
        relay.bridge.context = (('100', SOURCE, SOURCE_SESSION, 1), coordinator, '18')
        relay.offer_local(public_snapshot())
        relay._step()
        relay.bridge._sync.assert_not_called()
        self.assertFalse(relay.initial_sync_failed)
        relay.bridge.context = current
        relay._step()
        relay.bridge._sync.assert_called_once()
        self.assertTrue(relay.synced)


if __name__ == '__main__':
    unittest.main()
