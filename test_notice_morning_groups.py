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
        text = self.speech(('06:43', '수르트'), ('07:10', '미미르'), ('07:20', '이미르'))
        self.assertIn('6시 43분 수르트 외 2개', text)
        self.assertNotIn('미미르', text)
        text = self.speech(('06:43', '수르트'), ('06:50', '미미르'), ('07:10', '이미르'), ('07:20', '오딘'))
        self.assertIn('수르트 외 3개', text)

    def test_exact_one_hour_starts_another_group(self):
        text = self.speech(('06:00', '수르트'), ('06:59', '미미르'), ('07:00', '이미르'))
        self.assertIn('6시 수르트, 미미르, 내일 아침 7시 이미르', text)

    def test_sort_before_group_and_do_not_chain_beyond_first_hour(self):
        text = self.speech(('07:20', '이미르'), ('06:00', '수르트'), ('06:40', '미미르'))
        self.assertIn('6시 수르트, 미미르, 내일 아침 7시 20분 이미르', text)

    def test_non_morning_notices_keep_individual_times_and_data_unchanged(self):
        rows = [dict(at='2026-09-23T02:00:00+09:00', name='수르트', period='새벽'),
                dict(at='2026-09-23T02:20:00+09:00', name='미미르', period='새벽')]
        text = date_time_values({'_boss_entries': rows}, self.now)['보스목록']
        self.assertIn('2시 수르트, 내일 새벽 2시 20분 미미르', text)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['at'], '2026-09-23T02:20:00+09:00')


if __name__ == '__main__':
    unittest.main()
