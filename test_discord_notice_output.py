"""Synthetic frames/fake voice clients only. No synthesis or Discord connection."""
import asyncio
import queue
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import boss_timer_discord_bot as bot_module
from discord_notice_output import ResumableSource


class RawSource:
    def __init__(self, *frames):
        self.frames = iter((*frames, b""))
        self.cleanups = 0

    def read(self):
        return next(self.frames, b"")

    def is_opus(self):
        return False

    def cleanup(self):
        self.cleanups += 1


class ResumableSourceTests(unittest.TestCase):
    def test_suspend_is_not_eof_and_resume_preserves_position(self):
        raw = RawSource(b"a", b"b")
        owner = ResumableSource(raw)
        first = owner.attach(object)
        self.assertEqual(first.read(), b"a")
        first.suspend()
        self.assertEqual(first.read(), b"")
        self.assertFalse(owner.eof)
        self.assertFalse(owner.close())
        with self.assertRaises(RuntimeError):
            owner.attach(object)
        first.cleanup()
        self.assertEqual(raw.cleanups, 0)
        second = owner.attach(object)
        self.assertEqual(second.read(), b"b")
        self.assertEqual(second.read(), b"")
        self.assertTrue(owner.eof)
        second.cleanup()
        self.assertTrue(owner.close())
        self.assertTrue(owner.close())
        self.assertEqual(raw.cleanups, 1)

    def test_inflight_read_does_not_block_suspension_or_lose_a_frame(self):
        entered, release = threading.Event(), threading.Event()
        raw = RawSource()
        def read():
            entered.set()
            release.wait(timeout=2)
            return b"inflight"
        raw.read = read
        owner = ResumableSource(raw)
        first = owner.attach(object)
        result = []
        worker = threading.Thread(target=lambda: result.append(first.read()), daemon=True)
        worker.start()
        self.assertTrue(entered.wait(timeout=2))
        first.suspend()  # Returns before the decoder was released.
        self.assertFalse(owner.close())
        release.set()
        worker.join(timeout=2)
        self.assertEqual(result, [b""])
        first.cleanup()
        second = owner.attach(object)
        self.assertEqual(second.read(), b"inflight")
        self.assertEqual(owner.frames, 1)
        second.cleanup()
        owner.close()

    def test_decoder_error_is_visible_to_discord_player(self):
        raw = RawSource()
        raw._current_error = RuntimeError("decode error")
        segment = ResumableSource(raw).attach(object)
        self.assertIs(segment._current_error, raw._current_error)


class FakeVoice:
    def __init__(self):
        self.source = None
        self.after = None
        self.active = False
        self.connected = True
        self.played = []
        self.stops = 0
        self._player = None

    def is_connected(self):
        return self.connected

    def is_playing(self):
        return self.active

    def is_paused(self):
        return False

    def play(self, source, *, after):
        if self.active:
            raise RuntimeError("already playing")
        self.source, self.after = source, after
        self.active = True
        player = SimpleNamespace(alive=True)
        player.join = lambda timeout: None
        player.is_alive = lambda: player.alive
        self._player = player
        self.played.append(source)

    def finish(self, error=None):
        if self.active:
            self.active = False
            self._player.alive = False
            self.after(error)
            self.source.cleanup()

    def read(self):
        frame = self.source.read()
        if not frame:
            self.finish()
        return frame

    def stop(self):
        self.stops += 1
        self.finish()


class BotNoticeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = bot = bot_module.DiscordScheduleBot.__new__(bot_module.DiscordScheduleBot)
        bot.client = SimpleNamespace(loop=asyncio.get_running_loop())
        bot.discord = SimpleNamespace(AudioSource=object)
        bot.voice_client = self.voice = FakeVoice()
        bot.voice_play_lock = asyncio.Lock()
        bot.voice_transition_lock = asyncio.Lock()
        bot.play_queue = queue.Queue()
        bot.timed_bridge_tasks = {}
        bot.cancelled_voice_bridge_scopes = {}
        bot.current_voice_playback_token = None
        bot.current_voice_player_thread = None
        bot.current_voice_scope_id = ""
        bot.current_timed_composite_source = None
        bot.current_timed_composite_scope_id = ""
        bot.notice_output = None
        self.log_patch = patch.object(bot_module, "log")
        self.log_patch.start()
        self.addCleanup(self.log_patch.stop)
        self.raw = RawSource(b"one", b"two", b"three")
        self.assertTrue(await bot._install_prepared_notice_output(self.raw, scope_id="notice:test", deadline=time.monotonic() + 60))
        self.output = bot.notice_output

    async def asyncTearDown(self):
        await self.output.stop()
        await asyncio.wait_for(self.output.watcher, timeout=2)

    async def settle(self):
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def test_install_is_silent_and_cannot_replace_owned_notice(self):
        self.assertEqual(self.output.state, "ready")
        self.assertEqual(self.voice.played, [])
        other = RawSource(b"other")
        self.assertFalse(await self.bot._install_prepared_notice_output(other, scope_id="new", deadline=time.monotonic() + 60))
        self.assertEqual(other.cleanups, 0)  # Caller still owns rejected source.

    async def test_queue_job_and_timed_output_all_take_priority(self):
        self.bot.play_queue.put("boss")
        self.assertFalse(await self.output.play())
        self.bot.play_queue.get_nowait()
        self.bot.regular_voice_job_active = True
        self.assertFalse(await self.output.play())
        self.bot.regular_voice_job_active = False
        self.bot.timed_bridge_tasks["timed"] = ("scope", None)
        self.assertFalse(await self.output.play())
        self.bot.timed_bridge_tasks.clear()
        async with self.bot.voice_play_lock:
            self.assertFalse(await self.output.play())
        self.assertEqual(self.voice.played, [])

    async def test_timed_boss_preempts_and_notice_resumes_at_next_frame(self):
        self.assertTrue(await self.output.play())
        self.assertEqual(self.voice.read(), b"one")
        boss = RawSource(b"boss")
        await self.bot._play_timed_clip_replacing_current("fake", source=boss, scope_id="boss")
        self.assertEqual(self.output.state, "paused")
        self.assertEqual(self.raw.cleanups, 0)
        self.assertFalse(await self.output.play())
        self.assertEqual(self.voice.read(), b"boss")
        self.voice.read()
        await self.settle()
        self.assertTrue(await self.output.play())
        self.assertEqual(self.voice.read(), b"two")
        self.assertEqual(self.voice.read(), b"three")
        self.voice.read()
        await self.settle()
        self.assertEqual(self.output.state, "completed")
        await asyncio.wait_for(self.output.watcher, timeout=2)
        self.assertEqual(self.raw.cleanups, 1)

    async def test_sequential_chime_does_not_wait_for_whole_notice(self):
        self.assertTrue(await self.output.play())
        self.voice.read()
        boss = RawSource(b"chime")
        self.bot._create_playback_source = Mock(return_value=boss)
        task = asyncio.create_task(self.bot._play_clip("fake", gap_sec=0, scope_id="boss"))
        async def advance():
            while self.voice.source is not boss:
                await asyncio.sleep(0.001)
            self.assertEqual(self.output.state, "paused")
            self.voice.read()
            self.voice.read()
            await task
        try:
            await asyncio.wait_for(advance(), timeout=2)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_composite_replacement_retains_decoder_but_cancel_does_not(self):
        self.assertTrue(await self.output.play())
        self.voice.read()
        async with self.bot.voice_transition_lock:
            await self.bot._stop_current_voice_playback_and_wait(reason="countdown_composite_replace")
        self.assertEqual(self.output.state, "paused")
        self.assertTrue(await self.output.play())
        async with self.bot.voice_transition_lock:
            await self.bot._stop_current_voice_playback_and_wait(expected_scope_id="notice:test", reason="cancel_scope")
        self.assertEqual(self.output.state, "stopped")
        self.assertFalse(await self.output.play())

    async def test_expiry_during_boss_only_closes_notice(self):
        self.assertTrue(await self.output.play())
        boss = RawSource(b"boss")
        await self.bot._play_timed_clip_replacing_current("fake", source=boss, scope_id="boss")
        stops = self.voice.stops
        self.output.deadline = time.monotonic() - 1
        await asyncio.wait_for(self.output.watcher, timeout=2)
        self.assertEqual(self.voice.stops, stops)
        self.assertTrue(self.voice.active)
        self.assertEqual(self.raw.cleanups, 1)
        self.assertFalse(await self.output.play())
        self.voice.finish()
        await self.settle()

    async def test_old_callback_cannot_finish_resumed_segment(self):
        await self.output.play()
        old_token, old_segment = self.output.token, self.output.segment
        async with self.bot.voice_transition_lock:
            await self.bot._stop_current_voice_playback_and_wait(reason="notice_preempt")
        await self.output.play()
        new_token = self.output.token
        self.output._ended(old_token, old_segment, None)
        self.assertIs(self.output.token, new_token)
        self.assertEqual(self.output.state, "playing")

    async def test_cancelled_paused_scope_is_not_resumed(self):
        await self.output.play()
        async with self.bot.voice_transition_lock:
            await self.bot._stop_current_voice_playback_and_wait(reason="notice_preempt")
        self.bot.cancelled_voice_bridge_scopes["notice:test"] = time.monotonic()
        self.assertFalse(await self.output.play())
        await asyncio.wait_for(self.output.watcher, timeout=2)
        self.assertTrue(self.output.released)
        self.assertEqual(self.raw.cleanups, 1)

    async def test_unexpected_stop_or_decode_error_never_completes(self):
        await self.output.play()
        self.voice.read()
        self.voice.finish(RuntimeError("decode error"))
        await self.settle()
        self.assertEqual(self.output.state, "failed")
        self.assertFalse(await self.output.play())


if __name__ == "__main__":
    unittest.main()
