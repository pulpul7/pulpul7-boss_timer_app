"""OCR1 clock uses precision bounds throughout; OCR2 retains its old path."""
import ast
from datetime import datetime
from pathlib import Path
import re
from types import MethodType
import unittest
from unittest.mock import patch

import precision_capture_layout as layout
from precision_time_tracker import Rect
from schedule_ocr1_regions import clock_rect
import test_schedule_ocr_region_preview as preview_tests


class ClockBoundsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        preview_tests.RegionPreviewTests.setUpClass()
        cls.app = preview_tests.RegionPreviewTests.app
        cls.band = preview_tests.RegionPreviewTests.band
        cls.board = cls.app._get_schedule_input_ocr2_fixed_window_rect()
        tree = ast.parse(Path(__file__).with_name('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.name == '_extract_schedule_ocr_current_time')
        namespace = dict(datetime=datetime, SCHEDULE_OCR_CURRENT_TIME_BAND=cls.band)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'clock-filter', 'exec'), namespace)
        cls.app._extract_schedule_ocr_current_time = MethodType(namespace[method.name], cls.app)
        cls.app._get_schedule_reference_datetime = lambda: datetime(2026, 9, 28)
        cls.app._extract_schedule_ocr_current_time_tokens = lambda text: re.findall(r'\d{2}:\d{2}:\d{2}', text)
        cls.app._parse_schedule_clock_token = lambda text: (*map(int, text.split(':')), 'second')

    def test_clock_source_is_shared_not_a_copied_coordinate(self):
        with patch.object(layout, 'GAME_CLOCK_BOUNDS', Rect(191, 257, 421, 307)):
            self.assertEqual(clock_rect(self.board, self.band), dict(left=191, top=257, right=421, bottom=307))

    def test_focused_retry_and_preview_use_same_clock_as_mask(self):
        crop = self.app._get_schedule_ocr_current_time_crop_rect(1600, 900, window_rect=self.board)
        self.assertEqual(crop, [dict(clock_rect(self.board, self.band), label='precision_game_clock')])
        from schedule_ocr_region_preview import regions
        guide = next(r for r in regions(self.app, '니플하임', self.band) if r.label == '현재시간 판독')
        bounds = layout.GAME_CLOCK_BOUNDS
        self.assertEqual((guide.left, guide.top, guide.width, guide.height),
                         (bounds.left, bounds.top, bounds.width, bounds.height))

    def test_clock_near_new_lower_edge_is_kept_and_outside_time_ignored(self):
        outside = dict(text='07:00:00', left=200, top=240, width=100, height=15)
        inside = dict(text='21:23:24', left=200, top=299, width=100, height=6)
        result = self.app._extract_schedule_ocr_current_time([outside, inside], [], 1600, 900, window_rect=self.board)
        self.assertEqual(result.strftime('%H:%M:%S'), '21:23:24')
        self.assertIsNone(self.app._extract_schedule_ocr_current_time([outside], [], 1600, 900, window_rect=self.board))

    def test_ocr2_old_clock_selection_and_crops_are_unchanged(self):
        adaptive = {key: value for key, value in self.board.items() if key != 'ocr1_fixed'}
        outside = dict(text='07:00:00', left=200, top=240, width=100, height=15)
        result = self.app._extract_schedule_ocr_current_time([outside], [], 1600, 900, window_rect=adaptive)
        self.assertEqual(result.strftime('%H:%M:%S'), '07:00:00')
        crops = self.app._get_schedule_ocr_current_time_crop_rect(1600, 900, window_rect=adaptive)
        self.assertIn('fixed_1600_time_value', [r['label'] for r in crops])
        self.assertNotIn('precision_game_clock', [r['label'] for r in crops])


if __name__ == '__main__':
    unittest.main()
