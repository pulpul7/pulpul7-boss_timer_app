"""Regression tests for saved schedule break-time rules."""

from __future__ import annotations

import unittest

import boss_timer_gui as gui


class ScheduleBreakRuleTests(unittest.TestCase):
    @staticmethod
    def _app() -> gui.BossTimerApp:
        return gui.BossTimerApp.__new__(gui.BossTimerApp)

    def test_default_break_rules_do_not_restore_former_test_labels(self) -> None:
        entries = self._app()._build_default_schedule_break_entries()
        target = next(entry for entry in entries if entry["min_gap_minutes"] == 80)
        self.assertEqual(target["label_text"], "휴식")

    def test_long_break_row_scale_is_preserved_above_three(self) -> None:
        entry = self._app()._normalize_schedule_break_rule_entry(
            {
                "min_gap_minutes": 290,
                "max_gap_minutes": 319,
                "row_scale": 5.5,
            }
        )
        self.assertIsNotNone(entry)
        self.assertEqual(entry["row_scale"], 5.5)

    def test_row_scale_keeps_a_safe_upper_bound(self) -> None:
        entry = self._app()._normalize_schedule_break_rule_entry({"row_scale": 99})
        self.assertIsNotNone(entry)
        self.assertEqual(entry["row_scale"], 12.0)


if __name__ == "__main__":
    unittest.main()
