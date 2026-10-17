"""All-target classification for the third participation announcement."""
from copy import deepcopy
import unittest

from notice_module.payload.notice_remaining import classified_targets, remaining_message


def boss(name, chapter=7, minute=0, **extra):
    return dict(boss_name=name, chapter=chapter, scheduled_at=f'2026-10-17T23:{minute:02d}:00+09:00', **extra)


class RemainingSpeechTests(unittest.TestCase):
    def test_single_bosses_keep_names_and_plural_chapter_collapses(self):
        self.assertEqual(remaining_message([boss('히로킨'), boss('티르', 8)]),
                         '오늘 남은 주요보스 히로킨, 티르가 남았습니다.')
        self.assertEqual(remaining_message([boss('히로킨'), boss('호드', minute=50), boss('티르', 8)]),
                         '오늘 남은 주요보스 7챕보스, 티르가 남았습니다.')

    def test_absolute_precedes_chapter_and_each_boss_is_classified_once(self):
        rows = [boss('절대자A', 7, absolute=True), boss('절대자B', 8, absolute=True),
                boss('히로킨'), boss('호드'), boss('티르', 8)]
        self.assertEqual(remaining_message(rows), '오늘 남은 주요보스 절대자, 7챕보스, 티르가 남았습니다.')
        self.assertEqual(len(classified_targets(rows)), 3)

    def test_four_classes_read_two_then_many_but_three_read_all(self):
        rows = [boss('절대자A', absolute=True), boss('절대자B', absolute=True),
                boss('히로킨'), boss('호드'), boss('티르', 8), boss('던전A', 0, area='던전')]
        self.assertEqual(remaining_message(rows), '오늘은 절대자, 7챕보스 외 다수 주요보스가 남아있습니다.')
        self.assertIn('티르가 남았습니다.', remaining_message(rows[:-1]))

    def test_deduplicates_recurring_names_without_losing_other_names(self):
        rows = [boss('히로킨'), boss('히로킨', minute=40), boss('티르', 8)]
        original = deepcopy(rows)
        self.assertEqual(remaining_message(rows), '오늘 남은 주요보스 히로킨, 티르가 남았습니다.')
        self.assertEqual(rows, original)

    def test_empty_alias_and_particle(self):
        self.assertEqual(remaining_message([]), '')
        self.assertEqual(remaining_message([boss('원래이름', display_name='별칭')]),
                         '오늘 남은 주요보스 별칭이 남았습니다.')


if __name__ == '__main__':
    unittest.main()
