"""Offline guild isolation and authenticated identity regression tests."""
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import boss_timer_discord_bot as module


class EventClient:
    def __init__(self, **kwargs):
        self.user = NS(id=900)
        self.application_id = 900

    def event(self, callback):
        setattr(self, callback.__name__, callback)
        return callback

    async def setup_hook(self):
        pass


class ServerIsolationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="boss-discord-unit-")
        self.addCleanup(temporary.cleanup)
        self.config_path = Path(temporary.name) / "discord.ini"
        self.status = module.BotStatus()
        for name, value in (("CONFIG_PATH", self.config_path), ("STATUS", self.status), ("log", Mock()), ("save_config_value", Mock())):
            patcher = patch.object(module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def bot(self, guild=100):
        bot = object.__new__(module.DiscordScheduleBot)
        bot.config = {"server_id": str(guild), "voice_channel_id": str(guild + 1), "application_id": "900"}
        bot.client = EventClient()
        bot.voice_client = None
        bot.connection_policy = module.ConnectionPolicy(self.config_path)
        bot._resolve_voice_panel_channel = AsyncMock(return_value=None)
        bot._publish_discord_voice_channel_panel = AsyncMock()
        bot._bind_events()
        return bot

    async def test_foreign_join_move_and_leave_do_not_change_any_local_state(self):
        bot = self.bot()
        self.status.update(guild_id="100", voice_channel_id="101", voice_connected=True)
        previous = self.status.as_payload()
        config = dict(bot.config)
        for channel in (NS(id=201, guild=NS(id=200)), NS(id=202, guild=NS(id=200)), None):
            await bot.client.on_voice_state_update(NS(id=900, guild=NS(id=200)), NS(), NS(channel=channel))
            self.assertEqual(bot.config, config)
            self.assertEqual(self.status.as_payload(), previous)
        module.save_config_value.assert_not_called()
        bot._resolve_voice_panel_channel.assert_not_awaited()

    async def test_foreign_event_does_not_poison_next_reconnection_target(self):
        bot = self.bot()
        bot._connect_voice_channel = AsyncMock(return_value=(True, "ok"))
        await bot.client.on_voice_state_update(NS(id=900, guild=NS(id=200)), NS(),
                                             NS(channel=NS(id=201, guild=NS(id=200))))
        self.assertTrue(await bot._connect_configured_voice_channel())
        bot._connect_voice_channel.assert_awaited_once_with("101")

    async def test_own_guild_move_is_still_saved(self):
        bot = self.bot()
        channel = NS(id=102, guild=NS(id=100))
        await bot.client.on_voice_state_update(NS(id=900, guild=NS(id=100)), NS(), NS(channel=channel))
        self.assertEqual(bot.config["voice_channel_id"], "102")
        module.save_config_value.assert_called_once_with("voice_channel_id", "102")
        self.assertEqual(self.status.guild_id, "100")
        self.assertTrue(self.status.voice_connected)

    async def test_own_guild_leave_marks_disconnected_without_erasing_channel(self):
        bot = self.bot()
        self.status.update(voice_connected=True)
        await bot.client.on_voice_state_update(NS(id=900, guild=NS(id=100)), NS(), NS(channel=None))
        self.assertFalse(self.status.voice_connected)
        self.assertEqual(bot.config["voice_channel_id"], "101")
        module.save_config_value.assert_not_called()

    async def test_other_user_unknown_guild_and_conflicting_channel_ignored(self):
        bot = self.bot()
        for member, channel in ((NS(id=901, guild=NS(id=100)), NS(id=102, guild=NS(id=100))),
                                (NS(id=900), None),
                                (NS(id=900, guild=NS(id=100)), NS(id=201, guild=NS(id=200)))):
            await bot.client.on_voice_state_update(member, NS(), NS(channel=channel))
        module.save_config_value.assert_not_called()
        bot._resolve_voice_panel_channel.assert_not_awaited()

    async def test_wrong_application_is_rejected_without_logging_token(self):
        bot = self.bot()
        bot.config["bot_token"] = "test-secret-not-to-log"
        with self.assertRaises(module.DiscordConfigurationError):
            bot._validate_authenticated_application(901)
        self.assertTrue(self.status.as_payload()["configuration_error"])
        self.assertNotIn(bot.config["bot_token"], str(module.log.call_args_list))
        self.assertNotIn(bot.config["bot_token"], str(self.status.as_payload()))

    async def test_matching_application_allowed(self):
        self.bot()._validate_authenticated_application(900)
        self.assertFalse(self.status.configuration_error)

    async def test_identity_error_waits_for_manual_stop_instead_of_retrying(self):
        bot = self.bot()
        bot.config["bot_token"] = "fake-token"
        bot.message_content_enabled = True
        bot.client.run = Mock(side_effect=module.DiscordConfigurationError("설정 오류"))
        with patch.object(self.status.shutdown_requested, "wait") as wait:
            self.assertTrue(bot.run())
        bot.client.run.assert_called_once()
        wait.assert_called_once_with()

    async def test_login_hook_is_wired_before_any_ready_or_voice_operations(self):
        fake_discord = NS(Client=EventClient, Intents=NS(default=lambda: NS()),
                          app_commands=NS(CommandTree=Mock()))
        with patch.object(module, "load_config", return_value={"application_id": "901"}), \
             patch.object(module, "ScheduleReader"), patch.object(module, "VoiceBridgeReader"), \
             patch.object(module, "load_custom_discord_voice_commands", return_value={}), \
             patch.object(module, "load_disabled_builtin_discord_voice_commands", return_value=set()), \
             patch.object(module.DiscordScheduleBot, "_bind_commands"):
            bot = module.DiscordScheduleBot(fake_discord)
            with self.assertRaises(module.DiscordConfigurationError):
                await bot.client.setup_hook()
        self.assertIsNone(bot.voice_client)
        module.save_config_value.assert_not_called()

    async def test_already_corrupted_channel_rejected_without_moving_or_connecting(self):
        bot = self.bot()
        channel = NS(guild=NS(id=200), connect=AsyncMock())
        bot.client.get_channel = Mock(return_value=channel)
        success, message = await bot._connect_voice_channel("201")
        self.assertFalse(success)
        self.assertIn("음성채널", message)
        self.assertTrue(self.status.configuration_error)
        channel.connect.assert_not_awaited()
        bot._connect_voice_channel = AsyncMock()
        self.assertFalse(await bot._connect_configured_voice_channel())
        await bot._ensure_voice_connection()
        bot._connect_voice_channel.assert_not_awaited()

    async def test_gui_does_not_restart_bot_for_configuration_error(self):
        from boss_timer_gui import BossTimerApp
        app = object.__new__(BossTimerApp)
        app._reset_discord_voice_bridge_health_tracking = Mock()
        app._recover_discord_bot_runtime = Mock()
        app._monitor_discord_bot_voice_bridge_health({"ok": True, "configuration_error": "설정 확인 필요"})
        app._reset_discord_voice_bridge_health_tracking.assert_called_once()
        app._recover_discord_bot_runtime.assert_not_called()


if __name__ == "__main__":
    unittest.main()
