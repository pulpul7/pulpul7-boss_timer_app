"""Shared-time boss lists must enter the normal parser and duplicate validator."""
from datetime import datetime
import unittest

from boss_timer_discord_bot import parse_discord_schedule_message
from schedule_input_display import find_duplicate_boss_lines
from schedule_input_syntax import expand_schedule_boss_line
import test_edge_tts_voice as existing_tests


class MultiBossInputTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 22, 12)
        self.app = existing_tests.DiscordScheduleInputTests._parser_app(self.now)

    def test_three_bosses_share_same_time(self):
        items, ignored = self.app._parse_schedule_input_lines("2120 그로아, 헤이드, 티르", reference_datetime=self.now)
        self.assertEqual(ignored, 0)
        self.assertEqual(len(items), 3)
        self.assertEqual([item["boss_name"] for item in items], ["그로아", "헤이드", "티르"])
        self.assertEqual({self.app._resolve_schedule_seed_datetime(item, self.now) for item in items},
                         {datetime(2026, 9, 22, 21, 20)})

    def test_fractional_seconds_and_fullwidth_comma_preserved(self):
        for clock in ("21:20:02.551234", "212002.551234"):
            items, ignored = self.app._parse_schedule_input_lines(f"{clock} 그로아，헤이드, 티르", reference_datetime=self.now)
            self.assertEqual(ignored, 0)
            self.assertEqual(len(items), 3)
            self.assertEqual({item["clock_microsecond"] for item in items}, {551234})

    def test_duplicates_keep_original_line_numbers_even_in_one_line(self):
        text = "2120 그로아, 헤이드, 그로아\n2200 헤이드"
        groups = find_duplicate_boss_lines(text, self.app._parse_schedule_input_line)
        self.assertEqual(sorted(group["lines"] for group in groups), [[1, 1], [1, 2]])

    def test_common_cut_suffix_and_invasion_name_preserved(self):
        self.assertEqual(expand_schedule_boss_line("2120 침공 그로아, 헤이드 컷"),
                         ["2120 침공 그로아 컷", "2120 헤이드 컷"])

    def test_single_names_duration_and_comments_unchanged(self):
        for line in ("2120 그로아", "그로아 1일 2시간", "# 2120 그로아, 헤이드"):
            self.assertEqual(expand_schedule_boss_line(line), [line])

    def test_next_day_clock_applies_equally_to_all_names(self):
        items, ignored = self.app._parse_schedule_input_lines("2510 그로아, 헤이드", reference_datetime=self.now)
        self.assertEqual(ignored, 0)
        self.assertEqual({self.app._resolve_schedule_seed_datetime(item, self.now) for item in items},
                         {datetime(2026, 9, 23, 1, 10)})

    def test_discord_and_gui_share_fractional_result(self):
        request = parse_discord_schedule_message("21:20:02.55 그로아, 헤이드, 티르")
        self.assertEqual(request["line_count"], 3)
        items, ignored = self.app._parse_schedule_input_lines(request["raw_text"], reference_datetime=self.now)
        self.assertEqual(ignored, 0)
        self.assertEqual(len(items), 3)
        self.assertEqual({item["clock_microsecond"] for item in items}, {550000})


if __name__ == "__main__":
    unittest.main()
