"""Regression tests for the schedule shown by the Discord /보탐 command."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import boss_timer_discord_bot as discord_bot


class DiscordScheduleReaderTests(unittest.TestCase):
    def test_upcoming_rows_includes_a_scheduled_normal_boss(self) -> None:
        """Normal schedule events must be listed alongside fixed-boss entries."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_path = Path(temporary_directory)
            state_path = temporary_path / "schedule_state.json"
            definitions_path = temporary_path / "schedule_boss_definitions.txt"
            fixed_bosses_path = temporary_path / "schedule_fixed_bosses.txt"
            scheduled_at = datetime.now() + timedelta(minutes=2)
            state_path.write_text(
                json.dumps(
                    {
                        "schedule_events": [
                            {
                                "state": "scheduled",
                                "scheduled_at": scheduled_at.isoformat(),
                                "display_name": "일반보스",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            definitions_path.write_text("일반보스|일반|003:00|미드가르드|0|#FFFFFF|#2563EB|0\n", encoding="utf-8")
            fixed_bosses_path.write_text("", encoding="utf-8")

            with (
                patch.object(discord_bot, "SCHEDULE_STATE_PATH", state_path),
                patch.object(discord_bot, "SCHEDULE_BOSS_DEFINITIONS_PATH", definitions_path),
                patch.object(discord_bot, "FIXED_BOSSES_PATH", fixed_bosses_path),
            ):
                rows = discord_bot.ScheduleReader().upcoming_rows()

        self.assertEqual([(row["name"], row["kind"]) for row in rows], [("일반보스", "일반")])


if __name__ == "__main__":
    unittest.main()
