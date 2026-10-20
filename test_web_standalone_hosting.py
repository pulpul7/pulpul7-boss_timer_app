"""Offline web-only start/stop checks. No listener, router, certificate, or files."""
from copy import deepcopy
from pathlib import Path
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from discord_authority import AuthorityRecord
from test_web_delegated_hosting import (MemoryRepository, initial_record, public_snapshot,
    HOST, SOURCE, HOST_SESSION, SOURCE_SESSION, SCOPE)
from web_standalone_hosting import StandaloneHosting, standalone_info


class StandaloneTests(unittest.TestCase):
    def node(self, *, owner=HOST):
        data = initial_record()
        data.update(owner=owner, owner_runtime=HOST_SESSION if owner == HOST else SOURCE_SESSION)
        self.repo = MemoryRepository(data)
        public = dict(state='prepared', busy=False, address='https://example.invalid')
        manager = NS(lock=threading.RLock(), closed=threading.Event(), public_paused=False,
            root=Path('unused-offline-web-root'), snapshot_version=1, _log=Mock(),
            ddns_status=lambda: dict(configured=True), set_enabled=Mock(),
            https_status=lambda: dict(https_state='running', domain='example.invalid', port=8766),
            prepare_local_https=Mock(), status=Mock(return_value=dict(state='running')),
            public=NS(status=lambda: deepcopy(public), configure=Mock()),
            ddns=NS(status=Mock(return_value=dict(busy=False))),
            publication_allowed=Mock(return_value=True), set_authority=Mock(),
            offer_snapshot=Mock(), emergency_stop=Mock(), _flush_authority=Mock())
        app = NS(current_season_no=18, _github_get_json_file=self.repo.get)
        manager.publication_ddns_status = Mock(side_effect=lambda: dict(manager.ddns.status(), configured=True))
        coordinator = NS(client=SOURCE, runtime=SOURCE_SESSION, alive=True, scope=SCOPE,
            lock=threading.RLock(), record=AuthorityRecord(self.repo.get, self.repo.put, SCOPE),
            _assert_profile=Mock(), _status=Mock(return_value=dict(online=True)),
            policy=NS(snapshot=lambda: dict(runtime_id=SOURCE_SESSION, query_until=time.monotonic()+100)),
            _ui=lambda callback: callback(), web_control=NS(request=Mock()))
        app.discord_handover = coordinator
        bridge = NS(app=app, manager=manager, wake=threading.Event(), retained_host=None,
            _shared_path=lambda _: 'data/web/shared.json', _sync=Mock(),
            delegation=NS(lock=threading.RLock(), wake=threading.Event(), notice=None))
        manual = StandaloneHosting(bridge)
        coordinator._process_record = lambda data, _: manual.observe(data, coordinator)
        manual.observe(self.repo.data, coordinator)
        def stop_peer(payload, done):
            self.repo.data['web_standalone'] = dict(protocol=1, id='e'*32, state='stopped',
                owner=HOST, runtime=HOST_SESSION, **SCOPE)
            done(True, 'stopped')
        coordinator.web_control.request.side_effect = stop_peer
        return manual, manager, coordinator, public

    def run_start(self, manual):
        manual.busy = True
        with patch('web_direct_hosting.check_local_https'), \
             patch('web_schedule_snapshot.collect_snapshot', return_value=public_snapshot()):
            manual._start_worker()

    def test_force_start_stops_peer_and_preserves_voice_without_schedule_import(self):
        manual, manager, coordinator, _ = self.node()
        stamp = AuthorityRecord.owner_stamp(self.repo.data)
        self.run_start(manual)
        self.assertEqual(standalone_info(self.repo.data)['owner'], SOURCE)
        self.assertEqual(AuthorityRecord.owner_stamp(self.repo.data), stamp)
        coordinator.web_control.request.assert_called_once()
        self.assertEqual(coordinator.web_control.request.call_args.args[0]['operation'], 'administrator_web_stop')
        manual.bridge._sync.assert_not_called()
        manager.offer_snapshot.assert_called_once()
        self.assertNotIn('web_operation', self.repo.data)
        self.assertEqual(manual.stage, 'starting')

    def test_solo_start_does_not_require_handover_upload(self):
        manual, _, coordinator, _ = self.node(owner=SOURCE)
        self.repo.data['web_transfer'] = dict(state='failed')
        self.run_start(manual)
        self.assertEqual(standalone_info(self.repo.data)['state'], 'active')
        coordinator.web_control.request.assert_not_called()
        manual.bridge._sync.assert_not_called()

    def test_worker_start_failure_keeps_actual_reason_and_does_not_stop_peer(self):
        manual, manager, coordinator, _ = self.node()
        manager.status.return_value = dict(state='error', enabled=True,
            message='웹서버 시작 응답이 없습니다. 다시 시도해주세요.')
        manager.https_status = lambda: dict(https_state='stopped')
        self.run_start(manual)
        self.assertEqual(manual.stage, 'error')
        self.assertIn('웹서버 시작 응답이 없습니다', manual.message)
        self.assertNotIn('HTTPS 준비에 실패', manual.message)
        coordinator.web_control.request.assert_not_called()

    def test_prepared_local_worker_is_reused_during_force_start(self):
        manual, manager, _, _ = self.node(owner=SOURCE)
        manager.status.return_value.update(enabled=True)
        self.run_start(manual)
        manager.set_enabled.assert_not_called()
        self.assertEqual(manual.stage, 'starting')

    def test_missing_pin_explanation_reaches_ui_and_diagnostic_without_stopping_peer(self):
        from web_direct_hosting import PublicError
        manual, manager, coordinator, _ = self.node()
        message = '비밀번호 탭에서 웹 비밀번호를 먼저 설정해주세요.'
        with patch('web_direct_hosting.check_local_https', side_effect=PublicError(message)):
            manual._start_worker(force=False, approved=True)
        self.assertEqual(manual.stage, 'error')
        self.assertEqual(manual.message, message)
        manager.set_authority.assert_called_once_with((), message=message)
        manager._log.assert_any_call('web_standalone_start_failed', error_type='PublicError', reason=message)
        coordinator.web_control.request.assert_not_called()
        manager.public.configure.assert_not_called()
        self.assertNotIn('web_operation', self.repo.data)

    def test_unexpected_local_check_failure_does_not_expose_exception_text(self):
        manual, manager, coordinator, _ = self.node()
        with patch('web_direct_hosting.check_local_https', side_effect=RuntimeError('private fixture token')):
            manual._start_worker(force=False, approved=True)
        self.assertNotIn('private fixture token', manual.message)
        manager._log.assert_any_call('web_standalone_start_failed', error_type='RuntimeError', reason='')
        coordinator.web_control.request.assert_not_called()

    def test_missing_member_password_blocks_handover_before_old_web_is_stopped(self):
        manual, manager, coordinator, _ = self.node()
        message = '비밀번호 탭에서 회원용 웹 비밀번호를 먼저 저장해주세요. 공동 관리자는 기존 웹 비밀번호를 인계받아 사용합니다.'
        manager.access = NS(require_member_pin=Mock(side_effect=ValueError(message)))
        self.run_start(manual)
        self.assertEqual(manual.stage, 'error')
        self.assertEqual(manual.message, message)
        coordinator.web_control.request.assert_not_called()
        manager.public.configure.assert_not_called()
        self.assertNotIn('web_operation', self.repo.data)

    def test_failed_peer_stop_never_activates_new_web(self):
        manual, manager, coordinator, _ = self.node()
        coordinator.web_control.request.side_effect = lambda _, done: done(False, 'peer did not stop')
        self.run_start(manual)
        self.assertEqual(standalone_info(self.repo.data)['owner'], HOST)
        manager.public.configure.assert_not_called()
        manager.offer_snapshot.assert_not_called()
        self.assertEqual(manual.stage, 'error')

    def test_concurrent_operation_is_rejected_without_stopping_existing_web(self):
        manual, manager, coordinator, _ = self.node()
        self.repo.data['web_operation'] = dict(id='9'*32, expires=time.time()+100)
        self.run_start(manual)
        coordinator.web_control.request.assert_not_called()
        manager.set_authority.assert_not_called()
        self.assertEqual(self.repo.data['web_operation']['id'], '9'*32)

    def test_voice_changes_during_force_start_do_not_cancel_web_operation(self):
        manual, manager, coordinator, _ = self.node()
        def changed(_, done):
            self.repo.data['generation'] += 1
            done(True, 'stopped')
        coordinator.web_control.request.side_effect = changed
        self.run_start(manual)
        self.assertEqual(standalone_info(self.repo.data)['owner'], SOURCE)
        manager.public.configure.assert_called_once()

    def test_logout_during_force_start_prevents_late_public_activation(self):
        manual, manager, coordinator, _ = self.node()
        def logout(_, done):
            manual.cancelled.set()
            done(True, 'stopped')
        coordinator.web_control.request.side_effect = logout
        self.run_start(manual)
        self.assertEqual(standalone_info(self.repo.data)['owner'], HOST)
        manager.public.configure.assert_not_called()
        self.assertEqual(manual.stage, 'error')

    def test_standalone_lease_needs_bot_connection_but_not_voice_authority(self):
        manual, manager, coordinator, _ = self.node()
        self.run_start(manual)
        manager.set_authority.reset_mock()
        manual.tick()
        self.assertTrue(manager.set_authority.call_args.kwargs['ready'])
        self.assertEqual(self.repo.data['owner'], HOST)
        coordinator.policy.snapshot = lambda: dict(runtime_id=SOURCE_SESSION, query_until=0)
        manager.set_authority.reset_mock()
        manual.tick()
        manager.set_authority.assert_not_called()

    def test_verified_stop_marks_web_stopped_and_preserves_voice(self):
        manual, manager, _, public = self.node(owner=SOURCE)
        manager.status.return_value = dict(state='stopped')
        public.update(state='off')
        stamp = AuthorityRecord.owner_stamp(self.repo.data)
        with patch('web_standalone_hosting.read_json', return_value=dict(enabled=False)):
            manual.stop_sync()
        manager.emergency_stop.assert_called_once()
        self.assertEqual(standalone_info(self.repo.data)['state'], 'stopped')
        self.assertEqual(AuthorityRecord.owner_stamp(self.repo.data), stamp)

    def test_unverified_public_deny_never_acknowledges_stop(self):
        manual, _, _, _ = self.node(owner=SOURCE)
        with patch('web_standalone_hosting.read_json', return_value=dict(enabled=True)):
            with self.assertRaises(ValueError):
                manual.stop_sync()
        self.assertIsNone(standalone_info(self.repo.data))

    def test_handover_stop_keeps_local_worker_running_after_public_deny(self):
        manual, manager, _, public = self.node(owner=SOURCE)
        public.update(state='off')
        with patch('web_standalone_hosting.read_json', return_value=dict(enabled=False)):
            manual.stop_sync(for_handover=True)
        manager.emergency_stop.assert_called_once_with(standby=True)
        self.assertEqual(manager.status()['state'], 'running')
        self.assertEqual(standalone_info(self.repo.data)['state'], 'stopped')

    def test_shared_settings_are_encrypted_and_uploaded_only_when_changed(self):
        manual, manager, _, public = self.node(owner=SOURCE)
        self.run_start(manual)
        manager.pin_public_port = Mock()
        manager.settings_path = manager.root/'settings.json'
        public['external_port'] = 8766
        manual.app._github_put_json_file = self.repo.put
        before = self.repo.get.call_count
        with patch.object(Path, 'stat', return_value=NS(st_mtime_ns=1, st_size=100)), \
             patch('web_shared_state.export_state', return_value=dict(schema=1, encrypted='encrypted fixture')) as export:
            manual.publish_shared()
            for _ in range(5):
                manual.next_shared_check = 0
                manual.publish_shared()
        export.assert_called_once()
        self.assertEqual(self.repo.get.call_count-before, 2)  # SHA + one ownership verification.
        self.assertEqual(self.repo.files['data/web/shared.json']['encrypted'], 'encrypted fixture')

    def test_late_stop_from_old_session_cannot_stop_new_session_record(self):
        manual, manager, _, public = self.node(owner=SOURCE)
        manager.status.return_value = dict(state='stopped')
        public.update(state='off')
        self.repo.data['members'][SOURCE]['runtime'] = '9'*32
        with patch('web_standalone_hosting.read_json', return_value=dict(enabled=False)):
            with self.assertRaisesRegex(ValueError, '세션'):
                manual.stop_sync()
        self.assertIsNone(standalone_info(self.repo.data))

    def test_remote_handover_defers_only_record_write_after_verified_shutdown(self):
        manual, manager, _, public = self.node(owner=SOURCE)
        public.update(state='off')
        before = deepcopy(self.repo.data)
        with patch('web_standalone_hosting.read_json', return_value=dict(enabled=False)), \
             patch.object(manual, '_save_settings_before_stop') as save:
            manual.stop_sync(for_handover=True, record_stop=False)
        save.assert_called_once()
        manager.emergency_stop.assert_called_once_with(standby=True)
        self.assertEqual(self.repo.data, before)
        self.assertEqual(self.repo.writes, 0)
        self.assertFalse(manual.busy)

    def test_remote_shutdown_keeps_lease_loop_paused_and_restores_busy_on_failure(self):
        manual, manager, _, _ = self.node(owner=SOURCE)
        manual.busy = True  # Simulate an earlier startup worker being cancelled.
        def while_saving():
            self.assertTrue(manual.status()['busy'])
            self.assertTrue(manual.stopping)
            manual.busy = False  # The earlier worker finishes during this stop.
            self.assertTrue(manual.status()['busy'])
            manual.tick()
            manager.set_authority.assert_not_called()
        with patch.object(manual, '_save_settings_before_stop', side_effect=while_saving), \
             patch('web_standalone_hosting.read_json', return_value=dict(enabled=True)):
            with self.assertRaises(ValueError):
                manual.stop_sync(for_handover=True)
        self.assertFalse(manual.busy)

    def test_identical_shared_envelope_is_not_imported_twice_during_handover(self):
        manual, _, _, _ = self.node()
        envelope = dict(schema=1, encrypted='same verified fixture')
        original = manual.app._github_get_json_file
        manual.app._github_get_json_file = lambda path, **kwargs: (envelope, 'sha', '') if path == 'data/web/shared.json' else original(path)
        with patch('web_shared_state.import_state') as imported:
            self.run_start(manual)
        imported.assert_called_once()
        self.assertEqual(manual.stage, 'starting')

    def test_changed_shared_envelope_after_peer_stop_is_always_imported(self):
        manual, _, coordinator, _ = self.node()
        envelopes = [dict(schema=1, encrypted='before'), dict(schema=1, encrypted='new PIN or certificate')]
        manual.app._github_get_json_file = Mock(side_effect=[(data, 'sha', '') for data in envelopes])
        with patch('web_shared_state.import_state') as imported:
            self.run_start(manual)
        self.assertEqual(imported.call_count, 2)
        self.assertEqual(imported.call_args.args[1], envelopes[1])
        self.assertEqual(manual.stage, 'starting')

    def test_settings_already_uploaded_by_this_host_are_not_uploaded_again_on_stop(self):
        manual, manager, _, _ = self.node(owner=SOURCE)
        manager.settings_path = manager.root/'settings.json'
        manual.app._github_put_json_file = self.repo.put
        with patch.object(Path, 'stat', return_value=NS(st_mtime_ns=1, st_size=100)), \
             patch('web_shared_state.export_state') as export:
            manual.shared_signature = manual._shared_settings_signature()
            before = self.repo.get.call_count
            manual._save_settings_before_stop()
        export.assert_not_called()
        self.repo.put.assert_not_called()
        self.assertEqual(self.repo.get.call_count-before, 1)  # Ownership proof only.

    def test_changed_settings_are_saved_before_stop_and_cache_is_updated(self):
        manual, manager, _, _ = self.node(owner=SOURCE)
        manager.settings_path = manager.root/'settings.json'
        manual.app._github_put_json_file = self.repo.put
        with patch.object(Path, 'stat', return_value=NS(st_mtime_ns=2, st_size=100)), \
             patch('web_shared_state.export_state', return_value=dict(schema=1, encrypted='fixture')) as export:
            manual.shared_signature = ('old settings',)
            manual._save_settings_before_stop()
            self.assertEqual(manual.shared_signature, manual._shared_settings_signature())
        export.assert_called_once()
        self.repo.put.assert_called_once()


if __name__ == '__main__':
    unittest.main()
