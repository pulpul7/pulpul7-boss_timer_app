"""No game/real OCR required: allowlist, call isolation and actual WPF masking."""
import ast
import json
from pathlib import Path
import shutil
import subprocess
import unittest

from schedule_ocr1_regions import mask_script, read_regions
import test_schedule_ocr_region_preview as preview_tests


class MaskTests(unittest.TestCase):
    def test_all_chapter_candidates_keep_only_glyph_rectangles(self):
        preview_tests.RegionPreviewTests.setUpClass()
        source = preview_tests.RegionPreviewTests
        rects = read_regions(source.app, source.grid, source.band)
        from schedule_ocr_region_preview import regions
        guides = [r for area in source.grid for r in regions(source.app, area, source.band)]
        expected = {(r.left, r.top, r.left+r.width, r.top+r.height) for r in guides}
        actual = {tuple(r[k] for k in ('left', 'top', 'right', 'bottom')) for r in rects}
        self.assertEqual(actual, expected)
        self.assertNotIn((0, 0, 1600, 900), actual)
        self.assertNotIn((176, 190, 1410, 709), actual)
        from precision_capture_layout import GAME_CLOCK_BOUNDS
        self.assertIn(tuple(getattr(GAME_CLOCK_BOUNDS, side) for side in ('left', 'top', 'right', 'bottom')), actual)

    def test_ocr2_default_has_no_mask_and_invalid_masks_fail_closed(self):
        self.assertEqual(mask_script(None), '  $hasRoiMask = $false\n')
        for rects in ([], [dict(left=-1, top=0, right=20, bottom=20)]):
            with self.assertRaises(ValueError):
                mask_script(rects)

    def test_fixed_initial_and_retry_calls_supply_allowlist_adaptive_does_not(self):
        tree = ast.parse(Path(__file__).with_name('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        def method(name):
            return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        def calls(node):
            return [n for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == '_run_schedule_windows_ocr']
        fixed = calls(method('_build_schedule_input_ocr2_result_for_item'))
        self.assertEqual(len(fixed), 2)
        self.assertTrue(all(any(k.arg == 'read_regions' for k in n.keywords) for n in fixed))
        adaptive = calls(method('_build_schedule_input_ocr_result_for_item'))
        self.assertTrue(adaptive)
        self.assertFalse(any(k.arg == 'read_regions' for n in adaptive for k in n.keywords))
        low_level = ast.unparse(method('_run_schedule_windows_ocr'))
        self.assertIn('-and -not $hasRoiMask', low_level)  # Scale 1 must encode the masked bitmap.

    @unittest.skipUnless(shutil.which('powershell'), 'Windows WPF required')
    def test_native_mask_blacks_out_outside_and_preserves_overlapping_regions(self):
        script = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationCore
$bitmap = [System.Windows.Media.Imaging.WriteableBitmap]::new(1600,900,96,96,[System.Windows.Media.PixelFormats]::Bgra32,$null)
$bytes = [byte[]]::new(1600*900*4)
for ($i=0; $i -lt $bytes.Length; $i++) { $bytes[$i] = 255 }
$bitmap.WritePixels([System.Windows.Int32Rect]::new(0,0,1600,900),$bytes,6400,0)
'''
        script += mask_script([dict(left=100, top=100, right=150, bottom=150),
                               dict(left=120, top=120, right=170, bottom=170)])
        script += r'''
$output = [byte[]]::new(1600*900*4)
$bitmap.CopyPixels($output,6400,0)
@{outside=[int]$output[0];inside=[int]$output[(105*1600+105)*4];overlap=[int]$output[(130*1600+130)*4]} | ConvertTo-Json -Compress
'''
        result = subprocess.run(['powershell', '-NoProfile', '-STA', '-Command', script],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), dict(outside=0, inside=255, overlap=255))


if __name__ == '__main__':
    unittest.main()
