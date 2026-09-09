"""Persistence and precedence tests for Discord soundboard commands."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import boss_timer_discord_bot as discord_bot


class DiscordVoiceCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temporary_directory.name) / "discord_voice_commands.json"
        self.original_save = discord_bot.save_custom_discord_voice_commands

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _save_to_test_registry(self, commands, *, disabled_builtin_commands=None) -> None:
        self.original_save(
            commands,
            self.registry_path,
            disabled_builtin_commands=disabled_builtin_commands,
        )

    @staticmethod
    def _new_bot() -> discord_bot.DiscordScheduleBot:
        bot = discord_bot.DiscordScheduleBot.__new__(discord_bot.DiscordScheduleBot)
        bot.custom_voice_commands = {}
        bot.disabled_builtin_voice_commands = set()
        return bot

    def test_regular_custom_command_survives_save_and_delete(self) -> None:
        bot = self._new_bot()
        with patch.object(discord_bot, "save_custom_discord_voice_commands", self._save_to_test_registry):
            added, _message, name, text = bot._add_discord_voice_command("테스트, 준비 완료")
            self.assertTrue(added)
            self.assertEqual(bot._resolve_discord_voice_command(" 테스트 "), (name, text))

            deleted, _message = bot._delete_discord_voice_command("테스트")
            self.assertTrue(deleted)
            self.assertIsNone(bot._resolve_discord_voice_command("테스트"))

        self.assertEqual(discord_bot.load_custom_discord_voice_commands(self.registry_path), {})

    def test_builtin_command_can_be_overwritten_deleted_and_restored_by_new_override(self) -> None:
        builtin_name, builtin_text = next(iter(discord_bot.BUILTIN_DISCORD_VOICE_COMMANDS.items()))
        bot = self._new_bot()
        with patch.object(discord_bot, "save_custom_discord_voice_commands", self._save_to_test_registry):
            added, _message, _name, _text = bot._add_discord_voice_command(
                f"{builtin_name}, 새 기본 문구"
            )
            self.assertTrue(added)
            self.assertEqual(bot._resolve_discord_voice_command(builtin_name), (builtin_name, "새 기본 문구"))
            self.assertIn(
                (discord_bot.normalize_discord_voice_command_name(builtin_name), builtin_name, "새 기본 문구"),
                bot._get_discord_voice_command_menu_entries(),
            )

            deleted, _message = bot._delete_discord_voice_command(builtin_name)
            self.assertTrue(deleted)
            self.assertIsNone(bot._resolve_discord_voice_command(builtin_name))
            self.assertNotIn(
                discord_bot.normalize_discord_voice_command_name(builtin_name),
                {key for key, _name, _text in bot._get_discord_voice_command_menu_entries()},
            )

            added, _message, _name, _text = bot._add_discord_voice_command(
                f"{builtin_name}, 다시 설정한 문구"
            )
            self.assertTrue(added)
            self.assertEqual(bot._resolve_discord_voice_command(builtin_name), (builtin_name, "다시 설정한 문구"))

        restarted_bot = self._new_bot()
        restarted_bot.custom_voice_commands = discord_bot.load_custom_discord_voice_commands(self.registry_path)
        restarted_bot.disabled_builtin_voice_commands = discord_bot.load_disabled_builtin_discord_voice_commands(
            self.registry_path
        )
        self.assertEqual(
            restarted_bot._resolve_discord_voice_command(builtin_name),
            (builtin_name, "다시 설정한 문구"),
        )
        self.assertNotEqual(restarted_bot._resolve_discord_voice_command(builtin_name)[1], builtin_text)


if __name__ == "__main__":
    unittest.main()
