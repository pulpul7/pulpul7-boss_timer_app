"""Synthetic audio frames and fake voice clients only; no Discord/audio I/O."""
import asyncio
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, AsyncMock, patch

import boss_timer_discord_bot as bot_module
from voice_bridge_receipts import observe_audio_source
from notice_module.payload.notice_receipts import bridge_receipt_state


class SourceTests(unittest.TestCase):
    def test_receiver_requires_matching_process_scope_and_fresh_receipt(self):
        payload = {"pid": 123, "voice_connected": True, "voice_bridge_receipt_protocol": 1,
                   "voice_bridge_last_id": "one", "last_played": "BRIDGE AUDIO message",
                   "voice_bridge_receipts": []}
        def check():
            return bridge_receipt_state(payload, job_id="one", scope_id="notice-scope", bot_pid=123,
                                        sent_at="2026-09-14T18:00:00+09:00")
        self.assertEqual(check(), "waiting")
        receipt = {"id": "one", "scope_id": "notice-scope", "state": "started", "at": "2026-09-14T18:00:01+09:00"}
        payload["voice_bridge_receipts"] = [receipt]
        self.assertEqual(check(), "started")
        receipt["state"] = "completed"
        self.assertEqual(check(), "completed")
        receipt["scope_id"] = "other"
        self.assertEqual(check(), "failed")
        receipt["scope_id"] = "notice-scope"
        receipt["at"] = "2026-09-14T17:59:59+09:00"
        self.assertEqual(check(), "failed")
        receipt["at"] = "2026-09-14T18:00:01+09:00"
        payload["pid"] = 456
        self.assertEqual(check(), "failed")
        payload.pop("voice_bridge_receipt_protocol")
        self.assertEqual(check(), "unsupported")

    def test_frames_are_unchanged_and_cleanup_is_idempotent(self):
        raw = Mock()
        raw.read.side_effect = [b"pcm", b"end", b""]
        raw.is_opus.return_value = False
        wrapped = observe_audio_source(object, raw)
        self.assertEqual(wrapped.read(), b"pcm")
        self.assertFalse(wrapped.reached_eof)
        self.assertEqual(wrapped.read(), b"end")
        self.assertEqual(wrapped.read(), b"")
        self.assertTrue(wrapped.reached_eof)
        self.assertEqual(wrapped.frames_read, 2)
        self.assertFalse(wrapped.is_opus())
        wrapped.cleanup()
        wrapped.cleanup()
        raw.cleanup.assert_called_once()

    def test_status_receipts_are_bounded_and_do_not_expose_mutable_state(self):
        status = bot_module.BotStatus()
        for i in range(150):
            status.record_bridge_result(str(i), "started", "scope")
        status.record_bridge_result("149", "completed", "scope")
        payload = status.as_payload()
        self.assertEqual(len(payload["voice_bridge_receipts"]), 128)
        self.assertEqual(payload["voice_bridge_receipts"][-1]["state"], "completed")
        payload["voice_bridge_receipts"][-1]["state"] = "corrupt"
        self.assertEqual(status.as_payload()["voice_bridge_receipts"][-1]["state"], "completed")
        self.assertEqual(payload["voice_bridge_receipt_modes"], ["sequential"])


class PlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def outcome(self, *, eof=True, frames=True, error=None, cancelled=False, connected=True):
        bot = bot_module.DiscordScheduleBot.__new__(bot_module.DiscordScheduleBot)
        bot.client = SimpleNamespace(loop=asyncio.get_running_loop())
        bot.discord = SimpleNamespace(AudioSource=object)
        bot.voice_play_lock = asyncio.Lock()
        bot.voice_transition_lock = asyncio.Lock()
        bot.current_timed_composite_source = None
        bot.current_voice_playback_token = None
        bot._is_voice_bridge_scope_cancelled = Mock(return_value=False)
        raw = Mock()
        raw.read.side_effect = [b"pcm", b""] if frames else [b""]
        raw.is_opus.return_value = False
        bot._create_playback_source = Mock(return_value=raw)
        def play(source, *, after):
            source.read()
            if frames and eof:
                source.read()
            bot._is_voice_bridge_scope_cancelled.return_value = cancelled
            after(error)
        bot.voice_client = SimpleNamespace(is_playing=lambda: False, is_paused=lambda: False,
                                           is_connected=lambda: connected, play=play)
        with patch.object(bot_module, "log"):
            return await bot._play_clip("fake.mp3", gap_sec=0, scope_id="scope", confirm_eof=True)

    async def test_full_eof_and_success_callback_are_required(self):
        self.assertTrue(await self.outcome())
        self.assertFalse(await self.outcome(eof=False))
        self.assertFalse(await self.outcome(frames=False))
        self.assertFalse(await self.outcome(error=RuntimeError("decode failed")))
        self.assertFalse(await self.outcome(cancelled=True))
        self.assertFalse(await self.outcome(connected=False))

    async def test_sequential_job_reports_success_only_when_every_clip_succeeds(self):
        for outcomes, expected in (([True, True], "completed"), ([True, False], "failed"), ([False, True], "failed")):
            with self.subTest(outcomes=outcomes):
                bot = bot_module.DiscordScheduleBot.__new__(bot_module.DiscordScheduleBot)
                bot.client = SimpleNamespace(loop=asyncio.get_running_loop())
                bot.voice_client = SimpleNamespace(is_connected=lambda: True)
                bot._ensure_voice_connection = AsyncMock()
                bot._is_voice_bridge_scope_cancelled = Mock(return_value=False)
                bot._play_clip = AsyncMock(side_effect=outcomes)
                job = bot_module.VoiceBridgeJob("job1", datetime.now(), "AUDIO", "general", "center", 1.0, ("a", "b"))
                bot.play_queue = SimpleNamespace(get_nowait=lambda: job)
                status = bot_module.BotStatus()
                original = status.record_bridge_result
                def record(job_id, state, scope_id=""):
                    original(job_id, state, scope_id)
                    if state in {"completed", "failed", "cancelled"}:
                        status.shutdown_requested.set()
                status.record_bridge_result = record
                with patch.object(bot_module, "STATUS", status), patch.object(bot_module, "log"):
                    await asyncio.wait_for(bot._play_loop(), timeout=2)
                self.assertEqual(status.as_payload()["voice_bridge_receipts"][-1]["state"], expected)
                self.assertEqual(bot._play_clip.await_count, 2)


if __name__ == "__main__":
    unittest.main()
