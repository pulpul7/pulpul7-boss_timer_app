"""No network, voice playback, builds or real user configuration writes."""
import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

import boss_timer_discord_bot as bot_module
from discord_connection_policy import ConnectionPolicy, MAX_RETRIES


class PolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-connection-unit-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "test.ini"
        self.policy = ConnectionPolicy(self.path)

    def test_ten_attempts_shared_across_restarts_and_gui(self):
        for attempt in range(MAX_RETRIES):
            self.assertTrue(ConnectionPolicy(self.path).claim_retry(now=100 + attempt * 5))
        self.assertFalse(self.policy.claim_retry(now=1000))
        self.assertEqual(self.policy.snapshot()["retries"], 10)

    def test_retry_cooldown_does_not_spend_extra_attempts(self):
        self.assertTrue(self.policy.claim_retry(now=100))
        self.assertFalse(self.policy.claim_retry(now=104.9))
        self.assertEqual(self.policy.snapshot()["retries"], 1)
        self.assertTrue(self.policy.claim_retry(now=105))

    def test_standby_and_error_cannot_be_cleared_by_success(self):
        self.policy.pause()
        self.policy.success()
        self.assertTrue(ConnectionPolicy(self.path).snapshot()["standby"])
        self.assertFalse(self.policy.claim_retry())
        self.policy.resume()
        self.policy.update(error="Missing Access")
        self.policy.success()
        self.assertFalse(self.policy.claim_retry())
        self.policy.resume()
        self.assertTrue(self.policy.claim_retry(now=100))
        self.policy.success()
        self.assertEqual(self.policy.snapshot()["retries"], 0)

    def test_profiles_are_isolated_and_bad_state_fails_closed(self):
        self.policy.pause()
        other = ConnectionPolicy(self.path.with_name("other.ini"))
        self.assertTrue(other.claim_retry(now=100))
        self.policy.path.write_text("broken", encoding="utf-8")
        self.assertTrue(self.policy.snapshot()["standby"])
        self.assertFalse(self.policy.claim_retry())
        self.policy.resume()
        self.assertFalse(self.policy.snapshot()["standby"])


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-command-unit-")
        self.addCleanup(temp.cleanup)
        self.policy = ConnectionPolicy(Path(temp.name) / "test.ini")
        self.status = bot_module.BotStatus()
        for name, value in (("STATUS", self.status), ("log", Mock()), ("save_config_value", Mock())):
            patcher = patch.object(bot_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.bot = object.__new__(bot_module.DiscordScheduleBot)
        self.bot.config = {"server_id": "100", "application_id": "900", "voice_channel_id": "101", "default_voice_channel_id": "101"}
        self.bot.connection_policy = self.policy
        self.bot.voice_connect_lock = asyncio.Lock()
        self.bot.voice_transition_lock = asyncio.Lock()
        self.bot.client = NS(close=AsyncMock())
        self.bot.voice_client = None
        self.bot.timed_bridge_tasks = {}
        self.bot.play_task = None
        self.bot._stop_current_voice_playback_and_wait = AsyncMock(return_value=(True, 0, True))

    def interaction(self, channel=102, admin=True, guild=100):
        return NS(guild_id=guild, guild=NS(id=guild), command=None,
                  user=NS(voice=NS(channel=NS(id=channel) if channel else None),
                          guild_permissions=NS(administrator=admin, manage_guild=False)),
                  response=NS(send_message=AsyncMock(), defer=AsyncMock()),
                  followup=NS(send=AsyncMock()))

    async def test_join_uses_caller_channel_without_restarting_process(self):
        self.bot._connect_voice_channel = AsyncMock(return_value=(True, "ok"))
        await self.bot._join_from_interaction(self.interaction())
        self.bot._connect_voice_channel.assert_awaited_once_with("102")
        self.assertEqual(self.bot.config["default_voice_channel_id"], "101")
        self.assertEqual(self.bot.config["voice_channel_id"], "102")
        self.bot.client.close.assert_not_awaited()

    async def test_no_caller_channel_uses_original_default_after_move(self):
        self.bot.config["voice_channel_id"] = "103"
        self.bot._connect_voice_channel = AsyncMock(return_value=(True, "ok"))
        await self.bot._join_from_interaction(self.interaction(channel=None))
        self.bot._connect_voice_channel.assert_awaited_once_with("101")

    async def test_unauthorized_and_foreign_guild_do_not_change_state(self):
        self.bot._connect_voice_channel = AsyncMock()
        await self.bot._join_from_interaction(self.interaction(admin=False))
        await self.bot._join_from_interaction(self.interaction(guild=200))
        await self.bot._standby_from_interaction(self.interaction(admin=False))
        self.bot._connect_voice_channel.assert_not_awaited()
        self.assertFalse(self.policy.path.exists())

    async def test_missing_channel_does_not_clear_retry_limit(self):
        self.policy.update(retries=10)
        self.bot.config.update(voice_channel_id="", default_voice_channel_id="")
        await self.bot._join_from_interaction(self.interaction(channel=None))
        self.assertEqual(self.policy.snapshot()["retries"], 10)

    async def test_manual_join_resets_retry_budget(self):
        self.policy.update(retries=10, error="previous error")
        self.bot._connect_voice_channel = AsyncMock(return_value=(True, "ok"))
        await self.bot._join_from_interaction(self.interaction())
        self.assertEqual(self.policy.snapshot()["retries"], 0)
        self.assertFalse(self.status.configuration_error)

    async def test_standby_is_persisted_before_stopping_and_closes_gateway(self):
        async def stop(**kwargs):
            self.assertTrue(self.policy.snapshot()["standby"])
            self.assertTrue(self.status.standby)
            return True, 0, True
        self.bot._stop_current_voice_playback_and_wait.side_effect = stop
        self.bot.voice_client = NS(disconnect=AsyncMock())
        await self.bot._standby_from_interaction(self.interaction())
        self.bot.voice_client.disconnect.assert_awaited_once_with(force=True)
        self.bot.client.close.assert_awaited_once()
        self.assertFalse(self.bot._claim_connection_retry())
        self.assertTrue(self.bot._is_voice_bridge_scope_cancelled(""))

    async def test_standby_close_still_runs_after_disconnect_failure(self):
        self.bot.voice_client = NS(disconnect=AsyncMock(side_effect=RuntimeError("fail")))
        await self.bot._standby_from_interaction(self.interaction())
        self.bot.client.close.assert_awaited_once()
        self.assertTrue(self.policy.snapshot()["standby"])

    async def test_permission_failure_blocks_retries_but_timeout_does_not(self):
        class Forbidden(Exception):
            status, code = 403, 50001
        for exc, blocked in ((TimeoutError("timeout"), False), (Forbidden("Missing Access"), True)):
            self.policy.resume()
            self.bot.client.get_channel = Mock(return_value=None)
            self.bot.client.fetch_channel = AsyncMock(side_effect=exc)
            ok, _ = await self.bot._connect_voice_channel("101")
            self.assertFalse(ok)
            self.assertEqual(bool(self.policy.snapshot()["error"]), blocked)

    async def test_automatic_voice_retries_share_ten_attempt_budget(self):
        self.bot._connect_voice_channel = AsyncMock(return_value=(False, "failed"))
        for attempt in range(12):
            with patch("discord_connection_policy.time.time", return_value=100 + attempt * 5):
                await self.bot._connect_configured_voice_channel()
        self.assertEqual(self.bot._connect_voice_channel.await_count, 10)
        self.assertEqual(self.status.reconnect_attempts, 10)

    async def test_empty_audio_queue_can_be_cancelled_without_executor_hang(self):
        import queue
        self.bot.play_queue = queue.Queue()
        task = asyncio.create_task(self.bot._play_loop())
        await asyncio.sleep(.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)

    async def test_slash_registration_retains_bare_boss_command(self):
        import discord
        client = discord.Client(intents=discord.Intents.none())
        self.addAsyncCleanup(client.close)
        self.bot.discord = discord
        self.bot.tree = discord.app_commands.CommandTree(client)
        self.bot._bind_commands()
        command = self.bot.tree.get_command("보탐")
        self.assertIsNotNone(self.bot.tree.get_command("대기"))
        self.assertFalse(command.parameters[0].required)
        self.assertEqual(command.parameters[0].choices, [])  # Accept 접속 and every integer 0~50.
        self.bot._join_from_interaction = AsyncMock()
        await command.callback(self.interaction(), "접속")
        self.bot._join_from_interaction.assert_awaited_once()
        self.bot._resolve_text_channel = AsyncMock(return_value=NS(mention="#schedule"))
        self.bot._send_schedule_text = AsyncMock()
        await command.callback(self.interaction())
        self.bot._send_schedule_text.assert_awaited_once()

    async def test_gui_tracks_current_channel_without_changing_default(self):
        from boss_timer_gui import BossTimerApp
        app = object.__new__(BossTimerApp)
        app.discord_bot_server_id = "100"
        app.discord_bot_voice_channel_id = "101"
        app.discord_bot_default_voice_channel_id = "101"
        app._set_discord_bot_status_payload(dict(connection_control=True, guild_id="100", voice_channel_id="102",
                                                online=True, voice_connected=True, voice_bridge_enabled=True))
        self.assertEqual(app.discord_bot_voice_channel_id, "102")
        self.assertEqual(app.discord_bot_default_voice_channel_id, "101")
        self.assertTrue(app.discord_bot_voice_bridge_online)

    async def test_gui_does_not_restart_a_live_retrying_or_standby_bot(self):
        from boss_timer_gui import BossTimerApp
        app = object.__new__(BossTimerApp)
        app.discord_bot_expected_running = True
        app._reset_discord_voice_bridge_health_tracking = Mock()
        app._recover_discord_bot_runtime = Mock()
        for payload in ({"ok": True, "connection_control": True, "voice_connected": False}, {"standby": True}):
            app._monitor_discord_bot_voice_bridge_health(payload)
        app._recover_discord_bot_runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
