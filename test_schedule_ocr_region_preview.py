"""Read-only geometry and preview lifecycle checks; no OCR, screenshot or Tk UI."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS, MethodType
import unittest
from unittest.mock import Mock

from schedule_ocr_region_preview import regions, Preview, clear, suspend, capture_popup_right


class RegionPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path(__file__).with_name('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        namespace = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.startswith(('SCHEDULE_OCR_', 'SCHEDULE_OCR1_')):
                        try:
                            namespace[target.id] = ast.literal_eval(node.value)
                        except (ValueError, TypeError):
                            pass
        names = ('_get_schedule_input_ocr2_fixed_window_rect', '_get_schedule_ocr_slot_rects',
                 '_get_schedule_ocr_slot_timer_focus_crop_rect', '_get_schedule_ocr_slot_fallback_crop_rect',
                 '_get_schedule_ocr_current_time_crop_rect')
        methods = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in names]
        exec(compile(ast.Module(body=methods, type_ignores=[]), 'ocr-geometry', 'exec'), namespace)
        cls.app = NS()
        for name in names:
            setattr(cls.app, name, MethodType(namespace[name], cls.app))
        cls.grid = namespace['SCHEDULE_OCR_SLOT_GRID']
        cls.band = namespace['SCHEDULE_OCR_CURRENT_TIME_BAND']

    def test_every_chapter_uses_real_ocr1_slots_not_tracker_coordinates(self):
        board = self.app._get_schedule_input_ocr2_fixed_window_rect()
        for area, grid in self.grid.items():
            with self.subTest(area=area):
                actual = regions(self.app, area, self.band)
                self.assertEqual(len(actual), 1 + 2*(grid['top']+grid['bottom']))
                self.assertFalse(any('전체' in r.label or '보드' in r.label for r in actual))
                slots = self.app._get_schedule_ocr_slot_rects(area, 1600, 900, window_rect=board)
                for slot in slots:
                    guide = next(r for r in actual if r.label == f"{slot['slot_index']} 시간 기준")
                    self.assertEqual((guide.left, guide.top), (slot['timer_left'], slot['timer_top']))
                    self.assertEqual(guide.width, slot['timer_right']-slot['timer_left'])
                self.assertTrue(all(r.outline for r in actual))

    def test_fallback_crops_are_optional_and_use_existing_helpers(self):
        normal = regions(self.app, '니플하임', self.band)
        expanded = regions(self.app, '니플하임', self.band, True)
        self.assertFalse(any('재시도' in r.label for r in normal))
        self.assertEqual(sum('재시도' in r.label for r in expanded), 5)
        self.assertTrue(all((r.left, r.top, r.width, r.height) in
                           {(n.left, n.top, n.width, n.height) for n in normal} for r in expanded))
        for guide in expanded:
            self.assertTrue(0 <= guide.left < guide.left + guide.width <= 1600)
            self.assertTrue(0 <= guide.top < guide.top + guide.height <= 900)

    def test_only_requested_ocr1_name_and_timer_edges_change(self):
        fixed = self.app._get_schedule_input_ocr2_fixed_window_rect()
        original = {key: value for key, value in fixed.items() if key != 'ocr1_fixed'}
        for area, grid in self.grid.items():
            before = self.app._get_schedule_ocr_slot_rects(area, 1600, 900, window_rect=original)
            after = self.app._get_schedule_ocr_slot_rects(area, 1600, 900, window_rect=fixed)
            for index, (old, new) in enumerate(zip(before, after)):
                expected = dict(old)
                expected['name_top'] += 12 if index < grid['top'] else 4
                expected['name_right'] -= 12
                expected['timer_left'] += 10
                if index < grid['top']:
                    expected['timer_bottom'] += 4
                self.assertEqual(new, expected, (area, index))
                self.assertLess(new['name_top'], new['name_bottom'])
                self.assertLess(new['name_left'], new['name_right'])
                self.assertLess(new['timer_left'], new['timer_right'])

    def test_preview_docks_to_capture_popup_and_follows_its_position(self):
        popup = Mock()
        popup.winfo_exists.return_value = True
        popup.winfo_ismapped.return_value = True
        popup.winfo_rootx.return_value = 550
        popup.winfo_rooty.return_value = 20
        popup.winfo_width.return_value = 300
        app = NS(schedule_input_ocr_addon_window=popup)
        self.assertEqual(capture_popup_right(app), (856, 20))
        popup.winfo_rootx.return_value = -1000
        popup.winfo_rooty.return_value = 80
        self.assertEqual(capture_popup_right(app), (-694, 80))
        popup.winfo_ismapped.return_value = False
        self.assertIsNone(capture_popup_right(app))
        self.assertIsNone(capture_popup_right(NS()))

    def preview(self):
        preview = Preview.__new__(Preview)
        preview.overlay = Mock()
        preview.window = Mock()
        preview.closed = False
        preview.after = 'timer'
        preview.signature = ('old',)
        preview.app = NS(root=Mock(), _ocr1_region_preview=preview)
        return preview

    def test_capture_suspends_all_guides_immediately(self):
        preview = self.preview()
        overlay = preview.overlay
        suspend(preview.app)
        overlay.clear.assert_called_once()
        preview.window.withdraw.assert_called_once()
        self.assertIsNone(preview.signature)
        self.assertFalse(preview.closed)

    def test_closing_cancels_timer_and_clears_windows(self):
        preview = self.preview()
        overlay = preview.overlay
        clear(preview.app)
        self.assertIsNone(preview.app._ocr1_region_preview)
        overlay.clear.assert_called_once()
        preview.app.root.after_cancel.assert_called_once_with('timer')
        preview.window.destroy.assert_called_once()
        preview.close()
        preview.window.destroy.assert_called_once()


if __name__ == '__main__':
    unittest.main()
