import unittest
from unittest.mock import Mock

from schedule_input_display import apply_fraction_visibility, FRACTION_TAG
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


if __name__ == '__main__':
    unittest.main()
