from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from notice_module.payload.notice_management import (
    KST, NoticeError, NoticeStore, collection_interval, event_status,
    prune_all_servers, validate_interval, validate_settings,
)
from notice_module.payload.notice_management_ui import NoticeManagementWindow


class NoticeManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="boss-timer-notice-unit-")
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 9, 13, 18, 0, tzinfo=KST)
        self.root = Path(self.temp.name)
        self.store = NoticeStore(self.root, "server-odin9", clock=lambda: self.now)

    def event(self, key="one", **changes):
        item = {"id": key, "title": "이전권 판매 안내", "category": "transfer",
                "valid_from": self.now - timedelta(minutes=1), "valid_until": self.now + timedelta(hours=1),
                "source_key": f"CT9G/1957/{key}", "source_url": "https://cafe.daum.net/odin/CT9G/1957",
                "tts_text": "이전권 판매 중입니다.", "body": "수집 본문"}
        item.update(changes)
        return item

    def enable_output(self):
        settings = self.store.snapshot()["settings"]
        settings["output_enabled"] = True
        self.store.configure(settings)

    def test_safe_defaults(self):
        settings = self.store.snapshot()["settings"]
        self.assertTrue(settings["collection_enabled"])
        self.assertFalse(settings["output_enabled"])

    def test_server_settings_are_isolated(self):
        other = NoticeStore(self.root, "server-odin8", clock=lambda: self.now)
        self.enable_output()
        self.store.register(self.event())
        self.assertFalse(other.snapshot()["settings"]["output_enabled"])
        self.assertEqual(other.snapshot()["events"], {})
        reopened = NoticeStore(self.root, "server-odin9", clock=lambda: self.now)
        self.assertTrue(reopened.snapshot()["settings"]["output_enabled"])
        self.assertIn("one", reopened.snapshot()["events"])

    def test_clients_are_independent(self):
        other = NoticeStore(self.root / "another-client", "server-odin9", clock=lambda: self.now)
        self.enable_output()
        self.assertFalse(other.snapshot()["settings"]["output_enabled"])

    def test_server_paths_do_not_collide_or_escape(self):
        first, second = NoticeStore(self.root, "../a"), NoticeStore(self.root, "__a")
        self.assertNotEqual(first.path, second.path)
        self.assertEqual(first.path.parent, self.root)
        with self.assertRaises(NoticeError):
            NoticeStore(self.root, "")

    def test_unknown_period_is_never_playable(self):
        self.enable_output()
        for first, last in ((None, None), (None, self.now + timedelta(hours=1)), (self.now, None)):
            self.store.register(self.event(valid_from=first, valid_until=last))
            self.assertIsNone(self.store.delivery_token("one"))
            self.assertIn("미확정", event_status(self.store.snapshot()["events"]["one"], self.now))

    def test_invalid_period_is_not_silently_guessed(self):
        for start, end in ((self.now, self.now), (self.now, self.now - timedelta(seconds=1)),
                           ("2026-09-13", "2026-09-14"), ("아마 오후", None)):
            with self.assertRaises(NoticeError):
                validate_interval(start, end)

    def test_event_period_does_not_substitute_notice_validity(self):
        self.enable_output()
        self.store.register(self.event(valid_from=None, valid_until=None,
                                       event_from=self.now, event_until=self.now + timedelta(hours=2)))
        self.assertIsNone(self.store.delivery_token("one"))

    def test_no_global_announcement_hour_restriction(self):
        self.now = self.now.replace(hour=2)
        self.enable_output()
        self.store.register(self.event())
        self.assertIsNotNone(self.store.delivery_token("one"))

    def test_valid_start_inclusive_end_exclusive(self):
        self.enable_output()
        self.store.register(self.event(valid_from=self.now, valid_until=self.now + timedelta(seconds=1)))
        token = self.store.delivery_token("one")
        self.assertIsNotNone(token)
        self.now += timedelta(seconds=1)
        self.assertIsNone(self.store.delivery_token("one"))
        self.assertFalse(self.store.complete_delivery(token))
        self.assertEqual(self.store.snapshot()["events"]["one"]["retired_reason"], "유효기간 만료")

    def test_checkbox_off_on_invalidates_old_queue_without_repeating_completed(self):
        self.enable_output()
        self.store.register(self.event())
        token = self.store.delivery_token("one")
        self.store.set_enabled("one", False)
        self.assertIsNone(self.store.delivery_token("one"))
        self.store.set_enabled("one", True)
        self.assertFalse(self.store.complete_delivery(token))
        self.assertTrue(self.store.complete_delivery(self.store.delivery_token("one")))
        self.store.set_enabled("one", False)
        self.store.set_enabled("one", True)
        self.assertIsNone(self.store.delivery_token("one"))

    def test_global_output_off_invalidates_old_queue(self):
        self.enable_output()
        self.store.register(self.event())
        token = self.store.delivery_token("one")
        settings = self.store.snapshot()["settings"]
        settings["output_enabled"] = False
        self.store.configure(settings)
        self.assertFalse(self.store.complete_delivery(token))
        self.assertIsNone(self.store.delivery_token("one"))

    def test_successful_audio_only_recorded_once(self):
        self.enable_output()
        self.store.register(self.event())
        token = self.store.delivery_token("one")
        self.assertIsNone(self.store.snapshot()["events"]["one"]["last_delivery"])
        self.assertTrue(self.store.complete_delivery(token))
        self.assertFalse(self.store.complete_delivery(token))
        self.assertIsNone(self.store.delivery_token("one"))

    def test_content_change_invalidates_old_audio(self):
        self.enable_output()
        self.store.register(self.event())
        token = self.store.delivery_token("one")
        self.store.register(self.event(tts_text="판매 시간이 변경되었습니다."), collected=True)
        self.assertFalse(self.store.complete_delivery(token))

    def test_recollection_preserves_disabled_flag_and_manual_period(self):
        self.store.register(self.event(), collected=True)
        self.store.set_enabled("one", False)
        self.store.edit_period("one", self.now, self.now + timedelta(hours=2))
        self.store.register(self.event(body="수정 본문", valid_until=self.now + timedelta(hours=4)), collected=True)
        event = self.store.snapshot()["events"]["one"]
        self.assertFalse(event["enabled"])
        self.assertEqual(event["valid_until"], (self.now + timedelta(hours=2)).isoformat())

    def test_unchanged_collection_does_not_create_new_revision(self):
        event = self.event()
        self.store.register(event, collected=True)
        revision = self.store.snapshot()["events"]["one"]["revision"]
        self.assertFalse(self.store.register(event, collected=True))
        self.assertEqual(revision, self.store.snapshot()["events"]["one"]["revision"])

    def test_collection_controls_do_not_mute_existing_notices(self):
        self.enable_output()
        self.store.register(self.event())
        settings = self.store.snapshot()["settings"]
        settings["collection_enabled"] = False
        self.store.configure(settings)
        self.assertFalse(self.store.register(self.event("two"), collected=True))
        self.assertIsNotNone(self.store.delivery_token("one"))

    def test_category_filter(self):
        settings = self.store.snapshot()["settings"]
        settings["categories"]["transfer"] = False
        self.store.configure(settings)
        self.assertFalse(self.store.register(self.event(), collected=True))

    def test_discard_and_delete_history_do_not_resurrect_source(self):
        event = self.event()
        self.store.register(event, collected=True)
        self.store.discard("one")
        self.assertEqual(self.store.delete_history(), 1)
        self.assertFalse(self.store.register(event, collected=True))
        self.store.reconcile_sources([], successful=False)
        self.assertFalse(self.store.register(event, collected=True))
        self.store.reconcile_sources([], successful=True)
        self.assertTrue(self.store.register(event, collected=True))

    def test_failed_collection_does_not_retire_pinned_notices(self):
        self.store.register(self.event(), collected=True)
        self.store.reconcile_sources([], successful=False)
        self.assertNotIn("retired_at", self.store.snapshot()["events"]["one"])
        self.store.reconcile_sources([], successful=True)
        self.assertEqual(self.store.snapshot()["events"]["one"]["retired_reason"], "공지 해제")

    def test_thirty_day_retention_boundary(self):
        self.store.register(self.event())
        end = self.now + timedelta(hours=1)
        self.now = end + timedelta(days=30) - timedelta(seconds=1)
        self.assertIn("one", self.store.snapshot()["events"])
        self.now += timedelta(seconds=1)
        self.assertNotIn("one", self.store.snapshot()["events"])
        self.assertIn("CT9G/1957/one", self.store.snapshot()["suppressed"])

    def test_history_delete_does_not_delete_active_events(self):
        self.store.register(self.event())
        self.assertEqual(self.store.delete_history(), 0)
        self.assertIn("one", self.store.snapshot()["events"])

    def test_cleanup_inactive_server_records(self):
        self.now = datetime(2000, 1, 1, tzinfo=KST)
        self.store.register(self.event())
        self.assertEqual(prune_all_servers(self.root), [])
        self.assertEqual(json.loads(self.store.path.read_text(encoding="utf-8"))["events"], {})

    def test_corrupt_state_is_preserved(self):
        self.store.register(self.event())
        self.store.path.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.snapshot()
        self.assertEqual(self.store.path.read_text(encoding="utf-8"), "broken")

    def test_weekday_collection_rules(self):
        settings = self.store.snapshot()["settings"]
        self.assertIsNone(collection_interval(settings, datetime(2026, 9, 16, 6, 49, tzinfo=KST)))
        self.assertEqual(collection_interval(settings, datetime(2026, 9, 16, 6, 50, tzinfo=KST)), 15)
        self.assertEqual(collection_interval(settings, datetime(2026, 9, 16, 9, 30, tzinfo=KST)), 3)
        self.assertEqual(collection_interval(settings, datetime(2026, 9, 16, 11, 0, tzinfo=KST)), 10)
        self.assertEqual(collection_interval(settings, datetime(2026, 9, 17, 10, 0, tzinfo=KST)), 10)

    def test_invalid_collection_rules(self):
        settings = self.store.snapshot()["settings"]
        for changes in ({"start": "24:00"}, {"minutes": 0}, {"minutes": True}, {"end": "05:00"}, {"days": "휴일"}):
            value = deepcopy(settings)
            value["rules"][0].update(changes)
            with self.assertRaises(NoticeError):
                validate_settings(value)

    def test_stale_server_window_cannot_write(self):
        ui = object.__new__(NoticeManagementWindow)
        ui.server_id = "server-odin9"
        ui.app = Mock(schedule_server_profile_id="server-odin8")
        ui.window = Mock()
        operation = Mock()
        self.assertFalse(ui._run(operation))
        operation.assert_not_called()
        ui.app._show_centered_messagebox.assert_called_once()

    @unittest.skipUnless(sys.platform == "win32", "Windows locking")
    def test_same_client_file_lock_is_not_discord_connection_arbitration(self):
        other = NoticeStore(self.root, "server-odin9", clock=lambda: self.now)
        with self.store._transaction():
            with self.assertRaises(NoticeError):
                other.snapshot()
        other.snapshot()


if __name__ == "__main__":
    unittest.main()
