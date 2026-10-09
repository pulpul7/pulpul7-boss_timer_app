"""Nonvisual checks: no real Tk windows, notifications, or application startup."""
from collections import deque
import time
import unittest
from unittest.mock import MagicMock, patch

from desktop_banner_ui import SlidingBanner, banner_kind, ease_out, wrap_spans
from desktop_banner_service import DesktopBannerService


class BannerLayoutTests(unittest.TestCase):
    def test_motion_is_bounded_and_slows_before_stopping(self):
        self.assertEqual(ease_out(-1), 0)
        self.assertEqual(ease_out(2), 1)
        self.assertGreater(ease_out(0.25), 0.25)
        self.assertGreater(ease_out(0.25)-ease_out(0), ease_out(1)-ease_out(0.75))

    def test_pixel_wrapping_keeps_korean_and_colors(self):
        rows = wrap_spans([{"text": "엘드르 ", "color": "#c084fc"},
                           {"text": "1분 전", "color": "#fb7185", "bold": True}],
                          lambda char, bold: 10 if char != " " else 5, 40)
        self.assertEqual("".join(char for row in rows for char, _, _ in row), "엘드르 1분 전")
        self.assertTrue(all(sum(10 if char != " " else 5 for char, _, _ in row) <= 40 for row in rows))
        self.assertEqual(rows[-1][-1][1], "#fb7185")

    def test_long_notice_has_at_most_three_lines_and_ellipsis(self):
        rows = wrap_spans([{"text": "긴 공지" * 30}], lambda *_: 10, 40)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1][-1][0], "…")
        self.assertLessEqual(len(rows[-1]), 4)

    def test_warning_and_spawn_badges_are_distinct(self):
        self.assertEqual(banner_kind("엘드르 5분 남았습니다.")[0], "5분")
        self.assertEqual(banner_kind("엘드르 일분 남았습니다.")[0], "1분")
        self.assertEqual(banner_kind("엘드르 젠")[0], "젠")
        self.assertEqual(banner_kind("관리자 접속 완료")[0], "안내")


class WindowCacheTests(unittest.TestCase):
    def setUp(self):
        # Substitute only windows/fonts; exercise the real pool lifecycle.
        self.banner = SlidingBanner.__new__(SlidingBanner)
        self.banner.root = MagicMock()
        self.banner.root.after_idle.side_effect = lambda callback: callback
        self.banner.root.after.side_effect = lambda interval, callback: callback
        self.banner.enabled = False
        self.banner.failed = False
        self.banner.pool, self.banner.visible = [], []
        self.banner.pending = deque(maxlen=64)
        self.banner.prepare_id = None
        self.banner.preparing = False
        self.banner.cached_area = (0, 0, 1920, 1080)
        self.banner.height = 172
        self.banner.area = lambda: (0, 0, 1920, 1080)
        self.banner.on_error = MagicMock()
        self.banner.measure = lambda *_: 10
        self.banner._new_card = MagicMock(side_effect=lambda: MagicMock())

    def test_prepare_once_reuses_windows_across_disable_enable(self):
        self.banner.set_enabled(True)
        for _ in range(4):
            self.banner.prepare_id()
        self.assertEqual(self.banner._new_card.call_count, 3)
        pool = list(self.banner.pool)
        self.banner.show("보스", "5분 전")
        self.banner.set_enabled(False)
        self.banner.set_enabled(True)
        self.banner.prepare_id()
        self.banner.show("보스", "1분 전")
        self.assertEqual(self.banner._new_card.call_count, 3)
        self.assertIn(self.banner.visible[0], pool)

    def test_max_three_cards_and_waiting_notice_uses_recycled_window(self):
        self.banner.enabled = True
        for index in range(5):
            self.banner.show(str(index), "안내")
        self.assertEqual(len(self.banner.visible), 3)
        self.assertEqual(len(self.banner.pending), 2)
        bottom, middle, top = self.banner.visible
        self.assertEqual([card.slot for card in self.banner.visible], [0, 1, 2])
        self.assertGreater(bottom.retarget.call_args.args[0], middle.retarget.call_args.args[0])
        self.assertGreater(middle.retarget.call_args.args[0], top.retarget.call_args.args[0])
        middle_moves = middle.retarget.call_count
        self.banner.recycle(bottom)
        self.assertEqual(len(self.banner.visible), 2)
        self.assertEqual(len(self.banner.pending), 2)
        self.assertEqual(middle.retarget.call_count, middle_moves)
        self.assertEqual(middle.slot, 1)
        self.banner.recycle(self.banner.visible[-1])
        self.assertEqual(len(self.banner.visible), 2)
        self.assertEqual(len(self.banner.pending), 1)
        self.assertEqual(self.banner.visible[-1].slot, 2)
        self.assertEqual(self.banner._new_card.call_count, 3)

    def test_stale_notice_never_appears_after_waiting(self):
        self.banner.enabled = True
        self.banner.show("오래된 안내", "5분 전", created_at=time.time()-60)
        self.assertFalse(self.banner.visible)
        self.assertEqual(self.banner._new_card.call_count, 0)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.root, self.log, self.on_error = MagicMock(), MagicMock(), MagicMock()
        self.inbox_patch = patch("desktop_banner_service.BannerInbox")
        self.native_patch = patch("desktop_banner_service.WindowsBanner")
        self.inbox = self.inbox_patch.start().return_value
        self.native = self.native_patch.start().return_value
        self.native.errors.empty.return_value = True
        self.addCleanup(self.inbox_patch.stop)
        self.addCleanup(self.native_patch.stop)
        self.service = DesktopBannerService(self.root, "unused", lambda: "10", self.log, self.on_error)

    def test_disabled_does_not_prepare_fonts_or_windows(self):
        self.service.set_enabled(False)
        self.root.after_idle.assert_not_called()
        self.native.prepare.assert_not_called()
        self.assertIsNone(self.service.visual)

    def test_custom_card_only_native_history_is_requested(self):
        self.service.set_enabled(True)
        self.service.visual = MagicMock()
        self.service.visual.show.return_value = True
        self.service.show("보스", "5분 전")
        self.assertTrue(self.native.submit.call_args.kwargs["quiet"])
        self.service.visual.show.assert_called_once()

    def test_renderer_failure_falls_back_to_native_popup(self):
        self.service.set_enabled(True)
        self.service.visual = MagicMock()
        self.service.visual.show.return_value = False
        self.service.show("보스", "1분 전")
        self.assertFalse(self.native.submit.call_args.kwargs["quiet"])

    def test_history_error_does_not_interrupt_working_card(self):
        self.service.set_enabled(True)
        self.service.visual = MagicMock(failed=False)
        self.native.errors.empty.side_effect = [False, True]
        self.native.errors.get_nowait.return_value = "notification_disabled"
        self.inbox.drain.return_value = []
        self.service._poll()
        self.log.assert_called_once()
        self.on_error.assert_not_called()

    def test_disable_and_close_prevent_new_notifications(self):
        self.service.set_enabled(True)
        self.service.visual = MagicMock()
        self.service.set_enabled(False)
        self.service.show("보스", "젠")
        self.native.submit.assert_not_called()
        self.service.close()
        self.service.close()
        self.native.close.assert_called_once()
        self.service.visual.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
