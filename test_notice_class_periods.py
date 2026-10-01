"""Class season shared windows and spoken title dates, without network/audio."""
from copy import deepcopy
from datetime import datetime
import unittest

from notice_module.payload.notice_analysis import analyze_notice, notice_events
from notice_module.payload.notice_management import KST
from notice_module.payload.notice_templates import render_template, spoken_title_dates


class ClassPeriodsTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 1, 10, tzinfo=KST)
        self.article = dict(id='CT9G/1973', category='class_change',
            title='클래스 변경 시즌 15 안내', url='https://m.cafe.daum.net/odin/CT9G/1973',
            published_date='2026-09-30', first_seen=self.now.isoformat(), body=(
                'Ⅰ. 클래스 변경 일정\n'
                '- 클래스 변경권 판매 및 클래스 변경 기간: 9월 30일(수) 점검 후 ~ 10월 7일(수) 08:00\n'
                'Ⅲ. 클래스 변경권 제작식 안내\n'
                '제작 기간: 2026년 9월 30일(수) 08:00 ~ 2026년 10월 7일(수) 07:59'))
        self.maintenance = dict(id='CT9G/1970', category='maintenance', title='9/30(수) 정기 점검 안내',
            published_date='2026-09-29', body='점검 일정\n9월 30일(수) 08:00 ~ 11:30')

    def test_explicit_end_preserved_without_fabricating_start_or_crafting_time(self):
        before = deepcopy(self.article)
        analysis = analyze_notice(self.article)
        self.assertEqual([f['kind'] for f in analysis['windows']], ['class_purchase', 'class_use'])
        for fact in analysis['windows']:
            self.assertIsNone(fact['start'])
            self.assertEqual(fact['end'], '2026-10-07T08:00:00+09:00')
            self.assertFalse(fact['issue'])
        self.assertIn('종료 확정', analysis['status'])
        events = notice_events(self.article, analysis)
        self.assertEqual(len(events), 2)
        for event in events:
            self.assertIsNone(event['event_from'])
            self.assertEqual(event['valid_from'], datetime(2026, 10, 6, 8, tzinfo=KST))
            self.assertEqual(event['valid_until'], datetime(2026, 10, 7, 8, tzinfo=KST))
            self.assertNotIn('07시 59분', event['tts_text'])
        self.assertEqual(self.article, before)

    def test_matching_maintenance_resolves_shared_start_and_enables_purchase_notice(self):
        analysis = analyze_notice(self.article, related_articles=[self.maintenance], now=self.now)
        for fact in analysis['windows']:
            self.assertEqual(fact['start'], '2026-09-30T11:30:00+09:00')
            self.assertEqual(fact['start_source_id'], 'CT9G/1970')
        events = notice_events(self.article, analysis)
        self.assertEqual(len(events), 3)
        self.assertTrue(any(e['id'].endswith('/purchase_notice') for e in events))

    def test_wrong_day_future_conflicting_or_failed_maintenance_never_fills_start(self):
        wrong = dict(self.maintenance, body='점검 일정\n9월 29일(화) 08:00 ~ 11:30')
        conflict = dict(self.maintenance, id='other', body='점검 일정\n9월 30일(수) 08:00 ~ 12:00')
        failed = dict(self.maintenance, body_error='fetch failed')
        for rows, now in (([wrong], self.now), ([self.maintenance, conflict], self.now),
                          ([failed], self.now), ([self.maintenance], datetime(2026, 9, 30, 9, tzinfo=KST))):
            with self.subTest(rows=rows, now=now):
                facts = analyze_notice(self.article, related_articles=rows, now=now)['windows']
                self.assertTrue(all(f['start'] is None and f['end'] for f in facts))

    def test_combined_numeric_range_and_use_alias(self):
        self.article['body'] = self.article['body'].replace('점검 후', '11:30')
        facts = analyze_notice(self.article)['windows']
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0]['start'], facts[1]['start'])
        self.article['body'] = '■ 클래스 변경권\n이용 기간: 9/30 11:30 ~ 10/7 08:00'
        self.assertEqual(analyze_notice(self.article)['windows'][0]['kind'], 'class_use')

    def test_unknown_end_or_invalid_date_is_not_claimed_confirmed(self):
        for end in ('10월 7일(수) 점검 전', '10월 32일(수) 08:00', '10월 7일(수) 25:00'):
            self.article['body'] = '클래스 변경권 판매 및 클래스 변경 기간: 9월 30일(수) 점검 후 ~ ' + end
            facts = analyze_notice(self.article)['windows']
            self.assertTrue(facts)
            self.assertTrue(all(f['issue'] and f['end'] is None for f in facts))


class SpokenTitleTests(unittest.TestCase):
    def test_numeric_dates_with_optional_year_weekday(self):
        for raw, expected in (
            ('9/30(수)', '9월 30일 수요일'), ('27/9/30(수)', '27년 9월 30일 수요일'),
            ('2027/09/30(수요일)', '2027년 9월 30일 수요일'), ('9/30 ~ 10/7', '9월 30일 ~ 10월 7일'),
            ('9/31(수)', '9/31(수)'), ('127/9/30(수)', '127/9/30(수)'),
            ('27/2/29(월)', '27/2/29(월)')):
            with self.subTest(raw=raw):
                self.assertEqual(spoken_title_dates(raw), expected)

    def test_stored_title_and_values_unchanged(self):
        values = {'공지제목': '9/30(수) 업데이트 후 확인된 문제 안내'}
        original = deepcopy(values)
        text = render_template({}, 'discovery.issue', values)
        self.assertIn('9월 30일 수요일 업데이트 후 확인된 문제 안내', text)
        self.assertNotIn('9/30', text)
        self.assertEqual(values, original)


if __name__ == '__main__':
    unittest.main()
