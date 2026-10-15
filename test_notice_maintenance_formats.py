"""Maintenance formats and missing publication metadata; offline only."""
from datetime import datetime
from unittest import TestCase

from notice_module.payload.cafe_source import category_for, date_of, parse_article
from notice_module.payload.notice_analysis import analyze_notice, notice_events
from notice_module.payload.notice_management import KST
from notice_module.payload.notice_server_open import server_open_alarm, server_open_override


BODY = """오딘 발할라 라이징의 업데이트 점검 일정을 안내드립니다.
【 10/14(수) 정기 점검 안내 】
Ⅰ. 점검 일정
- 10월 14일(수) 08:00 ~ 11:00
Ⅱ. 점검 내용
1) 주요 사항
- 게임 시스템 개선
※ 유의사항 ※
- 점검 상황에 따라 점검이 조기 종료 또는 연장될 수 있습니다.
"""


def article(body=BODY, **changes):
    return dict(dict(id="CT9G/1981", url="https://m.cafe.daum.net/odin/CT9G/1981",
                     title="10/14(수) 정기 점검 안내", category="maintenance", body=body,
                     published_date=None, first_seen="2026-10-13T18:26:59+09:00"), **changes)


class MaintenanceFormatsTests(TestCase):
    def test_actual_october_notice_missing_publication_reads_time_and_alarm(self):
        item = article()
        item["analysis"] = analyze_notice(item)
        fact, = item["analysis"]["windows"]
        self.assertEqual(fact["start"], "2026-10-14T08:00:00+09:00")
        self.assertEqual(fact["end"], "2026-10-14T11:00:00+09:00")
        self.assertIn("최초 수집일", fact["date_reference"])
        self.assertIsNone(item["published_date"])
        event, = notice_events(item, item["analysis"])
        self.assertEqual(event["event_until"], fact["end"])
        state = {"articles": {item["id"]: item}}
        now = datetime(2026, 10, 14, 9, tzinfo=KST)
        self.assertEqual(server_open_alarm(state, now)["server_open"], fact["end"])
        self.assertIsNone(server_open_override(state, now))
        self.assertEqual(server_open_override(state, now.replace(hour=11))["server_open"], fact["end"])

    def test_spaces_fullwidth_keywords_weekdays_and_time_delimiters(self):
        for body in (
            "Ⅰ . 정 기 점 검 일 정\n10 월 14 일 ( 수 요 일 ) 08 : 00 ～ 11 : 00",
            "점\u200b검 시 간\n１０／１４（수） ０８：００ — １１：００",
            "점 검 진행 시 간\n2026 / 10 / 14 ( 수 ) 오 전 8 시 부터 오 전 11 시 까지",
            "점검 일시\n10 / 14 (수) 08 : 00 - 11 : 00",
        ):
            with self.subTest(body=body):
                fact, = analyze_notice(article(body))["windows"]
                self.assertEqual(fact["start"], "2026-10-14T08:00:00+09:00")
                self.assertEqual(fact["end"], "2026-10-14T11:00:00+09:00")
                self.assertEqual(fact["issue"], "")
        self.assertEqual(category_for("정 기 점\u200b검 안 내"), "maintenance")

    def test_missing_distant_or_invalid_reference_stays_held(self):
        for changes in ({"first_seen": None}, {"first_seen": "invalid"},
                        {"first_seen": "2026-07-13T18:00:00+09:00"}):
            with self.subTest(changes=changes):
                fact, = analyze_notice(article(**changes))["windows"]
                self.assertTrue(fact["issue"])
                self.assertIsNone(fact["start"])
        item = article("이전권 판매 기간\n10/14 08:00 ~ 11:00", category="transfer")
        self.assertTrue(analyze_notice(item)["windows"][0]["issue"])

    def test_reference_is_first_seen_not_current_collection_year(self):
        item = article("점검 일정\n1/1 08:00 ~ 11:00", first_seen="2026-12-31T18:00:00+09:00")
        self.assertEqual(analyze_notice(item)["windows"][0]["start"], "2027-01-01T08:00:00+09:00")
        later = datetime(2028, 12, 31, tzinfo=KST)
        self.assertEqual(analyze_notice(item, now=later), analyze_notice(item))

    def test_invalid_or_partial_times_do_not_become_confirmed(self):
        for value in ("25:00 ~ 26:00", "08:00 ~ 11:00:30", "08:00 ~ 11 : 00 : 30",
                      "8시 ~ 11시 15분 30초", "08:00 ~ 점검 전", "11:00 ~ 08:00"):
            with self.subTest(value=value):
                fact, = analyze_notice(article("점검 시간\n10/14 " + value))["windows"]
                self.assertTrue(fact["issue"])
        item = article("점검 시간\n26 . 10 . 14 08:00 ~ 11:00")
        self.assertTrue(all(f["issue"] for f in analyze_notice(item)["windows"]))

    def test_rewards_after_maintenance_section_are_not_another_window(self):
        item = article("점검 일정\n10/14 08:00 ~ 11:00\n점 검 보 상 지급 기간\n10/14 11:00 ~ 10/15 23:59")
        fact, = analyze_notice(item)["windows"]
        self.assertEqual(fact["end"], "2026-10-14T11:00:00+09:00")

    def test_publication_formats_and_metadata_do_not_use_body_or_comments(self):
        now = datetime(2026, 10, 13, 18, tzinfo=KST)
        for label in ("26. 10. 13", "2026 년 10 월 13 일", "２０２６／１０／１３", "2 시 간 4 분 전"):
            self.assertEqual(date_of(label, now), "2026-10-13")
        listing = {"published_date": None}
        base = '<span class="article_title">정기점검</span><div id="article" class="tx-content-container">10/14 08:00 ~ 11:00</div>'
        for metadata in ('<meta property="article:published_time" content="2026-10-12T23:30:00Z">',
                         '<time datetime="2026-10-13T08:30:00+09:00"></time>',
                         '<span class="created_at">2026 년 10 월 13 일</span>'):
            self.assertEqual(parse_article(metadata + base, listing, now)["published_date"], "2026-10-13")
        comments = '<div class="comments"><time datetime="2025-01-01"></time></div>'
        views = '<span class="num_subject">20261013</span>'
        self.assertIsNone(parse_article(base + comments + views, listing, now)["published_date"])
