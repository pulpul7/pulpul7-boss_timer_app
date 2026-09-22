"""Offline soundboard routing and configurable schedule message retention."""
import asyncio
import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import discord
import boss_timer_discord_bot as module


class ChannelPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = object.__new__(module.DiscordScheduleBot)
        self.bot.config = {"server_id": "100", "text_channel_id": "101"}
        self.guild = NS(id=100, text_channels=[])
        self.notice = NS(id=101, name="보탐매니저", guild=self.guild, send=AsyncMock())
        self.board = NS(id=102, name="사운드보드", guild=self.guild, send=AsyncMock())
        self.guild.text_channels = [self.notice, self.board]
        self.bot.client = NS(
            user=NS(id=900), get_guild=Mock(return_value=self.guild),
            get_channel=Mock(side_effect=lambda key: {101: self.notice, 102: self.board}.get(key)),
            fetch_channel=AsyncMock(return_value=None))
        self.bot.custom_voice_commands = {}
        self.bot.disabled_builtin_voice_commands = set()
        self.bot.voice_channel_panel_message_id = ""
        self.bot.message_content_enabled = True
        self.bot._queue_local_schedule_request = AsyncMock()
        self.bot._cleanup_voice_panel_after_activity = AsyncMock()
        self.bot.discord = discord
        self.client = discord.Client(intents=discord.Intents.none())
        self.addAsyncCleanup(self.client.close)
        self.bot.tree = discord.app_commands.CommandTree(self.client)
        self.bot._bind_commands()
        for name in ("save_config_value", "log"):
            patcher = patch.object(module, name, Mock())
            patcher.start()
            self.addCleanup(patcher.stop)

    def interaction(self, channel=None, guild=None):
        channel = channel or self.board
        guild = guild or self.guild
        return NS(guild=guild, guild_id=guild.id, channel=channel, user=NS(id=1),
                  response=NS(send_message=AsyncMock(), defer=AsyncMock(), edit_message=AsyncMock()),
                  followup=NS(send=AsyncMock()))

    async def test_default_resolves_soundboard_and_saves_not_notice_channel(self):
        self.assertIs(await self.bot._resolve_voice_panel_channel(), self.board)
        self.assertEqual(self.bot.config["voice_panel_channel_id"], "102")
        module.save_config_value.assert_called_with("voice_panel_channel_id", "102")

    async def test_no_board_never_falls_back_to_notice(self):
        self.guild.text_channels = [self.notice]
        self.assertIsNone(await self.bot._resolve_voice_panel_channel())
        self.notice.send.assert_not_awaited()

    async def test_legacy_same_channel_configuration_moves_to_board(self):
        self.bot.config["voice_panel_channel_id"] = "101"
        self.assertIs(await self.bot._resolve_voice_panel_channel(), self.board)

    async def test_foreign_guild_ignored(self):
        interaction = self.interaction(guild=NS(id=200))
        self.assertFalse(await self.bot._require_soundboard_channel(interaction))
        interaction.response.send_message.assert_not_awaited()
        self.assertIsNone(await self.bot._resolve_voice_panel_channel(NS(id=200)))

    async def test_all_soundboard_slash_commands_reject_notice_channel_before_changes(self):
        self.bot._add_discord_voice_command = Mock()
        self.bot._delete_discord_voice_command = Mock()
        for name, args in (("음성", ()), ("음성목록", ()), ("음성추가", ("test", "hello")),
                           ("음성삭제", ("test",))):
            interaction = self.interaction(self.notice)
            await self.bot.tree.get_command(name).callback(interaction, *args)
            self.assertIn("<#102>", interaction.response.send_message.call_args.args[0])
            self.assertTrue(interaction.response.send_message.call_args.kwargs["ephemeral"])
        self.bot._add_discord_voice_command.assert_not_called()
        self.bot._delete_discord_voice_command.assert_not_called()
        self.bot._queue_local_schedule_request.assert_not_awaited()

    async def test_alias_opens_buttons_only_in_soundboard(self):
        interaction = self.interaction()
        await self.bot.tree.get_command("음성").callback(interaction)
        self.assertIn("view", interaction.response.send_message.call_args.kwargs)

    async def test_persistent_buttons_cannot_play_from_legacy_notice_panel(self):
        view = self.bot._build_discord_voice_command_menu_view(persistent=True)
        await view.children[0].callback(self.interaction(self.notice))
        self.bot._queue_local_schedule_request.assert_not_awaited()
        await view.children[0].callback(self.interaction())
        self.bot._queue_local_schedule_request.assert_awaited_once()
        self.assertEqual(self.bot._queue_local_schedule_request.call_args.args[0]["channel_id"], "102")
        await asyncio.sleep(0)

    async def test_plain_voice_command_routes_to_board_only(self):
        name = next(iter(module.BUILTIN_DISCORD_VOICE_COMMANDS))
        for channel in (self.notice, self.board):
            message = NS(author=NS(id=1, bot=False), guild=self.guild, channel=channel,
                         content=name, id=1001, add_reaction=AsyncMock())
            await self.bot._handle_schedule_text_message(message)
        self.bot._queue_local_schedule_request.assert_awaited_once()
        self.assertEqual(self.bot._queue_local_schedule_request.call_args.args[0]["operation"], "voice_play")

    async def test_soundboard_cannot_be_selected_as_notice_channel_or_reverse(self):
        await self.bot._resolve_voice_panel_channel()
        for name, channel in (("음성채널", self.notice), ("보탐채널", self.board)):
            interaction = self.interaction(channel)
            await self.bot.tree.get_command(name).callback(interaction)
            self.assertIn("따로", interaction.response.send_message.call_args.args[0])

    async def test_all_51_valid_counts_saved_without_requesting_schedule(self):
        self.bot._cleanup_bot_text_channel_messages = AsyncMock()
        self.bot._send_schedule_text = AsyncMock()
        command = self.bot.tree.get_command("보탐")
        for count in range(51):
            await command.callback(self.interaction(self.notice), str(count))
            self.assertEqual(self.bot.config["text_channel_keep_count"], str(count))
            module.save_config_value.assert_called_with("text_channel_keep_count", str(count))
        self.bot._send_schedule_text.assert_not_awaited()
        self.assertEqual(self.bot._cleanup_bot_text_channel_messages.await_count, 51)

    async def test_countdown_slash_commands_queue_explicit_boolean(self):
        for action, enabled in (("초읽기", True), ("초읽기해제", False)):
            interaction = self.interaction(self.notice)
            await self.bot.tree.get_command("보탐").callback(interaction, action)
            payload = self.bot._queue_local_schedule_request.call_args.args[0]
            self.assertEqual(payload["operation"], "countdown_control")
            self.assertIs(payload["enabled"], enabled)
            self.assertEqual(payload["server_id"], "100")
            self.assertEqual(payload["raw_text"], f"/보탐 {action}")
            self.assertIn("요청", interaction.followup.send.call_args.args[0])
        module.save_config_value.assert_not_called()

    async def test_help_lists_every_registered_command_and_examples_without_mutation(self):
        from discord_command_help import COMMAND_HELP
        interaction = self.interaction(self.notice)
        await self.bot.tree.get_command("보탐").callback(interaction, "?")
        self.assertLessEqual(len(COMMAND_HELP), 2000)
        for command in self.bot.tree.get_commands():
            self.assertIn(f"/{command.name}", COMMAND_HELP)
        self.assertIn("2120 그로아, 헤이드, 티르", COMMAND_HELP)
        interaction.response.send_message.assert_awaited_once_with(COMMAND_HELP, ephemeral=True)
        self.bot._queue_local_schedule_request.assert_not_awaited()
        module.save_config_value.assert_not_called()

    async def test_plain_help_is_not_mistaken_for_retention_count(self):
        from discord_command_help import COMMAND_HELP
        message = NS(author=NS(id=1, bot=False), guild=self.guild, channel=self.notice,
                     content="/보탐 ?", id=1001, reply=AsyncMock())
        await self.bot._handle_schedule_text_message(message)
        message.reply.assert_awaited_once_with(COMMAND_HELP, mention_author=False)
        self.bot._queue_local_schedule_request.assert_not_awaited()
        module.save_config_value.assert_not_called()

    async def test_countdown_slash_rejects_foreign_guild(self):
        await self.bot.tree.get_command("보탐").callback(self.interaction(guild=NS(id=200)), "초읽기")
        self.bot._queue_local_schedule_request.assert_not_awaited()

    async def test_countdown_queue_failure_returns_error(self):
        self.bot._queue_local_schedule_request.side_effect = OSError("test")
        interaction = self.interaction()
        await self.bot.tree.get_command("보탐").callback(interaction, "초읽기해제")
        self.assertIn("전달하지 못했습니다", interaction.followup.send.call_args.args[0])

    async def test_plain_countdown_commands_only_work_in_notice_channel(self):
        for channel in (self.board, self.notice):
            for action, enabled in (("초읽기", True), ("초읽기해제", False)):
                message = NS(author=NS(id=1, bot=False), guild=self.guild, channel=channel,
                             content=f"/보탐 {action}", id=1001, reply=AsyncMock())
                await self.bot._handle_schedule_text_message(message)
                if channel is self.notice:
                    self.assertIs(self.bot._queue_local_schedule_request.call_args.args[0]["enabled"], enabled)
        self.assertEqual(self.bot._queue_local_schedule_request.await_count, 2)

    async def test_invalid_counts_do_not_change_configuration(self):
        command = self.bot.tree.get_command("보탐")
        for value in ("-1", "51", "120", "2.5", "abc"):
            interaction = self.interaction()
            await command.callback(interaction, value)
            self.assertIn("0~50", interaction.response.send_message.call_args.args[0])
        module.save_config_value.assert_not_called()

    async def test_save_failure_does_not_apply_or_delete(self):
        module.save_config_value.return_value = False
        self.bot._cleanup_bot_text_channel_messages = AsyncMock()
        result = await self.bot._set_text_channel_keep_count(1, self.guild)
        self.assertIn("저장하지 못했습니다", result)
        self.assertEqual(self.bot._text_channel_keep_count(), 2)
        self.bot._cleanup_bot_text_channel_messages.assert_not_awaited()

    async def test_plain_boss_count_command_does_not_become_schedule_input(self):
        message = NS(author=NS(id=1, bot=False), guild=self.guild, channel=self.notice,
                     content="/보탐 0", id=1001, reply=AsyncMock())
        await self.bot._handle_schedule_text_message(message)
        self.assertEqual(self.bot.config["text_channel_keep_count"], "0")
        self.bot._queue_local_schedule_request.assert_not_awaited()
        self.assertIn("제한 없음", message.reply.call_args.args[0])

    async def test_schedule_posts_use_configured_cleanup(self):
        self.bot.schedule_reader = NS(upcoming_rows=Mock(return_value=[]))
        self.bot._cleanup_bot_text_channel_messages = AsyncMock()
        await self.bot._send_schedule_text(self.notice)
        await self.bot._send_schedule_plain_text(self.notice)
        self.assertEqual(self.bot._cleanup_bot_text_channel_messages.await_count, 2)

    async def test_startup_publishes_only_to_board(self):
        self.bot._publish_discord_voice_channel_panel = AsyncMock(return_value=(True, "ok"))
        await self.bot._initialize_voice_panel_after_ready()
        self.bot._publish_discord_voice_channel_panel.assert_awaited_once_with(self.board, self.guild)

    async def test_unlimited_never_reads_history_or_deletes(self):
        self.bot.config["text_channel_keep_count"] = "0"
        self.notice.history = Mock()
        await self.bot._cleanup_bot_text_channel_messages(self.notice)
        self.notice.history.assert_not_called()

    async def test_retention_protects_user_pinned_and_panel_messages(self):
        messages = [NS(id=i, author=NS(id=900), pinned=False, delete=AsyncMock()) for i in range(100)]
        messages[0].author.id = 10
        messages[1].pinned = True
        self.bot.voice_channel_panel_message_id = "2"

        async def history(**kwargs):
            self.assertIsNone(kwargs["limit"])
            for message in messages:
                yield message

        self.notice.history = history
        for count in (1, 2, 50):
            self.bot.config["text_channel_keep_count"] = str(count)
            for message in messages:
                message.delete.reset_mock()
            await self.bot._cleanup_bot_text_channel_messages(self.notice)
            for index, message in enumerate(messages):
                self.assertEqual(message.delete.await_count, int(index >= count + 3))

    async def test_bad_config_uses_original_two_default(self):
        for value in (None, "", "oops", "-1", "51"):
            self.bot.config["text_channel_keep_count"] = value
            self.assertEqual(self.bot._text_channel_keep_count(), 2)

    async def test_legacy_panel_removal_only_deletes_verified_saved_bot_panel(self):
        self.bot.voice_channel_panel_message_id = "600"
        old = NS(author=NS(id=900), content="🔊 보탐매니저 음성 사운드보드", delete=AsyncMock())
        self.notice.fetch_message = AsyncMock(return_value=old)
        await self.bot._remove_old_schedule_channel_panel(self.guild, self.board, "500")
        old.delete.assert_awaited_once()
        old.delete.reset_mock()
        old.content = "Other bot message"
        await self.bot._remove_old_schedule_channel_panel(self.guild, self.board, "500")
        old.delete.assert_not_awaited()


class PersistenceTests(unittest.TestCase):
    def test_gui_save_preserves_bot_setting_and_load_accepts_zero(self):
        from boss_timer_gui import BossTimerApp
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discord.ini"
            with patch.object(module, "CONFIG_PATH", path):
                module.save_config_value("text_channel_keep_count", "0")
                module.save_config_value("voice_panel_channel_id", "102")
                app = object.__new__(BossTimerApp)
                app._get_discord_bot_config_storage_path = lambda: str(path)
                app.discord_bot_voice_panel_channel_id = "101"  # stale GUI snapshot
                self.assertTrue(app._save_discord_bot_settings())
                self.assertEqual(module.load_config()["text_channel_keep_count"], "0")
                self.assertEqual(module.load_config()["voice_panel_channel_id"], "102")
                parser = configparser.ConfigParser()
                parser.read(path, encoding="utf-8")
                self.assertEqual(parser["discord_bot"]["text_channel_keep_count"], "0")


if __name__ == "__main__":
    unittest.main()
