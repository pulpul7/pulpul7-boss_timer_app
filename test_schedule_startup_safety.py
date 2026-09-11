"""No UI startup, profile writes, or network requests in these regressions."""
import unittest
from unittest.mock import Mock, patch

from boss_timer_gui import BossTimerApp


class StartupSafetyTests(unittest.TestCase):
    def app(self):
        app = object.__new__(BossTimerApp)
        app.schedule_events = [{'boss_name': '파르바'}]
        app._current_schedule_has_data = lambda: bool(app.schedule_events)
        app._get_schedule_state_storage_path = lambda: 'existing_profile.json'
        app._append_debug_log = Mock()
        app._get_active_schedule_server_profile_season_key = lambda: 'season_17'
        app.root = Mock()
        return app

    def test_initial_setup_never_clears_loaded_profile(self):
        app = self.app()
        with patch('boss_timer_gui.os.path.exists', return_value=True):
            app._reset_schedule_for_new_season(protect_existing=True)
        self.assertEqual(app.schedule_events, [{'boss_name': '파르바'}])

    def test_saved_empty_profile_is_not_repopulated_from_cache(self):
        app = self.app(); app.schedule_events = []
        with patch('boss_timer_gui.os.path.exists', return_value=True):
            self.assertFalse(app._can_seed_startup_schedule())
            app._reset_schedule_for_new_season(protect_existing=True)

    def test_brand_new_profile_can_seed(self):
        app = self.app(); app.schedule_events = []
        with patch('boss_timer_gui.os.path.exists', return_value=False):
            self.assertTrue(app._can_seed_startup_schedule())

    def test_different_season_cache_is_untrusted(self):
        app = self.app()
        self.assertFalse(app._is_github_local_schedule_cache_trusted(
            {'id': '오9'}, {'season_no': '1', 'schedule_events': [{'boss_name': '파르바'}]}))

    def test_remote_downgrade_requires_confirmation_default_no(self):
        app = self.app()
        with patch('boss_timer_gui.messagebox.askyesno', return_value=False) as ask:
            self.assertFalse(app._confirm_schedule_sync_replace(
                {'season_no': '1'}, local_version='2026.09.10.003',
                remote_version='2026.09.07.008', local_dirty=True))
        self.assertEqual(ask.call_args.kwargs['default'], 'no')
        self.assertIn('2026.09.07.008', ask.call_args.args[1])
        self.assertIn('season_17', ask.call_args.args[1])

    def test_clean_newer_same_season_sync_does_not_prompt(self):
        app = self.app()
        with patch('boss_timer_gui.messagebox.askyesno') as ask:
            self.assertTrue(app._confirm_schedule_sync_replace(
                {'season_no': '17'}, local_version='2026.09.10.003',
                remote_version='2026.09.11.001', local_dirty=False))
        ask.assert_not_called()

    def test_backup_failure_aborts_import_before_mutation(self):
        app = self.app()
        app._compact_schedule_payload_for_retention = lambda value: (value, False)
        app._create_schedule_full_snapshot_from_shared_payload = lambda *a, **k: {'schedule_events': [{}]}
        app._schedule_restore_snapshot_has_data = lambda value: True
        app.schedule_status_var = Mock()
        app._restore_schedule_full_state_snapshot = Mock()
        with patch('boss_timer_gui.os.path.isfile', return_value=True), patch('builtins.open', side_effect=OSError('denied')):
            self.assertFalse(app._apply_loaded_schedule_shared_payload({}, source_label='test'))
        app._restore_schedule_full_state_snapshot.assert_not_called()
        self.assertEqual(app.schedule_events, [{'boss_name': '파르바'}])


if __name__ == '__main__':
    unittest.main()
