from copy import deepcopy
from datetime import datetime
import unittest

from notice_module.payload.notice_management import KST
from notice_module.payload.notice_templates import date_time_values, render_template


class MorningGroupTests(unittest.TestCase):
    now = datetime(2026, 9, 22, 20, tzinfo=KST)

    def speech(self, *rows):
        entries = [dict(at=f"2026-09-23T{clock}:00+09:00", name=name, period="아침")
                   for clock, name in rows]
        return render_template({}, 'participation.morning', {'_boss_entries': entries}, now=self.now)

    def test_two_bosses_only_read_first_time(self):
        self.assertEqual(self.speech(('06:43', '수르트'), ('07:10', '미미르')),
                         '내일 아침 6시 43분 수르트, 미미르 일정이 있습니다. 많은 참여 부탁드립니다.')

    def test_three_or_more_count_remaining_names(self):
        text = self.speech(('06:43', '수르트'), ('07:10', '미미르'), ('07:12', '이미르'))
        self.assertIn('6시 43분 수르트 외 2개', text)
        self.assertNotIn('미미르', text)
        text = self.speech(('06:43', '수르트'), ('06:50', '미미르'), ('07:10', '이미르'), ('07:12', '오딘'))
        self.assertIn('수르트 외 3개', text)

    def test_exact_thirty_minutes_is_excluded(self):
        text = self.speech(('06:00', '수르트'), ('06:29', '미미르'), ('06:30', '이미르'))
        self.assertIn('6시 수르트, 미미르 일정', text)
        self.assertNotIn('이미르', text)
        self.assertEqual(text.count('내일 아침'), 1)

    def test_sort_before_group_and_do_not_chain_beyond_first_window(self):
        text = self.speech(('06:40', '이미르'), ('06:00', '수르트'), ('06:20', '미미르'))
        self.assertIn('6시 수르트, 미미르 일정', text)
        self.assertNotIn('이미르', text)

    def test_dawn_uses_one_time_and_original_data_is_unchanged(self):
        rows = [dict(at='2026-09-23T00:21:00+09:00', name='최하층 강글', period='새벽'),
                dict(at='2026-09-23T00:24:00+09:00', name='최하층 굴베', period='새벽')]
        original = deepcopy(rows)
        text = date_time_values({'_boss_entries': rows}, self.now)['보스목록']
        self.assertEqual(text, '내일 새벽 0시 21분 최하층 강글, 최하층 굴베')
        self.assertEqual(rows, original)

    def test_seconds_boundary_and_duplicate_names(self):
        rows = [dict(at='2026-09-23T02:00:00+09:00', name='강글', period='새벽'),
                dict(at='2026-09-23T02:10:00+09:00', name='강글', period='새벽'),
                dict(at='2026-09-23T02:29:59+09:00', name='굴베', period='새벽'),
                dict(at='2026-09-23T02:30:00+09:00', name='스네르', period='새벽')]
        self.assertEqual(date_time_values({'_boss_entries': rows}, self.now)['보스목록'],
                         '내일 새벽 2시 강글, 굴베')


if __name__ == '__main__':
    unittest.main()
