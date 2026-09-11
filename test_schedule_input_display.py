import unittest
from unittest.mock import Mock

from schedule_input_display import (
    apply_fraction_visibility, FRACTION_TAG, find_duplicate_boss_lines,
    highlight_duplicate_boss_lines, DUPLICATE_TAG_PREFIX,
)
from boss_timer_gui import BossTimerApp
from test_schedule_share_text import Variable


class FractionDisplayTests(unittest.TestCase):
    def widget(self, raw):
        widget = Mock()
        widget.get.return_value = raw
        widget._schedule_fraction_tag_text = None
        return widget

    def test_hide_show_never_rewrites_input(self):
        raw = '16:43:02.551234 파르바\n164302.12 야른\n보스 1.23\n16:43:02 니드호그'
        widget = self.widget(raw)
        apply_fraction_visibility(widget)
        self.assertEqual(widget.tag_add.call_count, 2)
        widget.tag_configure.assert_called_with(FRACTION_TAG, elide=True)
        apply_fraction_visibility(widget, False)
        widget.tag_configure.assert_called_with(FRACTION_TAG, elide=False)
        self.assertEqual(widget.get('1.0', 'end-1c'), raw)
        widget.delete.assert_not_called()
        widget.insert.assert_not_called()
        widget.edit_reset.assert_not_called()

    def test_edit_retags_new_fraction(self):
        widget = self.widget('16:43:02.55 파르바')
        apply_fraction_visibility(widget)
        widget.get.return_value = '16:43:02.123456 파르바'
        apply_fraction_visibility(widget)
        widget.tag_add.assert_called_with(FRACTION_TAG, '1.0+8c', '1.0+15c')

    def test_default_resets_checked(self):
        app = object.__new__(BossTimerApp)
        app.schedule_input_hide_fraction_var = Variable(False)
        app._reset_schedule_input_ocr_view_filter_defaults()
        self.assertTrue(app.schedule_input_hide_fraction_var.get())


class DuplicateInputTests(unittest.TestCase):
    def test_aliases_share_identity_and_invasion_is_separate(self):
        from datetime import datetime
        from test_edge_tts_voice import DiscordScheduleInputTests
        app = DiscordScheduleInputTests._parser_app(datetime(2026, 9, 12, 12))
        raw = '# 메모\n16:43:02.551234 파르바\n\n164302.12 파르바\n17:00 침공 파르바'
        groups = find_duplicate_boss_lines(raw, app._parse_schedule_input_line)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['lines'], [2, 4])
        # Name normalization (including configured aliases) supplies raw_key.
        parse = Mock(side_effect=[
            dict(raw_key='normal:탕그리스니르', display_name='탕그'),
            dict(raw_key='normal:탕그리스니르', display_name='탕그리스니르'),
        ])
        self.assertEqual(find_duplicate_boss_lines('16:00 탕그\n17:00 탕그리스니르', parse)[0]['lines'], [1, 2])

    def test_control_and_invalid_lines_are_not_duplicates(self):
        parse = Mock(side_effect=[None, dict(state='control', raw_key='control'), dict(state='control', raw_key='control')])
        self.assertEqual(find_duplicate_boss_lines('invalid\ncontrol\ncontrol', parse), [])

    def test_highlight_preserves_text_and_fraction_tags(self):
        widget = Mock()
        widget.tag_names.return_value = (FRACTION_TAG, 'ocr_error', DUPLICATE_TAG_PREFIX + 'old')
        groups = [dict(name='파르바', lines=[1, 4]), dict(name='야른', lines=[2, 3])]
        highlight_duplicate_boss_lines(widget, groups)
        widget.tag_delete.assert_called_once_with(DUPLICATE_TAG_PREFIX + 'old')
        self.assertEqual(widget.tag_add.call_count, 4)
        widget.tag_add.assert_any_call(DUPLICATE_TAG_PREFIX + '0', '1.0', '1.end')
        widget.tag_add.assert_any_call(DUPLICATE_TAG_PREFIX + '0', '4.0', '4.end')
        widget.mark_set.assert_called_once_with('insert', '3.0')
        widget.see.assert_called_once_with('3.0')
        widget.focus_set.assert_called_once()
        widget.delete.assert_not_called()
        widget.insert.assert_not_called()

    def test_warning_targets_right_ocr_pane_and_blocks_apply(self):
        app = object.__new__(BossTimerApp)
        app._widget_available = lambda widget: widget is not None
        raw = '16:00 파르바\n17:00 파르바'
        app.schedule_input_text = Mock()
        app.schedule_input_text.get.return_value = ''
        app.schedule_input_text.tag_names.return_value = ()
        app.schedule_input_ocr1_text = Mock()
        app.schedule_input_ocr1_text.get.return_value = raw
        app.schedule_input_ocr1_text.tag_names.return_value = ()
        app._parse_schedule_input_line = Mock(return_value=dict(raw_key='normal:파르바', display_name='파르바'))
        app.schedule_input_window = object()
        app.schedule_input_status_var = Mock()
        app._show_centered_messagebox = Mock()
        self.assertTrue(app._warn_schedule_input_duplicate_bosses(raw))
        self.assertIs(app._show_centered_messagebox.call_args.kwargs['parent'], app.schedule_input_window)
        app.schedule_input_ocr1_text.mark_set.assert_called_with('insert', '2.0')
        app.schedule_input_text.mark_set.assert_not_called()


if __name__ == '__main__':
    unittest.main()
