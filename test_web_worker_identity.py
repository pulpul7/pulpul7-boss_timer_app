"""Frozen launcher/worker PID regression checks without processes or servers."""
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from discord_bot_process_recovery import BotProcess
from web_service_common import PROTOCOL, worker_paths, write_json
from web_service_manager import WebServiceManager
from web_worker_identity import SpawnedWorkerIdentity

EXE = r'C:\BossTimer\boss_timer_gui.exe'
RUN = 'a' * 32


class WorkerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.process = Mock(pid=100)
        self.process.poll.return_value = None
        self.records = {100: BotProcess(100, 90, EXE, 1000),
                        200: BotProcess(200, 100, EXE, 1010)}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('web_worker_identity.os', NS(name='nt')))
        self.stack.enter_context(patch('web_worker_identity._kernel', return_value=Mock()))
        self.inspect = self.stack.enter_context(patch('web_worker_identity._inspect',
            side_effect=lambda _kernel, pid, parent_pid=0: self.records.get(pid)))
        self.snapshot = self.stack.enter_context(patch('web_worker_identity._snapshot',
            side_effect=lambda _: {p: (v.parent_pid, 'boss_timer_gui.exe') for p, v in self.records.items()}))
        self.identity = SpawnedWorkerIdentity(self.process, EXE, frozen=True)

    def test_direct_pid_keeps_source_and_reattached_behavior(self):
        self.assertTrue(SpawnedWorkerIdentity(self.process, EXE).matches(100))
        self.inspect.assert_not_called()

    def test_frozen_same_executable_child_is_accepted(self):
        self.assertTrue(self.identity.matches(200))
        self.assertEqual(self.identity.worker.pid, 200)

    def test_non_frozen_pid_mismatch_remains_rejected(self):
        self.assertFalse(SpawnedWorkerIdentity(self.process, EXE).matches(200))
        self.inspect.assert_not_called()

    def test_unrelated_process_with_same_executable_is_rejected(self):
        self.records[200] = replace(self.records[200], parent_pid=999)
        self.assertFalse(self.identity.matches(200))

    def test_direct_child_with_different_executable_is_rejected(self):
        self.records[200] = replace(self.records[200], path=r'C:\Other\boss_timer_gui.exe')
        self.assertFalse(self.identity.matches(200))

    def test_launcher_must_match_expected_executable(self):
        self.records[100] = replace(self.records[100], path=r'C:\Other\app.exe')
        self.assertFalse(self.identity.matches(200))

    def test_child_created_before_launcher_is_rejected(self):
        self.records[200] = replace(self.records[200], created=900)
        self.assertFalse(self.identity.matches(200))

    def test_exited_launcher_cannot_approve_a_worker(self):
        self.process.poll.return_value = 0
        self.assertFalse(self.identity.matches(200))
        self.inspect.assert_not_called()

    def test_reused_child_or_launcher_pid_is_rejected(self):
        self.assertTrue(self.identity.matches(200))
        original = dict(self.records)
        self.records[200] = replace(self.records[200], created=1100)
        self.assertFalse(self.identity.matches(200))
        self.records = dict(original)
        self.records[100] = replace(self.records[100], created=1100)
        self.assertFalse(self.identity.matches(200))

    def test_verified_child_avoids_repeated_process_tree_scans(self):
        self.assertTrue(self.identity.matches(200))
        self.assertTrue(self.identity.matches(200))
        self.snapshot.assert_called_once()
        self.records[201] = BotProcess(201, 100, EXE, 1020)
        self.assertFalse(self.identity.matches(201))

    def test_missing_inspection_permission_fails_closed(self):
        self.inspect.side_effect = OSError('denied')
        self.assertFalse(self.identity.matches(200))

    def test_pid_reuse_during_initial_tree_inspection_is_rejected(self):
        worker = self.records[200]
        self.inspect.side_effect = [self.records[100], worker, replace(worker, created=1200)]
        self.assertFalse(self.identity.matches(200))

    def test_invalid_pid_types_are_rejected(self):
        for pid in (None, True, -1, 0, '200', 200.0):
            self.assertFalse(self.identity.matches(pid))

    def manager(self):
        temp = tempfile.TemporaryDirectory(prefix='boss-worker-identity-unit-')
        self.addCleanup(temp.cleanup)
        manager = WebServiceManager(Path(temp.name))
        manager.root.mkdir(exist_ok=True)
        def close():
            manager.process = None
            manager.close()
        self.addCleanup(close)
        manager._log = Mock()
        manager.public.send = Mock()
        manager._fail = Mock()
        manager.process = self.process
        manager.worker_identity = self.identity
        manager.process_generation = manager.generation
        manager.run_id = RUN
        manager.last_heartbeat_at = time.monotonic() - 21
        manager.view.update(state='starting')
        return manager

    def state(self, manager, **extra):
        state = dict(run_id=RUN, pid=200, protocol=PROTOCOL, port=8765,
                     state='running', heartbeat=1, https_state='running')
        state.update(extra)
        write_json(worker_paths(manager.root, RUN)[0], state)

    def test_ready_child_prevents_false_startup_timeout_and_is_not_restarted(self):
        manager = self.manager()
        self.state(manager)
        manager._monitor()
        self.assertEqual(manager.status()['state'], 'running')
        self.assertEqual(manager.status()['https_state'], 'running')
        self.assertEqual(manager.last_heartbeat, 1)
        manager._fail.assert_not_called()
        self.process.terminate.assert_not_called()

    def test_session_protocol_and_port_checks_still_apply(self):
        manager = self.manager()
        for extra in (dict(run_id='b'*32), dict(protocol=PROTOCOL+1), dict(port=8767)):
            self.state(manager, **extra)
            self.assertIsNone(manager._read_worker_state())
        self.inspect.assert_not_called()

    def test_unrelated_pid_still_times_out_instead_of_becoming_ready(self):
        manager = self.manager()
        self.records[200] = replace(self.records[200], parent_pid=999)
        self.state(manager)
        manager._monitor()
        manager._fail.assert_called_once_with('worker_timeout')


if __name__ == '__main__':
    unittest.main()
