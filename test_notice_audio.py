"""Fake output only; never creates audio, connects Discord or changes user data."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from notice_module.payload.notice_management import NoticeStore, KST
from notice_module.payload.notice_audio import NoticeAudioController


class FakeTransport:
    notice_audio_protocol = 1

    def __init__(self):
        self.states = {}
        self.calls = []
        self.allow_pause = True
        self.allow_stop = True
        self.gate_busy = False

    def prepare(self, text):
        handle = len(self.states)
        self.states[handle] = "preparing"
        self.calls.append(("prepare", handle))
        return handle

    def status(self, handle):
        return self.states[handle]

    def play(self, handle):
        self.calls.append(("play", handle))
        if self.gate_busy:
            return False
        self.states[handle] = "playing"
        return True

    def resume(self, handle):
        self.calls.append(("resume", handle))
        if self.gate_busy:
            return False
        self.states[handle] = "playing"
        return True

    def pause(self, handle):
        self.calls.append(("pause", handle))
        if self.allow_pause:
            self.states[handle] = "paused"
        return self.allow_pause

    def stop(self, handle):
        self.calls.append(("stop", handle))
        if self.allow_stop:
            self.states[handle] = "stopped"
        return self.allow_stop


class AudioTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-notice-audio-unit-")
        self.addCleanup(temp.cleanup)
        self.now = datetime(2026, 9, 10, 18, tzinfo=KST)
        self.seconds = 0.0
        self.store = NoticeStore(Path(temp.name), "odin9", clock=lambda: self.now)
        settings = self.store.snapshot()["settings"]
        settings["output_enabled"] = True
        self.store.configure(settings)
        self.store.register({"id": "notice", "title": "일반 공지", "tts_text": "공지입니다.",
                             "valid_from": self.now, "valid_until": self.now + timedelta(hours=1)})
        self.transport = FakeTransport()
        self.controller = NoticeAudioController(self.store, self.transport, clock=lambda: self.seconds)
        self.addCleanup(self.controller.close)

    def start_playing(self):
        self.controller.step(boss_busy=False)
        handle = self.controller.active["handle"]
        self.transport.states[handle] = "ready"
        self.controller.step(boss_busy=False)
        return handle

    def test_prepare_and_start_do_not_count_as_completion(self):
        handle = self.start_playing()
        self.assertEqual(self.transport.status(handle), "playing")
        self.assertIsNone(self.store.snapshot()["events"]["notice"]["last_delivery"])
        self.transport.states[handle] = "completed"
        self.controller.step(boss_busy=False)
        self.assertIsNotNone(self.store.snapshot()["events"]["notice"]["last_delivery"])
        self.assertIsNone(self.controller.active)
        self.controller.step(boss_busy=False)
        self.assertEqual(sum(call[0] == "prepare" for call in self.transport.calls), 1)

    def test_missing_prepared_audio_never_synthesizes_at_playback(self):
        pool = Mock()
        pool.acquire.return_value = None
        self.controller.prepared_audio = pool
        self.controller.step(boss_busy=False)
        self.assertEqual(self.transport.calls, [])
        self.assertIsNone(self.controller.active)
        self.assertEqual(self.controller.state, '사전 음성 준비 대기')

    def test_prepared_audio_path_keeps_delivery_and_boss_checks(self):
        pool = Mock()
        lease = pool.acquire.return_value
        self.transport.prepare_prepared = Mock(return_value=123)
        self.transport.states[123] = 'ready'
        self.controller.prepared_audio = pool
        self.controller.step(boss_busy=True)
        pool.acquire.assert_not_called()
        self.controller.step(boss_busy=False)
        self.transport.prepare_prepared.assert_called_once_with(lease)
        self.assertEqual(self.transport.calls, [])
        self.assertIsNone(self.store.snapshot()['events']['notice']['last_delivery'])
        self.controller.step(boss_busy=False)
        self.assertIn(('play', 123), self.transport.calls)

    def test_pending_schedule_change_stops_prepared_handle_immediately(self):
        self.start_playing()
        pool = Mock()
        pool.current.return_value = False
        self.controller.prepared_audio = pool
        self.controller.step(boss_busy=False)
        self.assertIsNone(self.controller.active)
        self.assertIsNone(self.store.snapshot()['events']['notice']['last_delivery'])

    def test_boss_gate_prevents_start_and_synchronous_interrupt_pauses(self):
        self.controller.step(boss_busy=True)
        self.assertEqual(self.transport.calls, [])
        handle = self.start_playing()
        self.assertTrue(self.controller.interrupt_for_boss())
        self.assertEqual(self.transport.status(handle), "paused")
        self.controller.step(boss_busy=True)
        self.assertNotIn(("resume", handle), self.transport.calls)
        self.controller.step(boss_busy=False)
        self.assertEqual(self.transport.status(handle), "playing")

    def test_transport_gate_catches_race_between_selection_and_boss_start(self):
        self.controller.step(boss_busy=False)
        self.transport.states[0] = "ready"
        self.transport.gate_busy = True
        self.controller.step(boss_busy=False)
        self.assertEqual(self.transport.status(0), "ready")
        self.assertIsNone(self.store.snapshot()["events"]["notice"]["last_delivery"])

    def test_edit_while_paused_discards_old_audio(self):
        handle = self.start_playing()
        self.controller.interrupt_for_boss()
        self.store.edit_tts("notice", "새 문장")
        self.controller.step(boss_busy=False)
        self.assertEqual(self.transport.status(handle), "stopped")
        self.assertNotIn(("resume", handle), self.transport.calls)
        self.assertIsNone(self.store.snapshot()["events"]["notice"]["last_delivery"])

    def test_output_off_or_expiry_never_resumes(self):
        handle = self.start_playing()
        self.controller.interrupt_for_boss()
        self.now += timedelta(hours=1)
        self.controller.step(boss_busy=False)
        self.assertEqual(self.transport.status(handle), "stopped")
        self.assertIsNone(self.controller.active)

    def test_pause_failure_stops_only_notice_and_does_not_mark_complete(self):
        handle = self.start_playing()
        self.transport.allow_pause = False
        self.assertTrue(self.controller.interrupt_for_boss())
        self.assertEqual(self.transport.status(handle), "stopped")
        self.assertIsNone(self.store.snapshot()["events"]["notice"]["last_delivery"])

    def test_unconfirmed_silence_quarantines_controller(self):
        self.start_playing()
        self.transport.allow_pause = False
        self.transport.allow_stop = False
        self.assertFalse(self.controller.interrupt_for_boss())
        self.assertTrue(self.controller.fault)
        calls = list(self.transport.calls)
        self.controller.step(boss_busy=False)
        self.assertEqual(self.transport.calls, calls)
        self.assertIsNone(self.store.snapshot()["events"]["notice"]["last_delivery"])
        self.transport.allow_stop = True

    def test_server_switch_and_module_close_stop_old_audio(self):
        handle = self.start_playing()
        other = NoticeStore(self.store.root, "odin8", clock=lambda: self.now)
        self.assertTrue(self.controller.switch_store(other))
        self.assertEqual(self.transport.status(handle), "stopped")
        self.assertIsNone(self.store.snapshot()["events"]["notice"]["last_delivery"])
        self.controller.close()
        self.controller.step(boss_busy=False)
        self.assertIsNone(self.controller.active)

    def test_failed_player_retries_after_cooldown_without_completion(self):
        handle = self.start_playing()
        self.transport.states[handle] = "failed"
        self.controller.step(boss_busy=False)
        self.controller.step(boss_busy=False)
        self.assertIsNone(self.controller.active)
        self.seconds = 31
        self.controller.step(boss_busy=False)
        self.assertIsNotNone(self.controller.active)
        self.assertIsNone(self.store.snapshot()["events"]["notice"]["last_delivery"])

    def test_old_generation_cannot_complete_after_edit(self):
        handle = self.start_playing()
        self.store.edit_tts("notice", "새 안내")
        self.transport.states[handle] = "completed"
        self.controller.step(boss_busy=False)
        self.assertIsNone(self.store.snapshot()["events"]["notice"]["last_delivery"])

    def test_failed_completion_save_retries_record_not_audio(self):
        handle = self.start_playing()
        self.transport.states[handle] = "completed"
        with patch.object(self.store, "complete_delivery", side_effect=OSError("locked")):
            self.controller.step(boss_busy=False)
        self.assertIsNotNone(self.controller.pending_completion)
        self.controller.step(boss_busy=False)
        self.assertIsNone(self.controller.pending_completion)
        self.assertEqual(sum(call[0] == "prepare" for call in self.transport.calls), 1)

    def test_old_bridge_without_verified_completion_protocol_is_rejected(self):
        with self.assertRaises(ValueError):
            NoticeAudioController(self.store, object())

    def test_output_disabled_during_preparation_discards_without_play(self):
        self.controller.step(boss_busy=False)
        settings = self.store.snapshot()["settings"]
        settings["output_enabled"] = False
        self.store.configure(settings)
        self.transport.states[0] = "ready"
        self.controller.step(boss_busy=False)
        self.assertNotIn(("play", 0), self.transport.calls)
        self.assertEqual(self.transport.status(0), "stopped")

    def test_old_boss_opportunity_is_not_resumed_or_counted(self):
        self.store.discard("notice")
        self.store.register({"id": "guild", "title": "길던", "category": "participation", "tts_text": "참여해 주세요.",
                             "valid_from": self.now, "valid_until": self.now + timedelta(hours=4),
                             "event_from": self.now + timedelta(hours=3)})
        context = {"id": "boss-event", "server_id": "odin9", "at": self.now, "kind": "boss", "major": True,
                   "chapter": 8, "phase": "one_minute_complete"}
        self.controller.step(boss_busy=False, opportunity=context)
        self.transport.states[0] = "ready"
        self.controller.step(boss_busy=False)
        self.controller.interrupt_for_boss()
        self.now += timedelta(seconds=121)
        self.controller.step(boss_busy=False)
        self.assertEqual(self.transport.status(0), "stopped")
        self.assertEqual(self.store.snapshot().get("delivery_ledger", []), [])


if __name__ == "__main__":
    unittest.main()
