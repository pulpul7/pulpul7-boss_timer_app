"""Tests for the user-writable Discord soundboard media folder."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import boss_timer_gui as gui


class DiscordVoiceMediaFolderTests(unittest.TestCase):
    def test_media_folder_is_created_in_application_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            app_root = Path(temporary_directory) / "BossTimer"
            with patch.object(gui, "get_app_root", return_value=str(app_root)):
                result = gui.ensure_discord_voice_command_media_dir()
            expected = app_root / gui.DISCORD_VOICE_COMMAND_MEDIA_DIRNAME
            self.assertEqual(Path(result), expected)
            self.assertTrue(expected.is_dir())


if __name__ == "__main__":
    unittest.main()
