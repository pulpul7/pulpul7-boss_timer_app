"""No real providers/processes: runtime ACK, delayed rollback and confirmation."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from web_publication_confirmation import address_confirmed
from web_service_manager import WebServiceManager
import test_web_standalone_hosting as standalone_fixtures
import test_web_return_recovery as return_fixtures
import test_web_delegated_hosting as delegated_fixtures
from test_web_delegated_hosting import HOST, HOST_SESSION, SOURCE, SOURCE_SESSION, SCOPE


def ready():
    return dict(configured=True, enabled=True, state='ready', busy=False,
                last_updated=datetime.fromtimestamp(time.time(), timezone.utc).isoformat())


class AddressProofTests(unittest.TestCase):
    def test_successful_provider_ack_includes_dns_pending(self):
        for state in ('ready', 'dns_pending'):
            self.assertTrue(address_confirmed(dict(ready(),state=state), time.time()-10))

    def test_busy_error_unconfigured_and_disabled_do_not_prove_publication(self):
        for extra in (dict(busy=True),dict(state='error'),dict(configured=False),dict(enabled=False)):
            self.assertFalse(address_confirmed(dict(ready(),**extra),time.time()-10))

    def test_old_ack_and_invalid_timestamps_are_rejected(self):
        self.assertFalse(address_confirmed(ready(),time.time()+100))
        for value in ('broken',None,123):
            self.assertFalse(address_confirmed(dict(ready(),last_updated=value),time.time()-10))
        for value in (None,True,float('nan'),float('inf'),'123'):
            self.assertFalse(address_confirmed(ready(),value))

    def test_runtime_reader_never_uses_stale_gui_ack_when_worker_reply_missing(self):
        with tempfile.TemporaryDirectory(prefix='boss-publication-proof-unit-') as temp:
            manager = WebServiceManager(Path(temp))
            manager.ddns.status = Mock(return_value=ready())
            manager.process = Mock()
            manager._read_worker_state = Mock(return_value=None)
            self.assertFalse(address_confirmed(manager.publication_ddns_status(),time.time()-10))
            manager._read_worker_state.return_value = dict(ddns_status=dict(configured=False,state='unconfigured'))
            self.assertFalse(manager.publication_ddns_status()['configured'])
            # UI configuration may still show saved credentials while the worker reloads.
            self.assertTrue(manager.ddns_status()['configured'])
            manager.process = None
            manager.close()


class StandaloneConfirmationTests(unittest.TestCase):
    node = standalone_fixtures.StandaloneTests.node

    def trial(self):
        manual,manager,coordinator,public = self.node()
        backup = dict(protocol=1,id='e'*32,state='active',owner=HOST,runtime=HOST_SESSION,**SCOPE)
        operation = 'f'*32
        self.repo.data['web_standalone'] = dict(protocol=1,id=operation,state='active',
            owner=SOURCE,runtime=SOURCE_SESSION,started_at=time.time()-300,
            trial_deadline=time.time()-180,backup=backup,**SCOPE)
        manual.observe(self.repo.data,coordinator)
        manager.ddns.status.return_value = dict(state='paused',busy=False,last_updated=None)
        manager.publication_ddns_status = Mock(return_value=ready())
        return manual,manager,coordinator,public,operation,backup

    def test_expired_trial_with_worker_ack_confirms_instead_of_rolling_back(self):
        manual,manager,coordinator,_,operation,_ = self.trial()
        with patch('web_standalone_hosting.threading.Thread',
                   side_effect=lambda target,args=(),**_:NS(start=lambda:target(*args))):
            manual.tick()
        self.assertNotIn('backup',self.repo.data['web_standalone'])
        self.assertEqual(self.repo.data['web_standalone']['owner'],SOURCE)
        self.assertEqual(manual.trial_verified_web,operation)
        manager.emergency_stop.assert_not_called()

    def test_pending_address_is_not_displayed_as_final_success(self):
        manual,manager,_,_,_,_ = self.trial()
        self.repo.data['web_standalone']['trial_deadline'] = time.time()+100
        manual.observe(self.repo.data,manual.app.discord_handover)
        manager.publication_ddns_status.return_value = dict(ready(),state='updating',last_updated=None)
        manual.tick()
        self.assertEqual(manual.stage,'confirming')
        manager.emergency_stop.assert_not_called()

    def test_completed_confirmation_cancels_late_restore_before_local_stop(self):
        manual,manager,coordinator,_,operation,backup = self.trial()
        manual._confirm_publication(coordinator,operation)
        manual._restore_previous(coordinator,operation,backup)
        manager.emergency_stop.assert_not_called()
        self.assertEqual(self.repo.data['web_standalone']['owner'],SOURCE)

    def test_now_healthy_trial_is_rechecked_before_delayed_restore(self):
        manual,manager,coordinator,_,operation,backup = self.trial()
        manual._restore_previous(coordinator,operation,backup)
        manager.emergency_stop.assert_not_called()
        self.assertNotIn('backup',self.repo.data['web_standalone'])

    def test_verified_healthy_web_survives_one_missing_runtime_ddns_reply(self):
        manual,manager,coordinator,_,operation,backup = self.trial()
        manual.trial_verified_web = operation
        manager.publication_ddns_status.return_value = dict(state='waiting',configured=False)
        manual._restore_previous(coordinator,operation,backup)
        manager.emergency_stop.assert_not_called()
        self.assertNotIn('backup',self.repo.data['web_standalone'])

    def test_offline_previous_pc_cannot_disable_current_site_before_restore(self):
        manual,manager,coordinator,_,operation,backup = self.trial()
        manager.publication_ddns_status.return_value = dict(state='waiting',configured=False)
        self.repo.data['members'][backup['owner']]['online'] = False
        before = deepcopy(self.repo.data)
        manual._restore_previous(coordinator,operation,backup)
        manual._restore_previous(coordinator,operation,backup)
        manager.emergency_stop.assert_not_called()
        manager.set_authority.assert_not_called()
        manager._flush_authority.assert_not_called()
        self.assertEqual(self.repo.data,before)
        skipped = [c for c in manager._log.call_args_list if c.args[0]=='web_previous_host_restore_skipped']
        self.assertEqual(len(skipped),1)
        self.assertEqual(skipped[0].kwargs['reason'],'offline')
        self.assertNotEqual(manual.stage,'running')

    def test_restarted_previous_pc_is_not_the_original_restore_session(self):
        manual,manager,coordinator,_,operation,backup = self.trial()
        manager.publication_ddns_status.return_value = dict(state='waiting',configured=False)
        self.repo.data['members'][backup['owner']].update(online=True,runtime='9'*32)
        manual._restore_previous(coordinator,operation,backup)
        manager.emergency_stop.assert_not_called()
        manager._log.assert_any_call('web_previous_host_restore_skipped',request=operation,reason='session_changed')

    def test_previous_pc_can_be_restored_after_it_becomes_available(self):
        manual,manager,coordinator,public,operation,backup = self.trial()
        manager.publication_ddns_status.return_value = dict(state='waiting',configured=False)
        member = self.repo.data['members'][backup['owner']]
        member['online'] = False
        manual._restore_previous(coordinator,operation,backup)
        member['online'] = True
        manager.emergency_stop.side_effect = lambda **_: public.update(state='off')
        with patch('web_standalone_hosting.read_json',return_value=dict(enabled=False)):
            manual._restore_previous(coordinator,operation,backup)
        manager.emergency_stop.assert_called_once_with(standby=True)
        self.assertEqual(self.repo.data['web_standalone']['owner'],backup['owner'])
        self.assertIsNone(manual.restore_unavailable)

    def test_confirmation_cas_and_queued_restore_cannot_race_local_stop(self):
        manual,manager,coordinator,_,operation,backup = self.trial()
        entered,release = threading.Event(),threading.Event()
        def commit(*args,**kwargs):
            entered.set()
            if not release.wait(2):
                raise RuntimeError('fixture timeout')
            return self.repo._put(*args,**kwargs)
        self.repo.put.side_effect = commit
        confirming = threading.Thread(target=manual._confirm_publication,args=(coordinator,operation))
        restoring = threading.Thread(target=manual._restore_previous,args=(coordinator,operation,backup))
        confirming.start()
        try:
            self.assertTrue(entered.wait(1))
            restoring.start()
        finally:
            release.set()
            confirming.join(2)
            if restoring.ident is not None: restoring.join(2)
        self.assertFalse(confirming.is_alive())
        self.assertFalse(restoring.is_alive())
        manager.emergency_stop.assert_not_called()
        self.assertNotIn('backup',self.repo.data['web_standalone'])

    def test_verified_trial_does_not_expire_during_control_record_retry(self):
        manual,manager,coordinator,_,operation,_ = self.trial()
        manual.confirm_attempts = 3  # Shared record retry exhausted; do not spawn threads.
        manual.tick()
        self.assertEqual(manual.trial_verified_web,operation)
        manager.publication_ddns_status.return_value = dict(state='waiting',configured=False)
        manual.tick()
        manager.emergency_stop.assert_not_called()
        self.assertEqual(manual.stage,'running')


class ReturnConfirmationTests(unittest.TestCase):
    setUp = delegated_fixtures.DelegatedTests.setUp
    node = delegated_fixtures.DelegatedTests.node
    recovery_node = return_fixtures.ReturnTests.recovery_node
    begin_trial = return_fixtures.ReturnTests.begin_trial

    def test_old_gui_ddns_cannot_override_healthy_worker_after_deadline(self):
        relay,manager,coordinator,_,recovery = self.begin_trial()
        manager.authority_ready = True
        self.repo.data['web_return_pending'].update(started_at=time.time()-300,deadline=time.time()-180)
        recovery.observe(self.repo.data)
        manager.ddns.status.return_value = dict(state='paused',busy=False,last_updated=None)
        manager.publication_ddns_status = Mock(return_value=ready())
        recovery.monitor(relay.bridge.context[0],coordinator)
        self.assertNotIn('web_return_pending',self.repo.data)
        manager.emergency_stop.assert_not_called()

    def test_healthy_trial_clears_a_previous_failure_without_rollback(self):
        relay,manager,coordinator,_,recovery = self.begin_trial()
        manager.authority_ready = True
        key = relay.bridge.context[0]
        pending = deepcopy(self.repo.data['web_return_pending'])
        recovery.failed = (key,coordinator,pending)
        recovery.monitor(key,coordinator)
        self.assertNotIn('web_return_pending',self.repo.data)
        manager.emergency_stop.assert_not_called()

    def test_worker_ddns_error_triggers_recovery_even_when_gui_service_is_idle(self):
        relay,manager,coordinator,_,recovery = self.begin_trial()
        manager.authority_ready = True
        manager.ddns.active = False
        manager.publication_ddns_status = Mock(return_value=dict(state='error',enabled=True))
        with patch('web_return_recovery.read_json',return_value=dict(enabled=False)):
            recovery.monitor(relay.bridge.context[0],coordinator)
        self.assertEqual(self.repo.data['web_delegate']['host'],HOST)
        manager.emergency_stop.assert_called_once()

    def test_prior_provider_proof_survives_missing_reply_during_cas_retry(self):
        relay,manager,coordinator,_,recovery = self.begin_trial()
        manager.authority_ready = True
        key = relay.bridge.context[0]
        pending = deepcopy(self.repo.data['web_return_pending'])
        recovery.verified_trial = pending['id']
        recovery.failed = (key,coordinator,pending)
        manager.publication_ddns_status = Mock(return_value=dict(state='waiting',configured=False))
        recovery.monitor(key,coordinator)
        self.assertNotIn('web_return_pending',self.repo.data)
        manager.emergency_stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
