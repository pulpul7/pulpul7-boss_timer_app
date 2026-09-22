"""Offline editable speech, item deadlines and persistent season rotation."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from notice_module.payload.notice_management import KST, NoticeStore, NoticeError, event_status
from notice_module.payload.notice_analysis import analyze_notice, notice_events
from notice_module.payload.notice_seasons import CHEERS
from test_notice_analysis import source


class TextTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-notice-text-unit-")
        self.addCleanup(temp.cleanup)
        self.now = datetime(2026, 9, 10, 18, tzinfo=KST)
        self.store = NoticeStore(Path(temp.name), "odin9", clock=lambda: self.now)
        settings = self.store.snapshot()["settings"]
        settings["output_enabled"] = True
        self.store.configure(settings)
        self.raw = {"id": "test", "title": "점검", "category": "maintenance", "source_key": "CT9G/1/test",
                    "valid_from": "2026-09-10 00:00", "valid_until": "2026-09-11 12:00",
                    "event_from": "2026-09-11 07:00", "event_until": "2026-09-11 11:00", "tts_text": "11시 종료입니다."}
        self.store.register(self.raw, collected=True)

    def item(self):
        return self.store.snapshot()["events"]["test"]

    def test_manual_text_survives_recollection_and_restart(self):
        self.store.edit_tts("test", "직접 쓴 안내\n두 번째 줄")
        self.store.register(self.raw, collected=True)
        self.store = NoticeStore(self.store.root, "odin9", clock=lambda: self.now)
        self.assertEqual(self.item()["tts_text"], "직접 쓴 안내\n두 번째 줄")
        self.assertEqual(self.item()["generated_tts_text"], "11시 종료입니다.")
        self.assertTrue(self.item()["manual_tts"])
        self.assertIsNotNone(self.store.delivery_token("test"))

    def test_changed_generated_time_holds_custom_text_until_reviewed(self):
        self.store.edit_tts("test", "11시까지 기다려 주세요.")
        token = self.store.delivery_token("test")
        self.raw.update(tts_text="12시 종료입니다.", event_until="2026-09-11 12:00")
        self.store.register(self.raw, collected=True)
        self.assertEqual(self.item()["tts_text"], "11시까지 기다려 주세요.")
        self.assertEqual(self.item()["generated_tts_text"], "12시 종료입니다.")
        self.assertIn("확인 필요", event_status(self.item(), self.now))
        self.assertIsNone(self.store.delivery_token("test"))
        self.assertFalse(self.store.complete_delivery(token))
        self.store.register(self.raw, collected=True)
        self.assertTrue(self.item()["tts_review_required"])
        self.store.edit_tts("test", "12시까지 기다려 주세요.", expected_revision=self.item()["revision"])
        self.store.register(self.raw, collected=True)
        self.assertIsNotNone(self.store.delivery_token("test"))

    def test_restore_uses_latest_generated_text_and_resumes_auto_updates(self):
        self.store.edit_tts("test", "편집")
        self.raw["tts_text"] = "새 자동 문장"
        self.store.register(self.raw, collected=True)
        self.store.edit_tts("test", "", use_generated=True)
        self.assertEqual(self.item()["tts_text"], "새 자동 문장")
        self.assertFalse(self.item()["manual_tts"])
        self.raw["tts_text"] = "다음 자동 문장"
        self.store.register(self.raw, collected=True)
        self.assertEqual(self.item()["tts_text"], "다음 자동 문장")

    def test_old_editor_cannot_overwrite_updated_event(self):
        revision = self.item()["revision"]
        self.raw["tts_text"] = "새 문장"
        self.store.register(self.raw, collected=True)
        with self.assertRaises(NoticeError):
            self.store.edit_tts("test", "오래된 창", expected_revision=revision)

    def test_blank_blocks_speech_and_editing_does_not_reenable_checkbox(self):
        self.store.edit_tts("test", "  ")
        self.assertIsNone(self.store.delivery_token("test"))
        self.store.set_enabled("test", False)
        self.store.edit_tts("test", "새 안내")
        self.assertFalse(self.item()["enabled"])
        self.assertIsNone(self.store.delivery_token("test"))

    def test_completed_once_is_not_replayed_by_text_edit(self):
        self.store.complete_delivery(self.store.delivery_token("test"))
        self.store.edit_tts("test", "수정한 안내")
        self.assertIsNone(self.store.delivery_token("test"))
        self.store.register(self.raw, collected=True)
        self.assertIsNone(self.store.delivery_token("test"))

    def test_deleted_event_cannot_be_resurrected_by_editor(self):
        self.store.discard("test")
        with self.assertRaises(NoticeError):
            self.store.edit_tts("test", "복구")


class ItemTests(unittest.TestCase):
    def test_drop_exchange_use_delete_are_distinct(self):
        article = source("""획득 기간: 9/10 13:00 ~ 9/20 23:59
