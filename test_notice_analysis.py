"""Offline period extraction and collection reconciliation; never plays audio."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from notice_module.payload.notice_analysis import analyze_notice, notice_events
from notice_module.payload.notice_management import KST, NoticeStore, event_status


def source(body, category="transfer", title="서버 이전 안내"):
    return dict(id="CT9G/1957", url="https://m.cafe.daum.net/odin/CT9G/1957",
                category=category, title=title, body=body, published_date="2026-09-09",
                first_seen="2026-09-09T18:00:00+09:00")


TRANSFER = """Ⅰ. 서버 이전 일정
1) 서버 이전권 판매 기간
· 1회차: 2026년 9월 10일(목) 13:00 ~ 23:59
· 2회차: 2026년 9월 12일(토) 13:00 ~ 23:59
2) 서버 이전 기간
· 2026년 9월 10일(목) 13:00 ~ 2026년 9월 14일(월) 23:59
3) 판매 수량
Ⅱ. 서버 이전권 관련 제작식 안내
· 제작 가능 기간: 2026년 9월 16일(수) 08:00 ~ 2026년 9월 23일(수) 점검 전까지
"""


class AnalysisTests(unittest.TestCase):
    def test_sale_slots_and_transfer_period_are_separate_from_crafting(self):
        article = source(TRANSFER)
        result = analyze_notice(article)
        self.assertEqual([f["kind"] for f in result["windows"]],
                         ["transfer_sale", "transfer_sale", "transfer_use"])
        self.assertEqual(result["windows"][2]["end"], "2026-09-14T23:59:00+09:00")
        events = notice_events(article, result)
        self.assertEqual(len(events), 10)
        first = {e["id"].split("/")[-1]: e for e in events[:4]}
        self.assertEqual(first["preview"]["trigger"], "major_boss_after")
        self.assertEqual(first["deadline_60"]["valid_from"].strftime("%H:%M"), "22:59")
        self.assertEqual(first["deadline_10"]["valid_from"].strftime("%H:%M"), "23:49")
        self.assertEqual(first["start"]["valid_until"].strftime("%H:%M"), "13:15")

    def test_maintenance_time_and_extension_text(self):
        article = source("점검 일시: 9/9(수) 오전 7시 ~ 오전 11시 30분", "maintenance", "정기점검 연장 안내")
        article["first_seen"] = "2026-09-09T10:00:00+09:00"
        result = analyze_notice(article)
        self.assertEqual(result["windows"][0]["start"], "2026-09-09T07:00:00+09:00")
        events = notice_events(article, result)
        self.assertEqual(len(events), 1)
        self.assertIn("정기점검 연장 공지가", events[0]["tts_text"])
        self.assertEqual(events[0]["valid_until"].hour, 11)

    def test_ended_maintenance_does_not_announce_discovery(self):
        article = source("점검 시간: 9/9 07:00 ~ 11:00", "maintenance")
        self.assertEqual(notice_events(article, analyze_notice(article)), [])

    def test_purchase_and_use_are_distinct_and_unrelated_item_is_ignored(self):
        article = source("""■ 클래스 변경권
