"""Closed-manager and stale-web-owner recovery checks, entirely in memory."""
from copy import deepcopy
from types import SimpleNamespace as NS
import threading
import unittest
from unittest.mock import Mock, patch

from discord_authority import DiscordAuthority, AuthorityRecord
from web_standalone_hosting import closed_web_confirmed
import test_web_standalone_hosting as fixtures
from test_web_delegated_hosting import HOST, HOST_SESSION, SOURCE, SOURCE_SESSION, SCOPE


class DepartedHostTests(unittest.TestCase):
    def node(self, proof=True):
        fixture = fixtures.StandaloneTests()
        manual, manager, coordinator, public = fixture.node(owner=SOURCE)
        manual.bridge.independent = True
        fixture.repo.data['web_standalone'] = dict(protocol=1, id='e'*32, state='active',
                                                 owner=HOST, runtime=HOST_SESSION, **SCOPE)
        member = fixture.repo.data['members'][HOST]
        member['online'] = False
        if proof:
            member['web_closed_runtime'] = HOST_SESSION
        coordinator._control = Mock(return_value=dict(members=[]))
        manual.app._show_centered_messagebox = Mock(side_effect=AssertionError('closed host must not prompt'))
        manual.observe(fixture.repo.data, coordinator)
        return fixture, manual, manager, coordinator

    def test_closed_host_is_retired_and_web_starts_without_remote_logout_or_prompt(self):
        fixture, manual, manager, coordinator = self.node()
        fixture.repo.data['web_operation'] = dict(id='f'*32, owner=HOST, runtime=HOST_SESSION, expires=10**12)
        before = AuthorityRecord.owner_stamp(fixture.repo.data)
        manual.busy = True
        with patch('web_direct_hosting.check_local_https'), patch('web_schedule_snapshot.collect_snapshot', return_value={}):
            manual._start_worker(force=False)
        self.assertEqual(fixture.repo.data['web_standalone']['owner'], SOURCE)
        coordinator.web_control.request.assert_not_called()
        manual.app._show_centered_messagebox.assert_not_called()
        self.assertEqual(AuthorityRecord.owner_stamp(fixture.repo.data), before)
        manager.public.configure.assert_called_once_with(True)
        self.assertEqual(manual.stage, 'starting')

    def test_legacy_exit_marker_waits_for_lease_and_ddns_grace_before_retirement(self):
        fixture, manual, manager, coordinator = self.node(proof=False)
        clock = NS(now=0.0)
        def wait(seconds):
            clock.now += seconds
            return False
        manager.closed = NS(is_set=lambda: False, wait=wait)
        fake_time = NS(time=lambda: 1000+clock.now, monotonic=lambda: clock.now)
        with patch('web_standalone_hosting.time', fake_time):
            updated = manual._retire_departed_host(coordinator, fixture.repo.data)
        self.assertGreaterEqual(clock.now, 20)
        self.assertEqual(updated['web_standalone']['state'], 'stopped')
        coordinator.web_control.request.assert_not_called()

    def test_temporarily_unresponsive_online_peer_is_never_treated_as_closed(self):
        fixture, manual, _, coordinator = self.node(proof=False)
        fixture.repo.data['members'][HOST]['online'] = True
        before = deepcopy(fixture.repo.data)
        self.assertEqual(manual._retire_departed_host(coordinator, fixture.repo.data), before)
        coordinator._control.assert_not_called()
        self.assertEqual(fixture.repo.writes, 0)

    def test_probe_contradicting_exit_marker_blocks_recovery(self):
        fixture, manual, _, coordinator = self.node()
        coordinator._control.return_value = dict(members=[dict(client_id=HOST, runtime=HOST_SESSION, online=True)])
        with self.assertRaisesRegex(ValueError, '다시 연결'):
            manual._retire_departed_host(coordinator, fixture.repo.data)
        self.assertEqual(fixture.repo.writes, 0)

    def test_changed_web_owner_cannot_be_erased_by_late_cleanup(self):
        fixture, manual, _, coordinator = self.node()
        stale = deepcopy(fixture.repo.data)
        def probe(_):
            fixture.repo.data['web_standalone'].update(owner=SOURCE, runtime=SOURCE_SESSION)
            return dict(members=[])
        coordinator._control.side_effect = probe
        with self.assertRaisesRegex(ValueError, '담당자가 바뀌었'):
            manual._retire_departed_host(coordinator, stale)
        self.assertEqual(fixture.repo.data['web_standalone']['owner'], SOURCE)
        self.assertEqual(fixture.repo.writes, 0)

    def test_dead_host_cleanup_never_clears_another_receivers_reservation(self):
        fixture, manual, _, coordinator = self.node()
        pending = dict(id='f'*32, owner=SOURCE, runtime=SOURCE_SESSION, expires=10**12)
        fixture.repo.data['web_operation'] = deepcopy(pending)
        updated = manual._retire_departed_host(coordinator, fixture.repo.data)
        self.assertEqual(updated['web_operation'], pending)

    def test_restarted_requester_can_clear_its_old_session_reservation(self):
        fixture, manual, _, coordinator = self.node()
        fixture.repo.data['web_operation'] = dict(id='f'*32, owner=SOURCE, runtime='9'*32, expires=10**12)
        previous_web = deepcopy(fixture.repo.data['web_standalone'])
        updated = manual._clear_abandoned_operation(coordinator, fixture.repo.data)
        self.assertNotIn('web_operation', updated)
        self.assertEqual(updated['web_standalone'], previous_web)

    def test_live_other_requesters_reservation_is_not_cleared(self):
        fixture, manual, _, coordinator = self.node(proof=False)
        fixture.repo.data['members'][HOST]['online'] = True
        fixture.repo.data['web_operation'] = dict(id='f'*32, owner=HOST, runtime=HOST_SESSION, expires=10**12)
        before = deepcopy(fixture.repo.data)
        self.assertEqual(manual._clear_abandoned_operation(coordinator, before), before)
        self.assertEqual(fixture.repo.writes, 0)

    def test_stopped_host_is_not_presented_as_an_active_split_web_host(self):
        import test_web_delegated_hosting as delegated
        fixture, manual, _, coordinator = self.node()
        test = delegated.DelegatedTests()
        test.setUp()
        self.addCleanup(test.clock.stop)
        relay, _, _, _ = test.node('source')
        relay.bridge.independent = True
        relay.observe(fixture.repo.data, coordinator)
        self.assertEqual(relay.role, '')


