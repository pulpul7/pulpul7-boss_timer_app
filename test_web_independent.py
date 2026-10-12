"""Independent web/voice ownership and consent tests, entirely in memory."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace as NS
import threading
import time
import unittest
from unittest.mock import Mock, patch

from discord_authority import AuthorityRecord, DiscordAuthority
from web_standalone_hosting import standalone_info
from web_independent_controller import IndependentWebController
import test_web_standalone_hosting as standalone_fixtures
import test_web_delegated_hosting as delegated_fixtures
from test_web_delegated_hosting import (
    HOST, HOST_SESSION, SOURCE, SOURCE_SESSION, SCOPE, initial_record, public_snapshot,
)


class IndependentTests(unittest.TestCase):
    def web_node(self):
        fixture = standalone_fixtures.StandaloneTests()
        manual, manager, coordinator, public = fixture.node()
        manual.bridge.independent = True
        manual.app.root = manual.app.schedule_window = None
        manual.app._show_centered_messagebox = Mock(return_value=True)
        coordinator._ui = lambda callback, **_kwargs: callback()
        fixture.repo.data['web_standalone'] = dict(protocol=1, id='e'*32, state='active',
                                                  owner=HOST, runtime=HOST_SESSION, **SCOPE)
        manual.observe(fixture.repo.data, coordinator)
        return fixture, manual, manager, coordinator, public

    def run_web(self, manual, approved=False):
        manual.busy = True
        with patch('web_direct_hosting.check_local_https'), \
             patch('web_schedule_snapshot.collect_snapshot', return_value=public_snapshot()):
            manual._start_worker(force=False, approved=approved)

    def test_web_button_decline_keeps_peer_and_never_reserves_or_stops_web(self):
        fixture, manual, manager, coordinator, _ = self.web_node()
        before = deepcopy(fixture.repo.data)
        manual.app._show_centered_messagebox.return_value = False
        self.run_web(manual)
        self.assertEqual(fixture.repo.data, before)
        fixture.repo.put.assert_not_called()
        coordinator.web_control.request.assert_not_called()
        manager.offer_snapshot.assert_not_called()

    def test_approved_parallel_web_skips_prompt_and_runs_during_voice_join(self):
        fixture, manual, manager, coordinator, _ = self.web_node()
        fixture.repo.data.update(phase='joining', owner=SOURCE, owner_runtime=SOURCE_SESSION)
        voice = AuthorityRecord.owner_stamp(fixture.repo.data)
        self.run_web(manual, approved=True)
        manual.app._show_centered_messagebox.assert_not_called()
        coordinator.web_control.request.assert_called_once()
        self.assertEqual(standalone_info(fixture.repo.data)['owner'], SOURCE)
        self.assertEqual(AuthorityRecord.owner_stamp(fixture.repo.data), voice)
        manager.offer_snapshot.assert_called_once()

    def test_voice_generation_change_during_normal_web_does_not_cancel_it(self):
        fixture, manual, manager, coordinator, _ = self.web_node()
        def stopped(_payload, done):
            fixture.repo.data.update(generation=999, phase='joining')
            fixture.repo.data['web_standalone']['state'] = 'stopped'
            done(True, 'stopped')
        coordinator.web_control.request.side_effect = stopped
        self.run_web(manual, approved=True)
        self.assertEqual(standalone_info(fixture.repo.data)['owner'], SOURCE)
        manager.public.configure.assert_called_once()

    def test_voice_handover_and_release_preserve_independent_web_and_pending_operation(self):
        fixture, manual, _, coordinator, _ = self.web_node()
        web = deepcopy(fixture.repo.data['web_standalone'])
        pending = dict(id='f'*32, owner=SOURCE, runtime=SOURCE_SESSION, expires=time.time()+100)
        fixture.repo.data['web_operation'] = deepcopy(pending)
        changed = coordinator.record.request(SOURCE, SOURCE_SESSION, '777', independent_web=True)
        self.assertEqual(changed['web_standalone'], web)
        self.assertEqual(changed['web_operation'], pending)
        self.assertNotIn('web_delegate', changed)

    def test_web_stop_cannot_be_confused_with_a_newer_web_operation(self):
        fixture, manual, _, coordinator, _ = self.web_node()
        fixture.repo.data['web_operation'] = dict(id='f'*32, owner=SOURCE,
                                                  runtime=SOURCE_SESSION, expires=time.time()+100)
        with self.assertRaises(ValueError):
            manual._check_operation(fixture.repo.data, '9'*32, coordinator)

    def test_normal_web_publication_failure_restores_previous_host_without_changing_voice(self):
        fixture, manual, manager, coordinator, public = self.web_node()
        voice = AuthorityRecord.owner_stamp(fixture.repo.data)
        self.run_web(manual, approved=True)
        web = standalone_info(fixture.repo.data)
        self.assertEqual(web['backup']['owner'], HOST)
        manager.emergency_stop.side_effect = lambda **_kw: public.update(state='off', busy=False)
        with patch('web_standalone_hosting.read_json', return_value=dict(enabled=False)):
            manual._restore_previous(coordinator, web['id'], web['backup'])
        self.assertEqual(standalone_info(fixture.repo.data)['owner'], HOST)
        self.assertTrue(standalone_info(fixture.repo.data)['restore_after_failure'])
        self.assertEqual(AuthorityRecord.owner_stamp(fixture.repo.data), voice)

    def test_late_normal_web_failure_does_not_stop_a_newer_pending_web_attempt(self):
        fixture, manual, manager, coordinator, _ = self.web_node()
        self.run_web(manual, approved=True)
        web = standalone_info(fixture.repo.data)
        fixture.repo.data['web_operation'] = dict(id='9'*32, owner=HOST, runtime=HOST_SESSION)
        manager.emergency_stop.reset_mock()
        manual._restore_previous(coordinator, web['id'], web['backup'])
        manager.emergency_stop.assert_not_called()
        self.assertEqual(standalone_info(fixture.repo.data)['owner'], SOURCE)

    def test_successful_normal_publication_clears_backup_without_changing_voice(self):
        fixture, manual, manager, coordinator, _ = self.web_node()
        self.run_web(manual, approved=True)
        voice = AuthorityRecord.owner_stamp(fixture.repo.data)
        web = standalone_info(fixture.repo.data)
        manual._confirm_publication(coordinator, web['id'])
        self.assertNotIn('backup', standalone_info(fixture.repo.data))
        self.assertEqual(AuthorityRecord.owner_stamp(fixture.repo.data), voice)

    def test_fresh_dns_confirmation_prevents_timeout_rollback_and_duplicate_confirmation(self):
        fixture, manual, manager, coordinator, _ = self.web_node()
        self.run_web(manual, approved=True)
        fixture.repo.data['web_standalone']['trial_deadline'] = time.time()-1
        manual.observe(fixture.repo.data, coordinator)
        manager.ddns.status.return_value.update(state='ready', last_updated=datetime.now(timezone.utc).isoformat())
        with patch('web_standalone_hosting.threading.Thread') as thread:
            manual.tick()
            manual.tick()
            thread.assert_called_once()
        manager.emergency_stop.assert_not_called()
        self.assertEqual(standalone_info(fixture.repo.data)['owner'], SOURCE)

    def test_independent_controller_never_uses_voice_authority_to_publish(self):
        controller = object.__new__(IndependentWebController)
        controller.closed = threading.Event()
        controller.manager = NS(closed=threading.Event(), _log=Mock())
        controller.standalone = NS(tick=Mock(), suppresses_normal=lambda: True, publish_shared=Mock())
        controller.delegation = NS(tick=Mock(), take_over_when_configured=Mock())
        controller.wake = NS(wait=lambda _: controller.closed.set(), clear=Mock())
        controller._loop()
        controller.standalone.tick.assert_called_once()
        controller.delegation.take_over_when_configured.assert_not_called()

    def test_split_schedule_relay_remains_independent_of_web_acquisition(self):
        fixture = delegated_fixtures.DelegatedTests()
        fixture.setUp()
        self.addCleanup(fixture.clock.stop)
        relay, manager, coordinator, _ = fixture.node('source')
        relay.bridge.independent = True
        data = deepcopy(fixture.repo.data)
        data.pop('web_delegate')
        data['web_standalone'] = dict(protocol=1, id='e'*32, state='active', owner=HOST,
                                      runtime=HOST_SESSION, **SCOPE)
        relay.observe(data, coordinator)
        self.assertEqual(relay.role, 'source')
        relay.take_over_when_configured(True)
        self.assertFalse(relay.auto_return_started)
        relay.offer_local(public_snapshot('changed schedule'))
        relay.sync_now()
        relay._step()
        self.assertTrue(fixture.repo.put.call_args.args[0].startswith('data/web/delegated/'))
        self.assertEqual(data['web_standalone']['owner'], HOST)

    def voice_node(self, consent=True, together=True):
        node = object.__new__(DiscordAuthority)
        node.client, node.runtime, node.scope = SOURCE, SOURCE_SESSION, SCOPE
        node.lock = threading.RLock()
        node.incoming_progress_token = 'local-request'
        node._status = lambda: dict(online=True, runtime_id=SOURCE_SESSION)
        node._control = node._progress = node._metadata = node._finish = Mock()
        node._presence = lambda data, *_: data
        node._live_data = lambda data: data
        node._ui = lambda callback, **_kw: callback()
        node.policy = NS(snapshot=lambda: {})
        node.app = NS(discord_start_web_together=together, schedule_window=None, root=None,
            discord_bot_voice_channel_id='777', _show_centered_messagebox=Mock(return_value=consent),
            _start_web_runtime=Mock(), _append_debug_log=Mock())
        node.record = NS(read=lambda: (initial_record(), 'sha'), request=Mock(side_effect=RuntimeError('end simulation')))
        return node

    def test_voice_decline_does_not_start_either_job(self):
        node = self.voice_node(consent=False)
        node._incoming(False, None, '777', None, None)
        node.record.request.assert_not_called()
        node.app._start_web_runtime.assert_not_called()

    def test_combined_consent_launches_web_before_waiting_for_voice_handover(self):
        node = self.voice_node()
        order = []
        node.app._start_web_runtime.side_effect = lambda **_kw: order.append('web')
        def voice(*_args, **_kw):
            order.append('voice')
            raise RuntimeError('end simulation')
        node.record.request.side_effect = voice
        node._incoming(False, None, '777', None, None)
        self.assertEqual(order, ['web', 'voice'])
        node.app._show_centered_messagebox.assert_called_once()
        self.assertIn('웹 동시 실행', node.app._show_centered_messagebox.call_args.args[2])

    def test_unchecked_option_starts_only_voice(self):
        node = self.voice_node(together=False)
        node._incoming(False, None, '777', None, None)
        node.record.request.assert_called_once()
        node.app._start_web_runtime.assert_not_called()

    def test_optional_web_start_error_cannot_cancel_voice_start(self):
        node = self.voice_node()
        node.app._start_web_runtime.side_effect = ValueError('web configuration missing')
        node._incoming(False, None, '777', None, None)
        node.record.request.assert_called_once()
        node.app._append_debug_log.assert_called_once()


if __name__ == '__main__':
    unittest.main()
