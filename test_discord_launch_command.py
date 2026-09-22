"""Do not let old distribution EXEs shadow a source checkout's bot patches."""
import ast
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import boss_timer_gui as module


class DiscordLaunchCommandTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(module.BossTimerApp)
        self.app._get_discord_bot_script_command = Mock(return_value=["pythonw.exe", "boss_timer_discord_bot.py"])
        self.app._get_discord_bot_executable_path = Mock(return_value="dist/boss_timer_discord_bot.exe")

    def test_source_uses_latest_script_even_when_old_exe_exists(self):
        with patch.object(module.sys, "frozen", False, create=True):
            self.assertEqual(self.app._get_discord_bot_launch_command(), ["pythonw.exe", "boss_timer_discord_bot.py"])
        self.app._get_discord_bot_executable_path.assert_not_called()

    def test_frozen_uses_packaged_exe_never_source(self):
        with patch.object(module.sys, "frozen", True, create=True):
            self.assertEqual(self.app._get_discord_bot_launch_command(), ["dist/boss_timer_discord_bot.exe"])
        self.app._get_discord_bot_script_command.assert_not_called()

    def test_source_without_script_can_use_existing_exe(self):
        self.app._get_discord_bot_script_command.return_value = []
        with patch.object(module.sys, "frozen", False, create=True):
            self.assertEqual(self.app._get_discord_bot_launch_command(), ["dist/boss_timer_discord_bot.exe"])

    def test_missing_packaged_exe_does_not_run_frozen_gui_as_python(self):
        self.app._get_discord_bot_executable_path.return_value = ""
        with patch.object(module.sys, "frozen", True, create=True):
            self.assertEqual(self.app._get_discord_bot_launch_command(), [])
        self.app._get_discord_bot_script_command.assert_not_called()

    def test_no_source_or_exe_returns_empty(self):
        self.app._get_discord_bot_script_command.return_value = []
        self.app._get_discord_bot_executable_path.return_value = ""
        with patch.object(module.sys, "frozen", False, create=True):
            self.assertEqual(self.app._get_discord_bot_launch_command(), [])

    def test_start_and_disconnect_share_launch_selection(self):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8-sig"))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "BossTimerApp")
        for name in ("_start_discord_bot_runtime", "_run_discord_bot_disconnect_only"):
            method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name)
            attrs = [node.attr for node in ast.walk(method) if isinstance(node, ast.Attribute)]
            self.assertIn("_get_discord_bot_launch_command", attrs)
            self.assertNotIn("_get_discord_bot_executable_path", attrs)


if __name__ == "__main__":
    unittest.main()
