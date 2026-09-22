"""No sound, synthesis, network, app launch or user settings in these tests."""
import io
import queue
import subprocess
import threading
import unittest
from unittest.mock import Mock

from notice_module.payload.notice_local_player import LocalPlayer, PLAYER_SOURCE
from notice_module.payload.notice_local_transport import LocalNoticeTransport


class FakePlayer:
    def __init__(self, path):
        self.state = "ready"
        self.pause_ok = True
        self.stop_ok = True
        self.play_ok = True
        self.calls = []

    def status(self):
        return self.state

    def play(self):
        self.calls.append("play")
        self.state = "playing"
        return self.play_ok

    def pause(self):
        self.calls.append("pause")
        if self.pause_ok:
            self.state = "paused"
        return self.pause_ok

    def stop(self):
        self.calls.append("stop")
        if self.stop_ok:
            self.state = "stopped"
        return self.stop_ok


class LocalTransportTests(unittest.TestCase):
    def setUp(self):
        self.synth = Mock()
        self.synth.synthesize.return_value = "fake-audio.mp3"
        self.factory = Mock(return_value=self.synth)
        self.player_factory = Mock(side_effect=FakePlayer)
        self.transport = LocalNoticeTransport(self.factory, player_factory=self.player_factory)
        self.addCleanup(self.transport.close)

    def ready(self):
        handle = self.transport.prepare("공지입니다.")
        handle.worker.join(timeout=2)
        self.assertFalse(handle.worker.is_alive())
        self.assertEqual(self.transport.status(handle), "ready")
        return handle

    def test_preparation_silent_eof_only_and_separate_cache(self):
        handle = self.ready()
        self.assertEqual(handle.player.calls, [])
        self.assertTrue(self.transport.play(handle))
        self.assertEqual(self.transport.status(handle), "playing")
        handle.player.state = "completed"
        self.assertEqual(self.transport.status(handle), "completed")
        self.assertTrue(self.transport.stop(handle))
        self.assertEqual(self.transport.status(handle), "stopped")
        self.factory.assert_called_once_with()

    def test_prepared_file_never_calls_synthesizer(self):
        lease = Mock(path='already-prepared.mp3')
        handle = self.transport.prepare_prepared(lease)
        handle.worker.join(timeout=2)
        self.assertEqual(self.transport.status(handle), 'ready')
        self.assertEqual(handle.player.calls, [])
        self.factory.assert_not_called()
        self.player_factory.assert_called_once_with(lease.path)
        self.assertTrue(self.transport.stop(handle))

    def test_overlapping_boss_leases_block_play_and_resume(self):
        handle = self.ready()
        self.transport.begin_boss("a")
        self.assertFalse(self.transport.play(handle))
        self.transport.end_boss("a")
        self.assertTrue(self.transport.play(handle))
        self.assertTrue(self.transport.begin_boss("a"))
        self.assertEqual(self.transport.status(handle), "paused")
        self.transport.begin_boss("b")
        self.transport.end_boss("a")
        self.assertFalse(self.transport.resume(handle))
        self.transport.end_boss("b")
        self.assertTrue(self.transport.resume(handle))

    def test_pause_failure_stops_only_owned_player(self):
        handle = self.ready()
        self.transport.play(handle)
        handle.player.pause_ok = False
        self.assertTrue(self.transport.begin_boss("boss"))
        self.assertEqual(handle.player.calls, ["play", "pause", "stop"])
        self.assertEqual(self.transport.status(handle), "stopped")

    def test_silence_failure_blocks_new_audio(self):
        handle = self.ready()
        self.transport.play(handle)
        handle.player.pause_ok = handle.player.stop_ok = False
        self.assertFalse(self.transport.begin_boss("boss"))
        self.transport.end_boss("boss")
        self.assertFalse(self.transport.resume(handle))
        with self.assertRaises(RuntimeError):
            self.transport.prepare("새 안내")
        handle.player.stop_ok = True

    def test_timed_out_play_cannot_start_after_gate_is_released(self):
        handle = self.ready()
        handle.player.play_ok = False
        self.assertFalse(self.transport.play(handle))
        self.assertEqual(handle.player.calls, ["play", "stop"])
        self.assertEqual(self.transport.status(handle), "stopped")

    def test_cancel_during_synthesis_never_opens_player(self):
        entered, finish = threading.Event(), threading.Event()
        def synthesize(text):
            entered.set()
            finish.wait(timeout=2)
            return "fake-audio.mp3"
        self.synth.synthesize.side_effect = synthesize
        handle = self.transport.prepare("생성 중 취소")
        self.assertTrue(entered.wait(timeout=2))
        self.assertTrue(self.transport.stop(handle))
        finish.set()
        handle.worker.join(timeout=2)
        self.player_factory.assert_not_called()
        self.synth.stop.assert_called_once()
        self.assertEqual(self.transport.status(handle), "stopped")

    def test_prepare_failure_is_silent_and_releases_synth(self):
        self.synth.synthesize.return_value = None
        handle = self.transport.prepare("실패")
        handle.worker.join(timeout=2)
        self.assertEqual(self.transport.status(handle), "failed")
        self.player_factory.assert_not_called()
        self.synth.stop.assert_called_once()

    def test_player_exception_cannot_release_silence_gate(self):
        handle = self.ready()
        self.transport.play(handle)
        handle.player.pause = Mock(side_effect=OSError("pause failed"))
        handle.player.stop = Mock(side_effect=OSError("stop failed"))
        self.assertFalse(self.transport.begin_boss("boss"))
        self.assertTrue(self.transport.fault)
        self.assertIn("stop failed", handle.error)
        handle.player.stop = Mock(return_value=True)

    def test_close_and_foreign_handles(self):
        handle = self.ready()
        self.assertTrue(self.transport.close())
        self.assertFalse(self.transport.play(handle))
        with self.assertRaises(RuntimeError):
            self.transport.prepare("종료 후")
        with self.assertRaises(ValueError):
            LocalNoticeTransport(self.factory).status(handle)