교환 기간: 9/10 13:00 ~ 9/21 23:59
사용 기간: 9/10 13:00 ~ 9/22 23:59
삭제 일시: 9/23 08:00""", "event", "소환 아이템 이벤트")
        analysis = analyze_notice(article)
        self.assertEqual([f["kind"] for f in analysis["windows"]], ["item_drop", "item_exchange", "item_use", "item_delete"])
        events = notice_events(article, analysis)
        self.assertEqual(len(events), 4)
        self.assertTrue(all(e["policy"] == "deadline_repeat" for e in events))
        self.assertEqual(events[-1]["event_until"], "2026-09-23T08:00:00+09:00")
        self.assertIsNone(events[-1]["event_from"])

    def test_multiline_deadline_accepts_explicit_time_not_date_only(self):
        for line, valid in (("9/23 08:00", True), ("9/23", False), ("9/23 점검 전", False)):
            article = source("사용 기한\n" + line, "event")
            events = notice_events(article, analyze_notice(article))
            self.assertEqual(events[0].get("policy") == "deadline_repeat", valid)

    def test_broken_range_does_not_become_a_point_deadline(self):
        article = source("사용 기한: 9/10 13:00 ~ 점검 전", "event")
        events = notice_events(article, analyze_notice(article))
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["retire_after_delivery"])
        self.assertIsNone(events[0]["event_until"])

    def test_multiple_items_with_same_kind_are_held_for_review(self):
        article = source("사용 기간: 9/10 13:00 ~ 9/20 23:59\n사용 기간: 9/10 13:00 ~ 9/21 23:59", "event")
        analysis = analyze_notice(article)
        self.assertTrue(all(f["issue"] for f in analysis["windows"]))
        self.assertEqual(len(notice_events(article, analysis)), 1)


class SeasonTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-notice-season-unit-")
        self.addCleanup(temp.cleanup)
        self.now = datetime(2026, 9, 14, 23, 44, tzinfo=KST)
        self.store = NoticeStore(Path(temp.name), "odin9", clock=lambda: self.now)
        settings = self.store.snapshot()["settings"]
        settings["output_enabled"] = True
        self.store.configure(settings)

    def event(self, number=18, article_id="CT9G/1957"):
        article = source("서버 이전 기간: 9/10 13:00 ~ 9/14 23:59", title=f"{number}차 서버 이전 안내")
        article["id"] = article_id
        return next(e for e in notice_events(article, analyze_notice(article)) if e.get("season_number"))

    def test_rotation_five_distinct_texts_then_wraps(self):
        for index in range(6):
            event = self.event(18 + index, f"CT9G/{index}")
            self.store.register(event, collected=True)
            self.assertEqual(self.store.snapshot()["events"][event["id"]]["tts_text"], CHEERS[index % 5])

    def test_same_season_duplicate_and_history_delete_cannot_replay(self):
        event = self.event()
        self.store.register(event, collected=True)
        token = self.store.delivery_token(event["id"])
        self.assertIsNotNone(token)
        self.assertTrue(self.store.complete_delivery(token))
        self.store.delete_history()
        self.store = NoticeStore(self.store.root, "odin9", clock=lambda: self.now)
        duplicate = self.event(article_id="CT9G/2000")
        self.store.register(duplicate, collected=True)
        self.assertIsNone(self.store.delivery_token(duplicate["id"]))

    def test_season_timing_exactly_fifteen_minutes_and_never_after_end(self):
        event = self.event()
        self.store.register(event, collected=True)
        self.now -= timedelta(seconds=1)
        self.assertIsNone(self.store.delivery_token(event["id"]))
        self.now += timedelta(seconds=1)
        self.assertIsNotNone(self.store.delivery_token(event["id"]))
        self.store.edit_period(event["id"], "2026-09-10 00:00", "2026-09-16 00:00")
        self.now = self.now.replace(minute=59)
        self.assertIsNone(self.store.delivery_token(event["id"]))

    def test_season_custom_text_survives_rotation_and_recollection(self):
        event = self.event()
        self.store.register(event, collected=True)
        self.store.edit_tts(event["id"], "우리 길드 수고하셨습니다!")
        self.store.register(event, collected=True)
        self.assertEqual(self.store.snapshot()["events"][event["id"]]["tts_text"], "우리 길드 수고하셨습니다!")

    def test_unknown_season_number_does_not_guess_rotation(self):
        article = source("서버 이전 기간: 9/10 13:00 ~ 9/14 23:59")
        self.assertFalse(any(e.get("season_number") for e in notice_events(article, analyze_notice(article))))


if __name__ == "__main__":
    unittest.main()