class ExitRecordTests(unittest.TestCase):
    def node(self):
        fixture = fixtures.StandaloneTests()
        _, manager, coordinator, _ = fixture.node(owner=SOURCE)
        fixture.repo.data['web_standalone'] = dict(protocol=1, id='e'*32, state='active',
                                                 owner=SOURCE, runtime=SOURCE_SESSION, **SCOPE)
        fixture.repo.data['web_operation'] = dict(id='f'*32, owner=SOURCE, runtime=SOURCE_SESSION)
        node = object.__new__(DiscordAuthority)
        node.client, node.runtime, node.scope = SOURCE, SOURCE_SESSION, SCOPE
        node.lock, node.record = coordinator.lock, coordinator.record
        node.policy = NS(snapshot=lambda: dict(authority_request=''), block_request=Mock())
        node.app = NS(web_service_manager=manager)
        manager.closed.set()
        manager.public.thread = None
        return fixture, node

    def test_confirmed_program_exit_clears_own_web_and_pending_request_together(self):
        fixture, node = self.node()
        with patch('web_standalone_hosting.read_json', return_value=dict(enabled=False)):
            node._release(stopped=True)
        self.assertEqual(fixture.repo.data['web_standalone']['state'], 'stopped')
        self.assertNotIn('web_operation', fixture.repo.data)
        self.assertEqual(fixture.repo.data['members'][SOURCE]['web_closed_runtime'], SOURCE_SESSION)
        self.assertFalse(fixture.repo.data['members'][SOURCE]['online'])

    def test_old_pc_exit_keeps_new_pc_web_and_reservation(self):
        fixture, node = self.node()
        fixture.repo.data['web_standalone'].update(owner=HOST, runtime=HOST_SESSION)
        fixture.repo.data['web_operation'].update(owner=HOST, runtime=HOST_SESSION)
        expected_web, expected_operation = deepcopy(fixture.repo.data['web_standalone']), deepcopy(fixture.repo.data['web_operation'])
        with patch('web_standalone_hosting.read_json', return_value=dict(enabled=False)):
            node._release(stopped=True)
        self.assertEqual(fixture.repo.data['web_standalone'], expected_web)
        self.assertEqual(fixture.repo.data['web_operation'], expected_operation)

    def test_bot_only_stop_does_not_claim_the_independent_web_was_closed(self):
        fixture, node = self.node()
        node.app.web_service_manager.closed.clear()
        node._release(stopped=True)
        self.assertEqual(fixture.repo.data['web_standalone']['state'], 'active')
        self.assertNotIn('web_closed_runtime', fixture.repo.data['members'][SOURCE])

    def test_pending_ddns_write_is_not_shutdown_proof(self):
        _, node = self.node()
        node.app.web_service_manager.ddns.status.return_value = dict(busy=True)
        with patch('web_standalone_hosting.read_json', return_value=dict(enabled=False)):
            self.assertFalse(closed_web_confirmed(node.app.web_service_manager))


if __name__ == '__main__':
    unittest.main()
