"""Read-only geometry and preview lifecycle checks; no OCR, screenshot or Tk UI."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS, MethodType
import unittest
from unittest.mock import Mock, patch
import tkinter as tk

from schedule_ocr_region_preview import regions, Preview, clear, suspend, capture_popup_right, is_open


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

    def test_popup_checkbox_state_is_not_changed_by_capture_hide(self):
        preview = self.preview()
        preview.window.winfo_exists.return_value = True
        self.assertTrue(is_open(preview.app))
        suspend(preview.app)
        self.assertTrue(is_open(preview.app))
        clear(preview.app)
        self.assertFalse(is_open(preview.app))
        self.assertFalse(is_open(NS()))

    def test_destroyed_popup_query_is_safe(self):
        preview = self.preview()
        preview.window.winfo_exists.side_effect = tk.TclError('destroyed')
        self.assertFalse(is_open(preview.app))

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

    def test_capture_settings_checkboxes_route_independently_and_sync_popup_close(self):
        tree = ast.parse(Path(__file__).with_name('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.name == '_open_schedule_input_ocr_addon_restore_delay_dialog')
        class Variable:
            def __init__(self, value=None): self.value = value
            def get(self): return self.value
            def set(self, value): self.value = value
        dialog = Mock()
        dialog.winfo_exists.return_value = True
        dialog.after.return_value = 'preview-sync'
        boxes = {}
        def checkbox(*args, **kwargs):
            widget = Mock()
            boxes[kwargs['text']] = (kwargs, widget)
            return widget
        gui = NS(Toplevel=Mock(return_value=dialog), StringVar=Variable, BooleanVar=Variable,
                 TclError=tk.TclError, Label=Mock(), Button=Mock(), Checkbutton=checkbox)
        namespace = dict(tk=gui, ttk=NS(Combobox=Mock()), DEFAULT_CAPTURE_RATE=5)
        setter = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.name == '_set_schedule_ocr1_region_preview_enabled')
        exec(compile(ast.Module(body=[method, setter], type_ignores=[]), 'capture-settings', 'exec'), namespace)
        app = NS(root=Mock(), schedule_input_ocr_addon_window=None,
                 schedule_input_ocr_addon_settings_window=None,
                 _center_window_over_parent=Mock(), _get_precision_capture_slots=lambda: {'니플하임': []},
                 button_font=('맑은 고딕', 10), percent_font=('맑은 고딕', 9),
                 popup_open=True, _save_settings=Mock())
        app._show_schedule_ocr1_region_preview = Mock(side_effect=lambda: setattr(app, 'popup_open', True))
        app._set_schedule_ocr1_region_preview_enabled = MethodType(namespace[setter.name], app)
        with patch('precision_capture_ui.clear_preview') as clear_precision, \
             patch('precision_capture_ui.show_preview') as show_precision, \
             patch('schedule_ocr_region_preview.is_open', side_effect=lambda a: a.popup_open), \
             patch('schedule_ocr_region_preview.clear', side_effect=lambda a: setattr(a, 'popup_open', False)) as clear_ocr:
            namespace[method.name](app)
            left, left_widget = boxes['스샷찍기 영역']
            right, right_widget = boxes['초단위 찍기 영역']
            self.assertLess(left_widget.place.call_args.kwargs['x'], right_widget.place.call_args.kwargs['x'])
            self.assertTrue(left['variable'].get())
            left['variable'].set(False)
            left['command']()
            clear_ocr.assert_called_once_with(app)
            self.assertFalse(left['variable'].get())
            self.assertFalse(app.ocr1_show_regions)
            app._save_settings.assert_called_once()
            left['variable'].set(True)
            left['command']()
            app._show_schedule_ocr1_region_preview.assert_called_once()
            self.assertTrue(left['variable'].get())
            self.assertTrue(app.ocr1_show_regions)
            self.assertEqual(app._save_settings.call_count, 2)
            app._set_schedule_ocr1_region_preview_enabled(False)  # Popup's X.
            dialog.after.call_args.args[1]()
            self.assertFalse(left['variable'].get())
            right['variable'].set(True)
            right['command']()
            self.assertTrue(show_precision.call_args.args[-1])
            self.assertTrue(app.precision_show_regions)
            self.assertEqual(app._save_settings.call_count, 4)
            self.assertEqual(clear_ocr.call_count, 2)
            destroyed = next(call.args[1] for call in dialog.bind.call_args_list if call.args[0] == '<Destroy>')
            destroyed(NS(widget=dialog))
            dialog.after_cancel.assert_called_once_with('preview-sync')
            clear_precision.assert_called_once_with(app)
            self.assertEqual(app._save_settings.call_count, 4)  # Cleanup cannot overwrite the preference.

    def test_disabled_preference_blocks_automatic_popup_reopen(self):
        tree = ast.parse(Path(__file__).with_name('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                      and n.name == '_show_schedule_ocr1_region_preview')
        namespace = dict(SCHEDULE_OCR_SLOT_GRID=self.grid, SCHEDULE_OCR_CURRENT_TIME_BAND=self.band)
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'preview-reopen', 'exec'), namespace)
        app = NS(ocr1_show_regions=False, schedule_input_window=Mock(), _widget_available=lambda window: True)
        with patch('schedule_ocr_region_preview.open_preview') as opened:
            namespace[method.name](app)
            opened.assert_not_called()
            app.ocr1_show_regions = True
            namespace[method.name](app)
            opened.assert_called_once()

    def test_checkbox_settings_read_write_roundtrip(self):
        import configparser
        tree = ast.parse(Path(__file__).with_name('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        keys = ('ocr1_show_regions', 'precision_show_regions')
        loader = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_load_settings')
        assignments = [n for n in loader.body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Attribute) and t.attr in keys for t in n.targets)]
        saver = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_save_settings')
        settings_dict = next(n for n in ast.walk(saver) if isinstance(n, ast.Dict)
                             and any(isinstance(k, ast.Constant) and k.value == 'ocr1_show_regions' for k in n.keys))
        expressions = {k.value: value for k, value in zip(settings_dict.keys, settings_dict.values)
                       if isinstance(k, ast.Constant) and k.value in keys}
        self.assertEqual(len(assignments), 2)
        for selected in (True, False):
            app = NS(ocr1_show_regions=selected, precision_show_regions=not selected)
            config = configparser.ConfigParser()
            config['settings'] = {key: eval(compile(ast.Expression(expressions[key]), 'saved-pref', 'eval'),
                                           {'self': app}) for key in keys}
            restarted = NS()
            exec(compile(ast.Module(body=assignments, type_ignores=[]), 'load-pref', 'exec'),
                 {'self': restarted, 'settings': config['settings']})
            self.assertEqual(restarted.ocr1_show_regions, selected)
            self.assertEqual(restarted.precision_show_regions, not selected)


if __name__ == '__main__':
    unittest.main()
