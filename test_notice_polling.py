"""Clock-controlled local tests: no network, application or speech."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from notice_module.payload.notice_management import KST, NoticeStore, CATEGORIES, DEFAULT_RULES
from notice_module.payload.notice_polling import polling_plan


def at(hour, minute=0):
    return datetime(2026, 9, 16, hour, minute, tzinfo=KST)


def extension(end="2026-09-16T11:30:00+09:00"):
    return {"pinned": True, "category": "maintenance", "title": "정기점검 연장 안내",
            "published_date": "2026-09-16", "analysis": {"windows": [
                {"kind": "maintenance", "issue": "", "start": "2026-09-16T07:00:00+09:00", "end": end}]}}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.state = {"settings": {"collection_enabled": True, "output_enabled": False,
                                  "categories": dict.fromkeys(CATEGORIES, True), "rules": deepcopy(DEFAULT_RULES)},
                      "articles": {"CT9G/1": extension()}}

    def test_extension_boundary_followup_and_return_to_timetable(self):
        self.assertEqual(polling_plan(self.state, at(11, 29))["mode"], "extension_wait")
        self.assertEqual(polling_plan(self.state, at(11, 30))["minutes"], 5)
        self.assertEqual(polling_plan(self.state, at(12, 29))["mode"], "extension_followup")
        self.assertEqual(polling_plan(self.state, at(12, 30))["minutes"], 10)

    def test_default_hours_and_collection_disable_are_respected(self):
        self.assertIsNone(polling_plan(self.state, at(6, 49))["minutes"])
        self.assertEqual(polling_plan(self.state, at(6, 50))["minutes"], 15)
        self.state["settings"]["collection_enabled"] = False
        self.assertEqual(polling_plan(self.state, at(11))["mode"], "normal")

    def test_maintenance_category_disable_uses_normal_timetable(self):
        self.state["settings"]["categories"]["maintenance"] = False
        self.assertEqual(polling_plan(self.state, at(10))["minutes"], 3)

    def test_unknown_failed_or_conflicting_end_never_suspends_polling(self):
        for changed in (dict(extension(), body_error="fetch failed"),
                        dict(extension(), analysis={"windows": []}), extension(None)):
            with self.subTest(changed=changed):
                self.state["articles"]["CT9G/1"] = changed
                self.assertEqual(polling_plan(self.state, at(10))["minutes"], 3)
        self.state["articles"] = {"CT9G/1": extension(), "CT9G/2": extension("2026-09-16T12:00:00+09:00")}
        self.assertEqual(polling_plan(self.state, at(10))["minutes"], 3)

    def test_completion_or_unpin_stops_extension_override(self):
        self.state["articles"]["CT9G/2"] = dict(extension(), title="정기점검 완료 안내")
        self.assertEqual(polling_plan(self.state, at(10))["minutes"], 3)
        del self.state["articles"]["CT9G/2"]
        self.state["articles"]["CT9G/1"]["pinned"] = False
        self.assertEqual(polling_plan(self.state, at(10))["minutes"], 3)

    def test_reextension_updates_deadline_without_changing_settings(self):
        old = deepcopy(self.state["settings"])
        self.state["articles"]["CT9G/1"] = extension("2026-09-16T12:00:00+09:00")
        self.assertEqual(polling_plan(self.state, at(11, 40))["mode"], "extension_wait")
        self.assertEqual(polling_plan(self.state, at(12))["mode"], "extension_followup")
        self.assertEqual(self.state["settings"], old)

    def test_missing_or_future_publication_date_is_not_used(self):
        for date in (None, "2026-09-17"):
            self.state["articles"]["CT9G/1"]["published_date"] = date
            self.assertEqual(polling_plan(self.state, at(10))["minutes"], 3)


class ClaimTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-notice-polling-unit-")
        self.addCleanup(temp.cleanup)
        self.now = at(11, 29)
        self.store = NoticeStore(Path(temp.name), "odin9", clock=lambda: self.now)
        with self.store._transaction() as state:
            state["articles"] = {"CT9G/1": extension()}

    def test_manual_bypass_and_exact_end_then_five_minute_gate_persist(self):
        self.assertIsNone(self.store.claim_collection())
        self.assertIsNotNone(self.store.claim_collection(manual=True))
        self.now = at(11, 30)
        self.assertIsNotNone(self.store.claim_collection())
        self.store = NoticeStore(self.store.root, "odin9", clock=lambda: self.now)
        self.now += timedelta(minutes=4)
        self.assertIsNone(self.store.claim_collection())
        self.now += timedelta(minutes=1)
        self.assertIsNotNone(self.store.claim_collection())

    def test_manual_cannot_override_collection_disabled(self):
        settings = self.store.snapshot()["settings"]
        settings["collection_enabled"] = False
        self.store.configure(settings)
        self.assertIsNone(self.store.claim_collection(manual=True))

    def test_other_server_does_not_inherit_extension(self):
        other = NoticeStore(self.store.root, "odin8", clock=lambda: self.now)
        self.assertIsNotNone(other.claim_collection())
        self.assertIsNone(self.store.claim_collection())


if __name__ == "__main__":
    unittest.main()