class LinePipe:
    def __init__(self):
        self.lines = queue.Queue()
        self.closed = False

    def __iter__(self):
        while True:
            line = self.lines.get(timeout=3)
            if line is None:
                return
            yield line

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = LinePipe()
        self.returncode = None
        self.stuck = False

    def poll(self):
        return self.returncode

    def terminate(self):
        if not self.stuck:
            self.returncode = 1
            self.stdout.lines.put(None)

    def kill(self):
        self.terminate()

    def wait(self, timeout):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


class PlayerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.process = FakeProcess()
        self.player = LocalPlayer("fake", spawn=lambda _: self.process)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.process.stuck = False
        self.player.stop()
        self.player.reader.join(timeout=2)

    def send_state(self, state):
        self.process.stdout.lines.put("0\t" + state + "\n")
        with self.player.condition:
            self.assertTrue(self.player.condition.wait_for(lambda: self.player.state == state, timeout=2))

    def test_exit_is_failure_not_completion(self):
        self.send_state("ready")
        self.send_state("playing")
        self.process.terminate()
        self.player.reader.join(timeout=2)
        self.assertEqual(self.player.status(), "failed")

    def test_only_explicit_completion_receipt_succeeds(self):
        self.send_state("ready")
        self.send_state("playing")
        self.send_state("completed")
        self.process.terminate()
        self.player.reader.join(timeout=2)
        self.assertEqual(self.player.status(), "completed")

    def test_missing_ack_does_not_report_pause_success(self):
        self.send_state("playing")
        self.assertFalse(self.player.command("pause", {"paused"}, timeout=0.01))
        self.assertEqual(self.process.stdin.getvalue(), "1\tpause\n")
        self.assertTrue(self.player.stop())

    def test_only_matching_command_receipt_is_accepted(self):
        self.send_state("playing")
        self.assertFalse(self.player.command("pause", {"paused"}, timeout=0.001))
        # Old acknowledgment cannot satisfy command 2.
        self.process.stdout.lines.put("1\tpaused\n")
        self.assertFalse(self.player.command("pause", {"paused"}, timeout=0.01))
        def reply():
            self.process.stdout.lines.put("3\tpaused\n")
        self.process.stdin.flush = reply
        self.assertTrue(self.player.command("pause", {"paused"}, timeout=1))

    def test_unconfirmed_process_stop_returns_false(self):
        self.process.stuck = True
        self.assertFalse(self.player.stop())

    def test_embedded_player_uses_real_eof_and_mutes_before_pause(self):
        self.assertIn("player.MediaEnded +=", PLAYER_SOURCE)
        self.assertNotIn("NaturalDuration", PLAYER_SOURCE)
        self.assertIn("player.IsMuted = true;", PLAYER_SOURCE)
        self.assertIn('if (state != "playing" && state != "paused") return;', PLAYER_SOURCE)


if __name__ == "__main__":
    unittest.main()
