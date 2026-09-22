"""Synthetic boss opportunities and persistent quotas; no live game or audio."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from notice_module.payload.notice_management import KST, NoticeStore, NoticeError
from notice_module.payload.notice_analysis import analyze_notice, notice_events
from test_notice_analysis import source, TRANSFER


class OpportunityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-notice-opportunity-unit-")
        self.addCleanup(temp.cleanup)
        self.now = datetime(2026, 9, 10, 18, tzinfo=KST)
        self.store = NoticeStore(Path(temp.name), "odin9", clock=lambda: self.now)
        settings = self.store.snapshot()["settings"]
        settings["output_enabled"] = True
        self.store.configure(settings)

    def register(self, key="guild", category="participation", **extra):
        event = {"id": key, "title": key, "category": category, "tts_text": "참여해 주세요.",
                 "valid_from": "2026-09-10 00:00", "valid_until": "2026-09-13 00:00",
                 "event_from": "2026-09-12 23:00" if category == "participation" else "2026-09-10 00:00",
                 "event_until": "2026-09-11 18:00" if category == "class_change" else "2026-09-13 00:00"}
        event.update(extra)
        self.store.register(event)

    def context(self, key="boss1", **changes):
        result = {"id": key, "server_id": "odin9", "at": self.now, "kind": "boss", "chapter": 8,
                  "major": True, "phase": "one_minute_complete"}
        result.update(changes)
        return result

    def deliver(self, key, occurrence):
        token = self.store.delivery_token(key, opportunity=self.context(occurrence))
        self.assertIsNotNone(token)
        self.assertTrue(self.store.complete_delivery(token))
        return token

    def test_participation_total_two_across_targets_restart_and_history_deletion(self):
        for key in ("guild", "bosses", "absolute"):
            self.register(key)
        token = self.deliver("guild", "one")
        self.assertFalse(self.store.complete_delivery(token))
        self.now += timedelta(minutes=59)
        self.assertIsNone(self.store.delivery_token("bosses", opportunity=self.context("two")))
        self.now += timedelta(minutes=1)
        self.deliver("bosses", "two")
        self.store.discard("guild")
        self.store.delete_history()
        self.store = NoticeStore(self.store.root, "odin9", clock=lambda: self.now)
        self.now += timedelta(hours=1)
        self.assertIsNone(self.store.delivery_token("absolute", opportunity=self.context("three")))
        self.now += timedelta(days=1)
        self.deliver("absolute", "next-day")

    def test_participation_time_and_target_boundary(self):
        self.register(event_from="2026-09-10 18:30")
        self.assertIsNone(self.store.delivery_token("guild", opportunity=self.context()))
        self.register(event_from="2026-09-10 18:31")
        self.assertIsNotNone(self.store.delivery_token("guild", opportunity=self.context()))
        self.register(event_from="2026-09-12 23:00")
        for hour in (6, 17, 23):
            self.now = self.now.replace(hour=hour)
            self.assertIsNone(self.store.delivery_token("guild", opportunity=self.context()))

    def test_missing_target_is_rejected(self):
        with self.assertRaises(NoticeError):
            self.register(event_from=None)

    def test_transfer_more_than_two_without_consuming_participation_quota(self):
        self.register("sale", "transfer", policy="transfer_repeat")
        self.register()
        for index in range(3):
            self.deliver("sale", str(index))
            self.now += timedelta(minutes=30)
        self.deliver("guild", "guild")

    def test_transfer_gap_and_same_opportunity_cannot_be_reused(self):
        self.register("sale", "transfer", policy="transfer_repeat")
        self.deliver("sale", "same")
        self.now += timedelta(minutes=29)
        self.assertIsNone(self.store.delivery_token("sale", opportunity=self.context("new")))
        self.now += timedelta(minutes=1)
        self.assertIsNone(self.store.delivery_token("sale", opportunity=self.context("same")))
        self.deliver("sale", "new")

    def test_candidate_selection_is_readonly_and_prioritizes_transfer(self):
        self.register()
        self.register("sale", "transfer", policy="transfer_repeat")
        context = self.context()
        one = self.store.opportunity_candidate(context)
        self.assertEqual(one["id"], "sale")
        self.assertEqual(one, self.store.opportunity_candidate(context))
        self.assertNotIn("delivery_ledger", self.store.snapshot())
        self.assertTrue(self.store.complete_delivery(one["token"]))
        self.assertIsNone(self.store.opportunity_candidate(context))

    def test_only_completed_matching_server_recent_major_boss_is_allowed(self):
        self.register("sale", "transfer", policy="transfer_repeat")
        for changes in ({"server_id": "odin8"}, {"phase": "started"}, {"chapter": 2}, {"chapter": 3},
                        {"chapter": 4}, {"major": False}, {"at": self.now - timedelta(seconds=121)},
                        {"at": self.now + timedelta(seconds=1)}, {"kind": "fixed", "fixed_kind": "goblin"}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.store.delivery_token("sale", opportunity=self.context(**changes)))
        for changes in ({"kind": "fixed", "fixed_kind": "valhalla"},
                        {"kind": "fixed", "fixed_kind": "world_boss"}, {"absolute": True, "major": False}):
            self.assertIsNotNone(self.store.delivery_token("sale", opportunity=self.context(**changes)))

    def test_conditional_once_cannot_bypass_opportunity_check(self):
        self.register("preview", "transfer", trigger="major_boss_after")
        self.assertIsNone(self.store.delivery_token("preview"))
        token = self.store.delivery_token("preview", opportunity=self.context())
        self.assertFalse(self.store.complete_delivery(token[:4]))
        self.assertTrue(self.store.complete_delivery(token))
        self.now += timedelta(hours=1)
        self.assertIsNone(self.store.delivery_token("preview", opportunity=self.context("second")))

    def test_disable_expiry_and_context_expiry_reject_pending_token(self):
        self.register("sale", "transfer", policy="transfer_repeat")
        token = self.store.delivery_token("sale", opportunity=self.context())
        self.store.set_enabled("sale", False)
        self.assertFalse(self.store.complete_delivery(token))
        self.store.set_enabled("sale", True)
        token = self.store.delivery_token("sale", opportunity=self.context())
        self.now += timedelta(seconds=121)
        self.assertFalse(self.store.complete_delivery(token))
        self.assertNotIn("delivery_ledger", self.store.snapshot())
        token = self.store.delivery_token("sale", opportunity=self.context("new"))
        self.now = datetime(2026, 9, 13, tzinfo=KST)
        self.assertFalse(self.store.complete_delivery(token))

    def test_deadline_quota_shared_by_purchase_and_use_for_same_article(self):
        for suffix in ("buy", "use"):
            self.register(suffix, "class_change", policy="deadline_repeat", source_key=f"CT9G/123/time-v1/{suffix}")
        self.deliver("buy", "one")
        self.now += timedelta(hours=1)
        self.deliver("use", "two")
        self.now += timedelta(hours=1)
        self.assertIsNone(self.store.delivery_token("buy", opportunity=self.context("three")))

    def test_recollection_keeps_quota_and_noop_preserves_repeat_revision(self):
        self.register("sale", "transfer", policy="transfer_repeat")
        self.deliver("sale", "one")
        old = self.store.snapshot()["events"]["sale"]["revision"]
        self.register("sale", "transfer", policy="transfer_repeat")
        self.assertEqual(self.store.snapshot()["events"]["sale"]["revision"], old)
        self.assertIsNone(self.store.delivery_token("sale", opportunity=self.context("two")))

    def test_ledger_retention_is_thirty_days(self):
        self.register()
        self.deliver("guild", "one")
        self.now += timedelta(days=30)
        self.assertEqual(self.store.snapshot()["delivery_ledger"], [])

    def test_generated_transfer_rule_is_live_only_during_sale(self):
        article = source(TRANSFER)
        event = next(e for e in notice_events(article, analyze_notice(article)) if e.get("policy") == "transfer_repeat")
        self.store.register(event, collected=True)
        self.assertIsNotNone(self.store.delivery_token(event["id"], opportunity=self.context()))
        self.now = self.now.replace(hour=23, minute=59)
        self.assertIsNone(self.store.delivery_token(event["id"], opportunity=self.context()))

    def test_manual_validity_extension_does_not_extend_actual_sale(self):
        self.register("sale", "transfer", policy="transfer_repeat", event_until="2026-09-10 19:00")
        self.store.edit_period("sale", "2026-09-10 00:00", "2026-09-15 00:00")
        self.now = self.now.replace(hour=19)
        self.assertIsNone(self.store.delivery_token("sale", opportunity=self.context()))

    def test_class_purchase_and_use_generate_different_deadline_messages(self):
        article = source("■ 클래스 변경권\n구매 기간: 9/10 13:00 ~ 9/12 23:59\n사용 기간: 9/10 13:00 ~ 9/14 23:59", "class_change")
        events = notice_events(article, analyze_notice(article))
        self.assertEqual(len(events), 3)
        deadlines = [e for e in events if e.get("policy") == "deadline_repeat"]
        self.assertEqual(len(deadlines), 2)
        self.assertIn("구매해 주세요", deadlines[0]["tts_text"])
        self.assertIn("사용해 주세요", deadlines[1]["tts_text"])
        self.assertNotEqual(deadlines[0]["valid_until"], deadlines[1]["valid_until"])

    def test_explicit_item_deletion_deadline_does_not_need_invented_start(self):
        article = source("삭제 일시: 9/10 23:59", "event", "소환 아이템 이벤트")
        event = notice_events(article, analyze_notice(article))[0]
        self.assertIsNone(event["event_from"])
        self.store.register(event, collected=True)
        self.deliver(event["id"], "item-one")
        self.now += timedelta(hours=1)
        self.deliver(event["id"], "item-two")
        self.now += timedelta(hours=1)
        self.assertIsNone(self.store.delivery_token(event["id"], opportunity=self.context("item-three")))


if __name__ == "__main__":
    unittest.main()