구매 기간: 9/10 13:00 ~ 9/12 23:59
사용 기간: 9/10 13:00 ~ 9/14 23:59
■ 다른 상품
판매 기간: 9/15 10:00 ~ 9/16 10:00""", "class_change")
        facts = analyze_notice(article)["windows"]
        self.assertEqual([f["kind"] for f in facts], ["class_purchase", "class_use"])
        self.assertNotEqual(facts[0]["end"], facts[1]["end"])

    def test_invalid_or_ambiguous_ranges_are_held(self):
        for period in ("9/10 13:00 ~ 점검 전", "9/10 25:00 ~ 26:00",
                       "9/31 13:00 ~ 23:59", "9/10 23:00 ~ 01:00",
                       "9/10 13:00 ~ 23:59:30", "26.09.10 13:00 ~ 23:59",
                       "1/1 13:00 ~ 23:59"):
            with self.subTest(period=period):
                article = source("서버 이전권 판매 기간\n" + period)
                result = analyze_notice(article)
                self.assertEqual(len(result["windows"]), 1)
                self.assertTrue(result["windows"][0]["issue"])
                event = notice_events(article, result)[0]
                self.assertEqual(event["tts_text"], "이전권 판매 공지가 올라왔습니다.")
                self.assertIsNone(event["event_until"])
                self.assertTrue(event["retire_after_delivery"])

    def test_explicit_cross_year_and_midnight(self):
        article = source("서버 이전권 판매 기간\n2026.12.31 13:00 ~ 2027.01.01 24:00")
        fact = analyze_notice(article)["windows"][0]
        self.assertEqual(fact["end"], "2027-01-02T00:00:00+09:00")

    def test_duplicate_slot_is_not_silently_overwritten(self):
        article = source("서버 이전권 판매 기간\n1회차: 9/10 13:00 ~ 23:59\n1회차: 9/12 13:00 ~ 23:59")
        result = analyze_notice(article)
        self.assertEqual(len(result["windows"]), 1)
        self.assertTrue(result["windows"][0]["issue"])
        self.assertEqual(len(notice_events(article, result)), 1)

    def test_multiple_uncertain_slots_make_only_one_discovery(self):
        article = source("서버 이전권 판매 기간\n1회차: 9/10 13:00 ~ 추후 공지\n2회차: 9/12 13:00 ~ 점검 전")
        events = notice_events(article, analyze_notice(article))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["valid_until"] - events[0]["valid_from"], timedelta(hours=24))

    def test_unknown_event_has_generic_message_not_invented_deadline(self):
        article = source("기간은 이미지 참고", "event", "아이템 이벤트 안내")
        event = notice_events(article, analyze_notice(article))[0]
        self.assertEqual(event["tts_text"], "이벤트 공지가 올라왔습니다.")
        self.assertIsNone(event["event_from"])
        self.assertIsNone(event["event_until"])

    def test_multiple_maintenance_windows_require_final_time_review(self):
        article = source("점검 시간\n9/10 07:00 ~ 11:00\n9/10 07:00 ~ 12:00", "maintenance")
        self.assertTrue(all(f["issue"] for f in analyze_notice(article)["windows"]))

    def test_update_discovery_has_no_invented_event_deadline(self):
        article = source("업데이트 내용", "update")
        events = notice_events(article, analyze_notice(article))
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["retire_after_delivery"])
        self.assertIsNone(events[0]["event_until"])

    def test_participation_is_not_automatically_treated_as_general_notice(self):
        article = source("길던 모임", "participation")
        self.assertEqual(notice_events(article, analyze_notice(article)), [])


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-notice-analysis-unit-")
        self.addCleanup(temp.cleanup)
        self.store = NoticeStore(Path(temp.name), "odin9", clock=lambda: datetime(2026, 9, 9, 18, tzinfo=KST))
        self.article = source("점검 일시: 9/10 07:00 ~ 11:00", "maintenance", "정기점검 안내")
        settings = self.store.snapshot()["settings"]
        settings["output_enabled"] = True
        self.store.configure(settings)

    def collect(self, *, failure=False):
        row = {k: self.article[k] for k in ("id", "url", "title", "category", "published_date")}
        body = dict(self.article, content_hash=self.article["title"] + self.article["body"])
        token, _ = self.store.claim_collection(manual=True)
        self.store.finish_collection(token, [row], {} if failure else {row["id"]: body},
                                     {row["id"]: "network failure"} if failure else {})
        return self.store.snapshot()

    def event(self):
        return self.store.snapshot()["events"]["CT9G/1957/time-v1/maintenance/1/discovery"]

    def unknown(self):
        self.article["body"] = "점검 일시: 추후 공지"
        return self.collect()["events"]["CT9G/1957/time-v1/unknown-discovery"]

    def test_unknown_discovery_retires_only_after_actual_success(self):
        event = self.unknown()
        token = self.store.delivery_token(event["id"])
        self.assertIsNotNone(token)
        self.collect()
        self.assertNotIn("retired_at", self.store.snapshot()["events"][event["id"]])
        self.assertTrue(self.store.complete_delivery(token))
        retired = self.store.snapshot()["events"][event["id"]]
        self.assertEqual(retired["retired_reason"], "1회 안내 완료 · 자동 폐기")
        self.assertIsNotNone(retired["last_delivery"])
        self.assertFalse(self.store.complete_delivery(token))
        self.collect()
        self.assertIsNone(self.store.delivery_token(event["id"]))

    def test_discovery_does_not_repeat_after_restart_edit_unpin_or_history_delete(self):
        event = self.unknown()
        self.store.complete_delivery(self.store.delivery_token(event["id"]))
        self.store.delete_history()
        token, _ = self.store.claim_collection(manual=True)
        self.store.finish_collection(token, [], {}, {})
        self.store.reconcile_sources([], successful=True)
        self.store = NoticeStore(self.store.root, "odin9", clock=self.store.clock)
        self.article["body"] += "\n수정된 설명"
        self.assertNotIn(event["id"], self.collect()["events"])
        self.assertIn(self.article["id"], self.store.snapshot()["articles"])

    def test_general_notice_does_not_repeat_on_edit_unpin_and_history_removal(self):
        self.article.update(category="update", title="업데이트 안내", body="업데이트 내용")
        event = next(iter(self.collect()["events"].values()))
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token(event["id"])))
        self.store.delete_history()
        token, _ = self.store.claim_collection(manual=True)
        self.store.finish_collection(token, [], {}, {})
        self.article["body"] += "\n본문 수정"
        self.assertNotIn(event["id"], self.collect()["events"])

    def test_title_date_upgrade_does_not_replay_completed_one_shot(self):
        self.article.update(category='update', title='9/9(수) 업데이트 후 확인된 문제 안내', body='문제 안내')
        with patch('notice_module.payload.notice_templates.spoken_title_dates', side_effect=lambda value: value):
            event = next(iter(self.collect()['events'].values()))
            self.assertIn('9/9(수)', event['tts_text'])
            self.assertTrue(self.store.complete_delivery(self.store.delivery_token(event['id'])))
        self.collect()
        self.assertIsNone(self.store.delivery_token(event['id']))

    def test_same_collection_links_class_to_maintenance_even_when_listed_later(self):
        self.store.clock = lambda: datetime(2026, 10, 1, 10, tzinfo=KST)
        article = source('클래스 변경권 판매 및 클래스 변경 기간: 9월 30일(수) 점검 후 ~ 10월 7일(수) 08:00',
                         'class_change', '클래스 변경 시즌 15 안내')
        article.update(published_date='2026-09-30', first_seen=self.store.clock().isoformat())
        maintenance = source('점검 일정\n9월 30일(수) 08:00 ~ 11:30', 'maintenance', '9/30(수) 정기 점검 안내')
        maintenance.update(id='CT9G/1970', published_date='2026-09-29')
        listing = [{k: item[k] for k in ('id', 'url', 'title', 'category', 'published_date')}
                   for item in (article, maintenance)]
        fetched = {item['id']: dict(item, content_hash=item['body']) for item in (article, maintenance)}
        token, _ = self.store.claim_collection(manual=True)
        self.store.finish_collection(token, listing, fetched, {})
        state = self.store.snapshot()
        for fact in state['articles'][article['id']]['analysis']['windows']:
            self.assertEqual(fact['start'], '2026-09-30T11:30:00+09:00')
        self.assertEqual(len([e for e in state['events'].values() if e['category'] == 'class_change']), 3)

    def test_unknown_expiry_does_not_extend_with_each_collection(self):
        event = self.unknown()
        now = self.store.clock()
        self.store.clock = lambda: now + timedelta(hours=25)
        state = self.collect()
        self.assertEqual(state["events"][event["id"]]["valid_until"], event["valid_until"])
        self.assertEqual(state["events"][event["id"]]["retired_reason"], "유효기간 만료")

    def test_disabled_discovery_is_not_marked_delivered(self):
        event = self.unknown()
        token = self.store.delivery_token(event["id"])
        self.store.set_enabled(event["id"], False)
        self.assertFalse(self.store.complete_delivery(token))
        self.collect()
        stored = self.store.snapshot()["events"][event["id"]]
        self.assertFalse(stored["enabled"])
        self.assertIsNone(stored["last_delivery"])

    def test_later_confirmed_period_cancels_pending_generic_and_creates_dated_notice(self):
        event = self.unknown()
        token = self.store.delivery_token(event["id"])
        self.article["body"] = "점검 일시: 9/10 07:00 ~ 11:00"
        state = self.collect()
        self.assertFalse(self.store.complete_delivery(token))
        self.assertIn("retired_at", state["events"][event["id"]])
        self.assertIsNotNone(self.store.delivery_token(self.event()["id"]))

    def test_noop_and_cosmetic_change_do_not_repeat_delivered_notice(self):
        self.collect()
        event = self.event()
        token = self.store.delivery_token(event["id"])
        self.assertIsNotNone(token)
        self.assertTrue(self.store.complete_delivery(token))
        self.article["body"] += "\n이용해 주셔서 감사합니다."
        self.collect()
        self.assertEqual(self.event()["revision"], event["revision"])
        self.assertIsNone(self.store.delivery_token(event["id"]))

    def test_meaningful_time_change_invalidates_old_token(self):
        self.collect()
        old = self.event()
        token = self.store.delivery_token(old["id"])
        self.article["body"] = "점검 일시: 9/10 07:00 ~ 12:00"
        self.collect()
        self.assertGreater(self.event()["revision"], old["revision"])
        self.assertFalse(self.store.complete_delivery(token))

    def test_uncertain_edit_holds_old_event_but_fetch_failure_preserves_it(self):
        self.collect()
        key = self.event()["id"]
        self.collect(failure=True)
        self.assertIsNotNone(self.store.delivery_token(key))
        self.article["body"] = "점검 일시: 9/10 07:00 ~ 추후 안내"
        self.collect()
        self.assertIn("실행 보류", event_status(self.event(), self.store.clock()))
        self.assertIsNone(self.store.delivery_token(key))
        self.article["body"] = "점검 일시: 9/10 07:00 ~ 11:00"
        self.collect()
        self.assertIsNotNone(self.store.delivery_token(key))

    def test_manual_checkbox_and_period_survive_collection_and_discard_is_final(self):
        self.collect()
        key = self.event()["id"]
        self.store.set_enabled(key, False)
        self.store.edit_period(key, "2026-09-09 19:00", "2026-09-10 10:00")
        self.article["body"] = "점검 일시: 9/10 07:00 ~ 12:00"
        self.collect()
        event = self.event()
        self.assertFalse(event["enabled"])
        self.assertEqual(event["valid_until"], "2026-09-10T10:00:00+09:00")
        self.store.discard(key)
        self.collect()
        self.assertEqual(self.event()["retired_reason"], "사용자 폐기")


if __name__ == "__main__":
    unittest.main()
