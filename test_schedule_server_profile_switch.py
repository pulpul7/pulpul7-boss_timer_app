"""Safety checks for switching schedule-server profiles."""

from __future__ import annotations

import unittest

import boss_timer_gui as gui


class ScheduleServerProfileSwitchTests(unittest.TestCase):
    def test_existing_profile_is_never_seeded_from_cache(self) -> None:
        self.assertFalse(gui.BossTimerApp._should_seed_schedule_server_profile_from_cache(True))

    def test_new_profile_can_be_seeded_from_cache(self) -> None:
        self.assertTrue(gui.BossTimerApp._should_seed_schedule_server_profile_from_cache(False))


if __name__ == "__main__":
    unittest.main()
