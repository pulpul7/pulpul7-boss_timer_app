"""In-memory first connection and concurrent web/presence regressions."""
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace as NS
import threading
import queue
import unittest
from unittest.mock import Mock, patch

import discord_authority as authority
import discord_connection_policy as policy_module
from discord_authority import AuthorityRecord, DiscordAuthority, AuthorityError
from discord_connection_policy import ConnectionPolicy, CONTROL_PROTOCOL


class MemoryPolicy(ConnectionPolicy):
    def __init__(self):
        self.data = dict(self._empty(), protocol=CONTROL_PROTOCOL, client_id='local',
                         runtime_id='runtime', authority_generation=1,
                         authority_request='join', authority_active=True,
                         authority_needs_sync=False, standby=False,
                         voice_requested=True, authority_until=1010)

    def _locked(self):
        return nullcontext()

    def snapshot(self, *, strict=False):
        return deepcopy(self.data)

    def _save(self, data):
        self.data = deepcopy(data)


class JoinCompletionTests(unittest.TestCase):
    def setUp(self):
        clock = NS(monotonic=lambda: 1000, time=lambda: 1000, sleep=Mock())
        for module in (authority, policy_module):
            timer = patch.object(module, 'time', clock)
            timer.start()
            self.addCleanup(timer.stop)
        self.node = object.__new__(DiscordAuthority)
        self.node.client, self.node.runtime = 'local', 'runtime'
        self.node.lock = threading.RLock()
        self.node.releasing = False
        self.node._assert_profile = Mock()
        self.node.policy = MemoryPolicy()
        self.node.record = NS(online_members=lambda _: {}, query_members=lambda _: [])
        self.node.app = NS(_append_debug_log=Mock())
        self.status = dict(online=True, runtime_id='runtime', application_id='123',
                           voice_connected=True, voice_channel_id='777', voice_authority_generation=1)
        self.record = dict(owner='local', owner_runtime='runtime', generation=1,
                           request='join', phase='active', channel='777', members={})

    def test_late_web_snapshot_cannot_demote_completed_voice_join(self):
        old_web_record = dict(self.record, phase='joining')
        self.node._observe(self.record, self.status)
        self.node._observe(old_web_record, self.status)
        local = self.node.policy.snapshot()
        self.assertTrue(local['authority_active'])
        self.assertTrue(ConnectionPolicy.has_authority(local, 'runtime'))
        self.assertFalse(local['standby'])

    def test_new_request_and_generation_still_enter_joining(self):
        self.node._observe(dict(self.record, phase='joining', request='new-join', generation=2), self.status)
        self.assertFalse(self.node.policy.snapshot()['authority_active'])
        self.assertEqual(self.node.policy.snapshot()['authority_request'], 'new-join')

    def test_new_runtime_cannot_inherit_completed_join(self):
        self.node._observe(dict(self.record, owner_runtime='new-runtime', phase='joining'),
                           dict(self.status, runtime_id='new-runtime'))
        self.assertFalse(self.node.policy.snapshot()['authority_active'])

    def test_release_and_blocked_request_are_still_respected(self):
        self.node._observe(dict(self.record, phase='requested'), self.status)
        self.assertFalse(self.node.policy.snapshot()['authority_active'])
        self.assertTrue(self.node.policy.snapshot()['standby'])
        self.node.policy.data.update(authority_active=True, blocked_request='join')
        self.node._observe(dict(self.record, phase='joining'), self.status)
        self.assertFalse(self.node.policy.snapshot()['authority_active'])
        self.assertFalse(self.node.policy.snapshot()['voice_requested'])

    def test_web_startup_observer_failure_cannot_cancel_voice_grant(self):
        self.node.app.web_service_manager = NS(delegation=NS(observe=Mock(side_effect=OSError('web starting'))))
        self.node._observe(self.record, self.status)
        self.assertTrue(self.node.policy.snapshot()['authority_active'])
        self.node.app._append_debug_log.assert_called_once()

    def incoming_node(self, failed_notice=False):
        node = self.node
        data = dict(schema=CONTROL_PROTOCOL, scope=dict(guild='100'), owner='', owner_runtime='',
                    generation=0, phase='idle', request='', receiver='', members={
                        'local': dict(runtime='runtime', online=True, application_id='123', protocol=CONTROL_PROTOCOL,
                                      presence_mode='event')})
        saved = [deepcopy(data), '0']
        def get(_):
            return deepcopy(saved[0]), saved[1], ''
        def put(_, value, **kwargs):
            self.assertEqual(kwargs['sha'], saved[1])
            saved[:] = [deepcopy(value), str(int(saved[1]) + 1)]
            return True, ''
        node.record = AuthorityRecord(get, put, dict(guild='100'))
        node.policy.data.update(authority_generation=0, authority_active=False, standby=True)
        node._status = Mock(return_value=self.status)
        node._control, node._progress, node._metadata = Mock(), Mock(), Mock()
        node._presence = lambda value, *_: value
        node._live_data = lambda value, probe=None: value
        node._ui = lambda callback, **kwargs: callback()
        node._cancel_incoming = Mock()
        node._finish = Mock()
        node.incoming_progress_token = 'progress'
        node.connection_notice_lock = threading.Lock()
        node.connection_notices = queue.Queue()
        node.connection_notice_running = False
        node.app.discord_start_web_together = False
        node.app.discord_bot_voice_channel_id = '777'
        node.app.web_service_manager = NS(status=Mock(side_effect=OSError('web status starting')))
        def notify(_, *, notice=''):
            if failed_notice and notice == 'completed':
                raise TimeoutError('notice delayed')
        node._notify = Mock(side_effect=notify)
        return node, saved

    def test_first_voice_join_finishes_even_while_optional_web_status_is_unavailable(self):
        node, saved = self.incoming_node()
        with patch.object(authority.threading, 'Thread'):
            node._incoming(False, None, '777', None, None)
        self.assertEqual(saved[0]['phase'], 'active')
        self.assertTrue(node.policy.snapshot()['authority_active'])
        node._cancel_incoming.assert_not_called()
        self.assertTrue(node._finish.call_args.args[2])
        node.app.web_service_manager.status.assert_not_called()

    def orphan_node(self):
        node, saved = self.incoming_node()
        saved[0].update(owner='local', owner_runtime='departed', owner_application_id='123',
                        owner_bot_user_id='123', phase='active', request='departed-request', generation=6)
        node._status.return_value = dict(self.status, voice_authority_generation=7)
        node._live_data = lambda value, probe=None: dict(value, members={})
        node.app._show_centered_messagebox = Mock(return_value=True)
        node.app.schedule_window = None
        node.app.root = None
        def control(action, **kwargs):
            if action == 'verify_absent':
                return dict(absent=kwargs.get('reset_orphan') is True)
            return dict(members=[])
        node._control.side_effect = control
        return node, saved

    def test_confirmed_leftover_voice_does_not_block_new_runtime_admission(self):
        node, saved = self.orphan_node()
        with patch.object(authority.threading, 'Thread'):
            node._incoming(False, None, '777', None, None)
        node._control.assert_any_call('verify_absent', bot_user_id='123', reset_orphan=True)
        self.assertEqual(saved[0]['owner_runtime'], 'runtime')
        self.assertEqual(saved[0]['phase'], 'active')
        self.assertTrue(node._finish.call_args.args[2])

    def test_declined_or_unverified_orphan_recovery_never_grants_authority(self):
        for approved, stopped in ((False, True), (True, False)):
            with self.subTest(approved=approved, stopped=stopped):
                node, saved = self.orphan_node()
                node.app._show_centered_messagebox.return_value = approved
                if not stopped:
                    node._control.side_effect = lambda action, **_: dict(absent=False, members=[])
                with patch.object(authority.threading, 'Thread'):
                    node._incoming(False, None, '777', None, None)
                self.assertEqual(saved[0]['owner_runtime'], 'departed')
                self.assertFalse(node._finish.call_args.args[2])

    def test_new_owner_or_returning_controller_cancels_old_confirmation(self):
        for changed_owner in (True, False):
            node, saved = self.orphan_node()
            if changed_owner:
                def approve(*_, **kwargs):
                    saved[0]['generation'] += 1
                    return True
                node.app._show_centered_messagebox.side_effect = approve
            else:
                node._live_data = Mock(side_effect=[dict(members={}), dict(members={'local':dict(runtime='departed')})])
            with patch.object(authority.threading, 'Thread'):
                node._incoming(False, None, '777', None, None)
            self.assertFalse(any(call.kwargs.get('reset_orphan') for call in node._control.call_args_list))
            self.assertFalse(node._finish.call_args.args[2])

    def test_unattended_remote_request_never_resets_leftover_voice_without_confirmation(self):
        node, _ = self.orphan_node()
        with patch.object(authority.threading, 'Thread'):
            node._incoming(True, None, '777', None, None)
        node.app._show_centered_messagebox.assert_not_called()
        self.assertFalse(any(call.kwargs.get('reset_orphan') for call in node._control.call_args_list))
        self.assertFalse(node._finish.call_args.args[2])

    def test_completed_notice_timeout_does_not_undo_verified_admission(self):
        node, saved = self.incoming_node(failed_notice=True)
        class ImmediateThread:
            def __init__(self, *, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        with patch.object(authority.threading, 'Thread', ImmediateThread):
            node._incoming(False, None, '777', None, None)
        self.assertEqual(saved[0]['phase'], 'active')
        self.assertTrue(node.policy.snapshot()['authority_active'])
        node._cancel_incoming.assert_not_called()
        self.assertTrue(node._finish.call_args.args[2])
        self.assertTrue(any('authority_connection_notice_failed notice=completed' in call.args[0]
                            for call in node.app._append_debug_log.call_args_list))

    def test_voice_popup_finishes_before_notices_and_notices_keep_their_order(self):
        node, saved = self.incoming_node()
        workers = []
        def deferred_thread(**kwargs):
            return NS(start=lambda: workers.append(kwargs['target']))
        with patch.object(authority.threading, 'Thread', side_effect=deferred_thread):
            node._incoming(False, None, '777', None, None)
        self.assertTrue(node._finish.call_args.args[2])
        node._notify.assert_not_called()
        self.assertEqual(len(workers), 1)
        self.assertEqual(saved[0]['phase'], 'active')
        workers[0]()
        self.assertEqual([call.kwargs['notice'] for call in node._notify.call_args_list], ['started', 'completed'])
        self.assertEqual(node._notify.call_args_list[0].args[0]['phase'], 'joining')
        self.assertFalse(node.connection_notice_running)

    def test_finished_admission_does_not_wait_for_duplicate_control_check(self):
        node, _ = self.incoming_node()
        steps = []
        node._progress = lambda role, token, step, detail, **kwargs: steps.append(step)
        original_change = node.record.change
        def commit(*args, **kwargs):
            self.assertEqual(steps[-1], 'confirm')  # Voice has already entered before this write.
            return original_change(*args, **kwargs)
        node.record.change = commit
        with patch.object(authority.threading, 'Thread'):
            node._incoming(False, None, '777', None, None)
        self.assertEqual([call.args[0] for call in node._control.call_args_list], ['probe'])
        self.assertEqual(steps[-2:], ['voice', 'confirm'])

    def test_verified_completion_notice_is_kept_after_immediate_release(self):
        node, _ = self.incoming_node()
        workers = []
        with patch.object(authority.threading, 'Thread', side_effect=lambda **kwargs:
                          NS(start=lambda: workers.append(kwargs['target']))):
            node._incoming(False, None, '777', None, None)
        node.policy.data.update(blocked_request=node.policy.data['authority_request'], voice_requested=False)
        workers[0]()
        self.assertEqual([call.kwargs['notice'] for call in node._notify.call_args_list], ['completed'])

    def test_restarted_bot_cannot_send_an_old_connection_notice(self):
        node, _ = self.incoming_node()
        workers = []
        with patch.object(authority.threading, 'Thread', side_effect=lambda **kwargs:
                          NS(start=lambda: workers.append(kwargs['target']))):
            node._incoming(False, None, '777', None, None)
        node.policy.data.update(runtime_id='new-runtime')
        workers[0]()
        node._notify.assert_not_called()

    def test_reusing_probe_result_still_rejects_an_unrecorded_sender(self):
        node = self.node
        node._control = Mock()
        probe = dict(members=[dict(client_id='other', online=True, sending=True, runtime='peer')])
        with self.assertRaises(AuthorityError):
            node._live_data(dict(owner='local', owner_runtime='runtime', members={}), probe)
        node._control.assert_not_called()


if __name__ == '__main__':
    unittest.main()
