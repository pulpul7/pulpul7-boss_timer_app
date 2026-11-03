"""All-target classification for the third participation announcement."""
from copy import deepcopy
from datetime import datetime
import unittest

from notice_module.payload.notice_management import KST
from notice_module.payload.notice_remaining import classified_targets, remaining_message
from notice_module.payload.notice_templates import render_template


def boss(name, chapter=7, minute=0, **extra):
    return dict(boss_name=name, chapter=chapter, scheduled_at=f'2026-10-17T23:{minute:02d}:00+09:00', **extra)


class RemainingSpeechTests(unittest.TestCase):
    def test_single_boss_per_chapter_keeps_names_and_three_combine(self):
        self.assertEqual(remaining_message([boss('히로킨'), boss('티르', 8)]),
                         '오늘 남은 주요보스는 히로킨, 티르입니다.')
        self.assertEqual(remaining_message([boss('히로킨'), boss('호드', minute=50), boss('티르', 8)]),
                         '오늘 남은 주요보스는 7, 8챕입니다.')

    def test_absolute_overrides_chapter_membership_and_each_boss_is_classified_once(self):
        rows = [boss('절대자A', 7, absolute=True), boss('절대자B', 8, absolute=True),
                boss('히로킨'), boss('호드'), boss('티르', 8)]
        self.assertEqual(remaining_message(rows), '오늘 남은 주요보스는 절대자, 7, 8챕입니다.')
        self.assertEqual(len(classified_targets(rows)), 2)

    def test_spawn_time_precedes_absolute_and_chapter_priority(self):
        rows = [dict(boss('오딘', 4, absolute=True), scheduled_at='2026-11-01T23:15:00+09:00'),
                dict(boss('엘디르', 8), scheduled_at='2026-11-01T21:40:00+09:00')]
        original = deepcopy(rows)
        self.assertEqual(remaining_message(rows), '오늘 남은 주요보스는 엘디르, 오딘입니다.')
        self.assertEqual(rows, original)

    def test_all_classes_use_earliest_spawn_order(self):
        rows = [boss('절대자A', absolute=True, minute=50), boss('절대자B', absolute=True, minute=55),
                boss('7챕A', minute=20), boss('7챕B', minute=25),
                boss('8챕A', 8, minute=10), boss('8챕B', 8, minute=15)]
        self.assertEqual(remaining_message(rows), '오늘 남은 주요보스는 7, 8챕, 절대자입니다.')
        rows.append(boss('던전A', 0, minute=30, area='던전'))
        self.assertEqual(remaining_message(rows), '오늘 남은 주요보스는 7, 8챕, 던전, 절대자입니다.')

    def test_four_or_more_classes_remain_explicit(self):
        rows = [boss('절대자A', absolute=True), boss('절대자B', absolute=True),
                boss('히로킨'), boss('호드'), boss('티르', 8), boss('던전A', 0, area='던전'),
                boss('최하층굴베', 0, area='던전'), boss('최하층스네르', 0, area='던전'),
                boss('최하층강글', 0, area='던전')]
        self.assertEqual(remaining_message(rows),
            '오늘 남은 주요보스는 절대자, 7, 8챕, 던전, 최하층굴베, 최하층스네르, 최하층강글입니다.')

    def test_deduplicates_recurring_names_without_losing_other_names(self):
        rows = [boss('히로킨'), boss('히로킨', minute=40), boss('티르', 8)]
        original = deepcopy(rows)
        self.assertEqual(remaining_message(rows), '오늘 남은 주요보스는 히로킨, 티르입니다.')
        self.assertEqual(rows, original)

    def test_empty_and_fixed_alias(self):
        self.assertEqual(remaining_message([]), '')
        self.assertEqual(remaining_message([boss('원래이름', display_name='별칭', fixed=True)]),
                         '오늘 남은 주요보스는 별칭입니다.')

    def test_depth_classes_stay_distinct_and_fixed_names_never_become_chapters(self):
        rows = [boss('히로킨'), boss('티르', 8, minute=5), boss('오딘', 4, minute=10, absolute=True),
                boss('최하층 굴베', 0, minute=15, area='던전'),
                boss('최하층스네르', 0, minute=20, area='던전'),
                boss('최하층 강글', 0, minute=25, area='던전'),
                boss('방어전', 7, minute=30, fixed=True, absolute=True)]
        self.assertEqual(remaining_message(rows),
            '오늘 남은 주요보스는 히로킨, 티르, 오딘, 최하층굴베, 최하층스네르, 최하층강글, 방어전입니다.')

    def test_new_chapter_and_fixed_event_alias_deduplication(self):
        rows = [boss('새보스', 9), boss('길던', fixed=True, minute=5), boss('길드던전', fixed=True, minute=10)]
        self.assertEqual(remaining_message(rows), '오늘 남은 주요보스는 9챕, 길던입니다.')

    def test_seven_eight_count_matrix_uses_distinct_bosses(self):
        for seven, eight, labels in [
            (1, 0, ['7보스0']), (0, 1, ['8보스0']),
            (1, 1, ['7보스0', '8보스0']), (2, 0, ['7챕']), (0, 2, ['8챕']),
            (3, 0, ['7챕']), (0, 3, ['8챕']),
            (2, 1, ['7, 8챕']), (1, 2, ['7, 8챕']), (2, 2, ['7, 8챕'])]:
            with self.subTest(seven=seven, eight=eight):
                rows = [boss(f'7보스{i}', 7, minute=i) for i in range(seven)]
                rows += [boss(f'8보스{i}', 8, minute=10+i) for i in range(eight)]
                self.assertEqual([g['label'] for g in classified_targets(rows)], labels)
                self.assertEqual([g['label'] for g in classified_targets(rows + rows)], labels)

    def test_absolute_singleton_name_plural_label_and_no_chapter_double_count(self):
        absolute = boss('오딘', 7, absolute=True)
        self.assertEqual([g['label'] for g in classified_targets([absolute])], ['오딘'])
        self.assertEqual([g['label'] for g in classified_targets([absolute, absolute])], ['오딘'])
        self.assertEqual([g['label'] for g in classified_targets([absolute, boss('수르트', 8, absolute=True)])], ['절대자'])
        rows = [boss('호드', 7), boss('스카디', 8, minute=5), absolute]
        self.assertEqual([g['label'] for g in classified_targets(rows)], ['호드', '오딘', '스카디'])

    def test_aliases_and_combined_group_keep_first_spawn_order(self):
        rows = [boss('스카디', 8, minute=20), boss('호드', 7, minute=10, display_name='호드별칭'),
                boss('호드', 7, minute=15, display_name='다른별칭')]
        self.assertEqual([g['label'] for g in classified_targets(rows)], ['호드별칭', '스카디'])
        rows.extend([boss('히로킨', 7, minute=30), boss('오딘', 4, minute=0, absolute=True)])
        self.assertEqual([g['label'] for g in classified_targets(rows)], ['오딘', '7, 8챕'])

    def test_elapsed_target_recalculates_combined_group_and_absolute_singleton(self):
        rows = [boss('호드', 7, minute=10), boss('히로킨', 7, minute=20), boss('스카디', 8, minute=30),
                boss('수르트', 4, minute=10, absolute=True), boss('오딘', 4, minute=40, absolute=True)]
        before = deepcopy(rows)
        initial = render_template({}, 'participation.remaining', {'_remaining_entries': rows},
                                  now=datetime(2026, 10, 17, 23, 5, tzinfo=KST))
        later = render_template({}, 'participation.remaining', {'_remaining_entries': rows},
                                now=datetime(2026, 10, 17, 23, 15, tzinfo=KST))
        self.assertEqual(initial, '오늘 남은 주요보스는 7, 8챕, 절대자입니다.')
        self.assertEqual(later, '오늘 남은 주요보스는 히로킨, 스카디, 오딘입니다.')
        self.assertEqual(rows, before)


if __name__ == '__main__':
    unittest.main()
