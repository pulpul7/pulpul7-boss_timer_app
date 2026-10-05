"""Source regression cases; no real Discord connection or audio is required."""
import asyncio
import logging
import threading
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from copy import deepcopy

import boss_timer_discord_bot as module


class RuntimeOptimizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = object.__new__(module.DiscordScheduleBot)
        self.bot.config = {}
        self.generation = 7
        self.bot._send_allowed = Mock(return_value=True)
        self.bot._connection_state = lambda: {"authority_generation": self.generation,
                                              "authority_active": True,
                                              "command_sync_allowed": True}
        self.bot._is_voice_bridge_scope_cancelled = Mock(return_value=False)
        self.bot._cleanup_audio_source = Mock()
        self.bot.boss_notice_lock = asyncio.Lock()
        self.bot.boss_notice_groups = {}
        self.bot.voice_channel_panel_message_id = ""
        self.bot.client = NS(user=NS(id=900))
        self.channel = NS(id=101)

    def test_default_twelve_and_saved_choices_preserved(self):
        self.assertEqual(self.bot._text_channel_keep_count(), 12)
        for value in ("", None, "invalid", "-1", "51"):
            self.bot.config["text_channel_keep_count"] = value
            self.assertEqual(self.bot._text_channel_keep_count(), 12)
        for value in (0, 2, 12, 50):
            self.bot.config["text_channel_keep_count"] = str(value)
            self.assertEqual(self.bot._text_channel_keep_count(), value)

    async def test_history_is_bounded_and_repeated_pass_is_deferred(self):
        scans = []
        messages = [NS(id=i, author=NS(id=900), pinned=False, embeds=[],
                       delete=AsyncMock()) for i in range(40)]
        messages[-1].author.id = 1
        messages[-2].pinned = True
        from discord_authority_events import COMPLETED_NOTICE_FOOTER
        messages[-3].embeds = [NS(footer=NS(text=COMPLETED_NOTICE_FOOTER))]
        async def history(**values):
            scans.append(values)
            for message in messages:
                yield message
        self.channel.history = history
        self.bot._defer_text_channel_cleanup = Mock()
        with patch.object(module, "time", NS(monotonic=lambda: 1000, time=lambda: 2000)):
            await self.bot._cleanup_bot_text_channel_messages(self.channel)
            await self.bot._cleanup_bot_text_channel_messages(self.channel)
        self.assertEqual(scans, [{"limit": 100}])
        self.bot._defer_text_channel_cleanup.assert_called_once()
        self.assertEqual(sum(message.delete.await_count for message in messages), 20)
        for message in messages[-3:]:
            message.delete.assert_not_awaited()

    async def test_unlimited_and_standby_never_request_history(self):
        self.channel.history = Mock()
        self.bot.config["text_channel_keep_count"] = "0"
        await self.bot._cleanup_bot_text_channel_messages(self.channel)
        self.bot.config["text_channel_keep_count"] = "12"
        self.bot._send_allowed.return_value = False
        await self.bot._cleanup_bot_text_channel_messages(self.channel)
        self.channel.history.assert_not_called()

    async def test_slow_audio_preparation_does_not_block_loop_and_cancel_discards_source(self):
        loop = asyncio.get_running_loop()
        entered, cleaned = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        source = object()
        worker_threads = []
        def prepare(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(2):
                raise TimeoutError("test worker was not released")
            return source
        self.bot._create_playback_source = prepare
        self.bot._cleanup_audio_source.side_effect = lambda _: loop.call_soon_threadsafe(cleaned.set)
        task = asyncio.create_task(self.bot._prepare_playback_source("fake.wav"))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            self.assertNotEqual(worker_threads[0], threading.get_ident())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            release.set()
        await asyncio.wait_for(cleaned.wait(), 1)
        self.bot._cleanup_audio_source.assert_called_once_with(source)

    async def test_audio_prepared_under_old_generation_is_never_returned(self):
        source = object()
        def prepare(*args, **kwargs):
            self.generation = 8
            return source
        self.bot._create_playback_source = prepare
        self.assertIsNone(await self.bot._prepare_playback_source("fake.wav"))
        self.bot._cleanup_audio_source.assert_called_once_with(source)

    async def test_command_registration_is_not_repeated_after_handover(self):
        self.bot.commands_registered = False
        self.bot._get_configured_server_id = lambda: "100"
        self.bot.discord = NS(Object=lambda **kwargs: NS(**kwargs))
        self.bot.tree = NS(copy_global_to=Mock(), sync=AsyncMock())
        with patch.object(module, "log"):
            await self.bot._sync_commands()
            self.generation = 8
            await self.bot._sync_commands()
        self.bot.tree.sync.assert_awaited_once()

    async def test_stale_ready_flag_does_not_hide_long_gateway_outage(self):
        stopped = threading.Event()
        status = NS(online=False, shutdown_requested=stopped, update=Mock())
        self.bot.client = NS(is_ready=lambda: True, close=AsyncMock())
        self.bot.gateway_disconnected_at = 1000
        async def pause(_):
            return
        with patch.object(module, "STATUS", status), patch.object(module, "time", NS(monotonic=lambda: 1091)), \
                patch.object(module.asyncio, "sleep", pause), patch.object(module, "log"):
            await self.bot._gateway_recovery_loop()
        self.bot.client.close.assert_awaited_once()
        self.assertEqual(module.GATEWAY_RECOVERY_TIMEOUT_SEC, 90)

    def test_api_limit_diagnostic_omits_sensitive_url_and_coalesces_warnings(self):
        handler = module.HttpRateLimitDiagnosticHandler()
        record = logging.LogRecord("discord.http", logging.WARNING, "", 0,
            "We are being rate limited. %s %s responded with 429. Retrying in %.2f seconds.",
            ("POST", "https://discord.com/api/webhooks/private-token", 1.5), None)
        with patch.object(module, "log") as write_log:
            handler.emit(record)
            handler.emit(record)
        write_log.assert_called_once()
        self.assertIn("route_rate_limit", write_log.call_args.args[0])
        self.assertIn("retry_after_sec=1.500", write_log.call_args.args[0])
        self.assertNotIn("private-token", write_log.call_args.args[0])


class QueryReadCacheTests(unittest.TestCase):
    def test_waiting_reads_share_cache_but_writes_use_current_sha(self):
        from discord_query_routing import QueryLedger
        import discord_query_routing as routing
        stored = {"schema": 1, "guild": "100", "requests": {"1": {"state": "pending"}}}
        sha = ["old-sha"]
        get = Mock(side_effect=lambda _: (deepcopy(stored), sha[0], ""))
        def put(_path, data, **values):
            self.assertEqual(values["sha"], "new-sha")
            stored.clear()
            stored.update(deepcopy(data))
            return True, ""
        ledger = QueryLedger(NS(get=get, put=put, scope={"guild": "100"}, path="authority.json"))
        with patch.object(routing, "time", NS(monotonic=lambda: 1000)):
            row = ledger.entry("1", cached=True)
            row["state"] = "local-only-change"
            self.assertEqual(ledger.entry("1", cached=True)["state"], "pending")
            self.assertEqual(get.call_count, 1)
            sha[0] = "new-sha"
            ledger.mutate(lambda requests, record: requests["1"].update(state="ready"))
            self.assertEqual(get.call_count, 2)
            self.assertEqual(ledger.entry("1", cached=True)["state"], "ready")
            self.assertEqual(get.call_count, 3)


if __name__ == "__main__":
    unittest.main()
