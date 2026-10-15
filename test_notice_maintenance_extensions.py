"""Same-article maintenance edits, using offline HTML and fake delivery only."""
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from notice_module.payload.cafe_source import parse_article
from notice_module.payload.notice_analysis import analyze_notice, notice_events
from notice_module.payload.notice_management import KST, NoticeStore
from notice_module.payload.notice_polling import polling_plan
from notice_module.payload.notice_text import maintenance_extended


def article(body, **changes):
    return dict(dict(id="CT9G/1981", url="https://m.cafe.daum.net/odin/CT9G/1981",
                     title="10/14(수) 정기 점검 안내", category="maintenance",
                     published_date="2026-10-13", first_seen="2026-10-13T18:00:00+09:00",
                     body=body), **changes)


def period(end):
    return "점검 일정\n10/14(수) 08:00 ~ " + end


class ExtensionAnalysisTests(unittest.TestCase):
    def test_unchanged_title_uses_extension_template_from_body(self):
        item = article(period("14:30") + " (3시간 30분 연장)")
        event, = notice_events(item, analyze_notice(item))
        self.assertEqual(event["tts_template"], "maintenance.regular.extension")
        self.assertIn("정기점검 연장", event["tts_text"])
        self.assertEqual(event["event_until"], "2026-10-14T14:30:00+09:00")

    def test_disclaimer_and_completed_title_are_not_extensions(self):
        self.assertFalse(maintenance_extended(article(period("11:00") + "\n점검은 연장될 수 있습니다.")))
        self.assertFalse(maintenance_extended(article("점검은 30분 연장될 수 있습니다.")))
        self.assertFalse(maintenance_extended(article(period("14:30") + " (3시간 30분 연장)",
                                                        title="정기점검 완료 안내")))
        self.assertTrue(maintenance_extended(article("점 검 연 장 안 내\n" + period("12:00"))))
        self.assertTrue(maintenance_extended(article("정기 점검 연장 공지\n" + period("12:00"))))

    def test_struck_original_and_prior_extension_are_excluded(self):
        for tag in ("s", "strike", "del", 'span style="text-decoration: line-through"'):
            close = tag.split()[0]
            html = ('<span class="article_title">10/14(수) 정기 점검 안내</span>'
                    '<div id="article" class="tx-content-container"><p>점검 일정</p>'
                    f'<{tag}>10/14(수) 08:00 ~ 11:00</{close}>'
                    f'<{tag}>10/14(수) 08:00 ~ 12:00 (1시간 연장)</{close}>'
                    '<p>점검 연장 안내</p><p>10/14(수) 08:00 ~ 14:30 (3시간 30분 연장)</p></div>')
            parsed = parse_article(html, article(""), datetime(2026, 10, 14, 11, tzinfo=KST))
            item = article(parsed["body"])
            with self.subTest(tag=tag):
                fact, = analyze_notice(item)["windows"]
                self.assertEqual(fact["end"], "2026-10-14T14:30:00+09:00")
                self.assertNotIn("11:00", parsed["body"])
                self.assertNotIn("12:00", parsed["body"])

    def test_unstruck_history_uses_latest_end_only_for_explicit_same_start_extension(self):
        item = article(period("11:00") + "\n【점검 연장 안내】\n10/14 08:00 ~ 12:00"
                       "\n【추가 연장 안내】\n10/14 08:00 ~ 14:30")
        fact, = analyze_notice(item)["windows"]
        self.assertEqual(fact["key"], "maintenance/1")
        self.assertEqual(fact["end"], "2026-10-14T14:30:00+09:00")
        self.assertFalse(fact["issue"])
        for body in (period("11:00") + "\n10/14 08:00 ~ 12:00",
                     period("11:00") + "\n점검 연장 안내\n10/14 09:00 ~ 12:00"):
            with self.subTest(body=body):
                self.assertTrue(all(f["issue"] for f in analyze_notice(article(body))["windows"]))

    def test_body_extension_also_uses_extension_polling_policy(self):
        item = article(period("12:00") + " (1시간 연장)", pinned=True)
        item["analysis"] = analyze_notice(item)
        state = dict(articles={item["id"]: item}, settings=dict(collection_enabled=True,
            categories={"maintenance": True}, rules=[dict(days="매일", start="00:00", end="24:00", minutes=10)]))
        self.assertEqual(polling_plan(state, datetime(2026, 10, 14, 11, 5, tzinfo=KST))["mode"], "extension_wait")


class ExtensionLifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="boss-notice-extension-unit-")
        self.addCleanup(temporary.cleanup)
        self.now = datetime(2026, 10, 14, 10, 50, tzinfo=KST)
        self.store = NoticeStore(Path(temporary.name), "9", clock=lambda: self.now)
        settings = self.store.snapshot()["settings"]
        settings["output_enabled"] = True
        self.store.configure(settings)
        self.key = "CT9G/1981/time-v1/maintenance/1/discovery"

    def collect(self, end, extra=""):
        item = article(period(end) + extra)
        row = {k: item[k] for k in ("id", "url", "title", "category", "published_date")}
        token, _ = self.store.claim_collection(manual=True)
        self.store.finish_collection(token, [row], {item["id"]: dict(item, content_hash=item["body"])}, {})
        return self.store.snapshot()["events"][self.key]

    def test_three_body_edits_after_expiry_each_allow_one_new_delivery(self):
        self.collect("11:00")
        previous = self.store.delivery_token(self.key)
        self.assertTrue(self.store.complete_delivery(previous))
        for hour, minute, end in ((11, 1, "12:00"), (12, 1, "13:00"), (13, 1, "14:30")):
            self.now = self.now.replace(hour=hour, minute=minute)
            self.assertEqual(self.store.snapshot()["events"][self.key]["retired_reason"], "유효기간 만료")
            event = self.collect(end)  # No title change, or even the word 연장.
            self.assertNotIn("retired_at", event)
            self.assertEqual(event["tts_template"], "maintenance.regular.extension")
            token = self.store.delivery_token(self.key)
            self.assertIsNotNone(token)
            self.assertFalse(self.store.complete_delivery(previous))
            self.assertTrue(self.store.complete_delivery(token))
            self.assertIsNone(self.store.delivery_token(self.key))
            self.collect(end, "\n이용해 주셔서 감사합니다.")
            self.assertIsNone(self.store.delivery_token(self.key))
            previous = token

    def test_user_off_and_discard_remain_respected(self):
        self.collect("11:00")
        self.store.set_enabled(self.key, False)
        self.now = self.now.replace(hour=11, minute=1)
        self.assertFalse(self.collect("12:00")["enabled"])
        self.assertIsNone(self.store.delivery_token(self.key))
        self.store.set_enabled(self.key, True)
        self.store.discard(self.key)
        self.assertEqual(self.collect("13:00")["retired_reason"], "사용자 폐기")
        self.assertIsNone(self.store.delivery_token(self.key))

    def test_stale_body_and_manual_period_never_revive(self):
        self.collect("11:00")
        self.store.edit_period(self.key, "2026-10-14 08:00", "2026-10-14 10:55")
        self.now = self.now.replace(hour=11, minute=1)
        self.assertEqual(self.collect("12:00")["retired_reason"], "유효기간 만료")
        self.assertIsNone(self.store.delivery_token(self.key))

    def test_same_expired_period_never_revives_from_wording_alone(self):
        self.collect("11:00")
        self.now = self.now.replace(hour=11, minute=1)
        self.assertEqual(self.collect("11:00", "\n연장 안내")["retired_reason"], "유효기간 만료")
        self.assertIsNone(self.store.delivery_token(self.key))


if __name__ == "__main__":
    unittest.main()
