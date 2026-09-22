"""Exercise GUI request handling without Tk windows, audio or user file writes."""
import unittest
from unittest.mock import Mock

from boss_timer_gui import BossTimerApp


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class CountdownControlTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(BossTimerApp)
        self.app.discord_bot_server_id = "100"
        self.app.schedule_alarm_countdown_enabled_var = Var(False)
        self.app._apply_schedule_alarm_global_options = Mock(return_value=True)
        self.app._append_debug_log = Mock()

    def request(self, enabled, **extra):
        return self.app._apply_discord_schedule_request(
            {"operation": "countdown_control", "server_id": "100", "enabled": enabled, **extra})

    def test_on_and_off_use_checkbox_path_not_a_toggle(self):
        for enabled in (True, False):
            self.assertTrue(self.request(enabled)[0])
            self.assertIs(self.app.schedule_alarm_countdown_enabled_var.get(), enabled)
            self.app._apply_schedule_alarm_global_options.assert_called_with(show_status=False)
        self.assertEqual(self.app._apply_schedule_alarm_global_options.call_count, 2)

    def test_repeat_command_does_not_restart_audio_or_flip_state(self):
        self.request(True)
        self.app._apply_schedule_alarm_global_options.reset_mock()
        self.assertIn("이미", self.request(True)[1])
        self.app._apply_schedule_alarm_global_options.assert_not_called()
        self.assertTrue(self.app.schedule_alarm_countdown_enabled_var.get())

    def test_wrong_missing_guild_and_invalid_values_do_not_change_settings(self):
        for guild in ("200", ""):
            self.assertFalse(self.request(True, server_id=guild)[0])
        for value in ("false", "true", 1, None):
            self.assertFalse(self.request(value)[0])
        self.app._apply_schedule_alarm_global_options.assert_not_called()
        self.assertFalse(self.app.schedule_alarm_countdown_enabled_var.get())

    def test_handover_blocks_countdown_change(self):
        self.app.discord_handover_busy = True
        self.assertFalse(self.request(True)[0])
        self.app._apply_schedule_alarm_global_options.assert_not_called()

    def test_real_checkbox_path_on_disable_clears_countdown_and_saves(self):
        app = self.app
        del app._apply_schedule_alarm_global_options
        app.schedule_alarm_countdown_enabled_var.set(True)
        app.schedule_alarm_countdown_start_var = Var("10")
        app.schedule_second_precision_expire_hours_var = Var("0")
        app.schedule_alarm_ai_recording_preferred_var = Var(True)
        app.schedule_fixed_boss_skip_due_time_var = Var(True)
        app._get_schedule_alarm_global_options_snapshot = Mock(return_value={"changed": True})
        app._get_schedule_alarm_global_defaults_snapshot = Mock(return_value={})
        app.schedule_events = []
        app.schedule_tree = None
        app._normalize_schedule_event_items = Mock(return_value=[])
        for method in ("_drop_pending_schedule_alarm_queue_items", "_stop_schedule_alarm_countdown_audio",
                       "_prepare_schedule_alarm_voice_output", "_save_schedule_alarm_settings", "_save_settings",
                       "_refresh_schedule_view"):
            setattr(app, method, Mock())
        self.assertTrue(self.request(False)[0])
        app._drop_pending_schedule_alarm_queue_items.assert_called_once_with(category="countdown")
        app._stop_schedule_alarm_countdown_audio.assert_called_once_with(close_host=False)
        app._save_schedule_alarm_settings.assert_called_once()
        app._save_settings.assert_called_once()
        self.assertFalse(app.schedule_alarm_countdown_enabled_default)


if __name__ == "__main__":
    unittest.main()
