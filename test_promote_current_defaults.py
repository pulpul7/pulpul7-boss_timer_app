"""Promotion safety checks on synthetic data only; no live settings or audio."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from notice_module.payload.notice_defaults import bundled_preferences
from notice_module.payload.notice_management import NoticeStore
from promote_current_defaults import promote, atomic_write


class PromotionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='boss-settings-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/'project'
        self.data = Path(temporary.name)/'appdata'
        self.profile = self.data/'server_profiles/season_26/9'
        self.baseline = self.profile/'settings_rollback/baseline'

        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding='utf-8')
        self.write = write
        write(self.data/'active_server_profile.json', json.dumps(dict(season_key='season_26', server_id='9')))
        write(self.baseline/'manifest.json', json.dumps(dict(groups={}, inherited_from='old')))
        write(self.baseline/'boss_timer_settings.ini', '[settings]\nschedule_alarm_enabled = false\n')
        write(self.data/'boss_timer_settings.ini', '[settings]\nschedule_alarm_enabled = true\nschedule_break_rows_enabled = true\ndiscord_token = SECRET\n')
        write(self.root/'init/default_settings.ini', '[settings]\nschedule_alarm_enabled = false\n')
        write(self.data/'edge_tts.ini', '[edge_tts]\nenabled = true\nvoice = voice\nrate = 0\nvolume = 0\npitch = 0\nprivate = SECRET\n')
        write(self.root/'wave/chime.wav', 'synthetic')
        self.alarm = dict(chime_settings=dict(general='wave/chime.wav', fixed='wave/chime.wav',
                                             rapid_chain='wave/chime.wav', skip_countdown=True))
        write(self.profile/'schedule_alarm_settings.json', json.dumps(self.alarm))
        for name in ('schedule_boss_definitions.txt', 'schedule_area_definitions.txt',
                     'schedule_boss_metrics.json', 'schedule_break_rules.json', 'schedule_fixed_bosses.txt'):
            write(self.profile/'init'/name, '{}')
            write(self.root/'init'/name, 'old-project')
            write(self.data/'init'/name, 'old-shared')
        self.notice_path = NoticeStore(self.data/'notice_data', '9').path
        write(self.notice_path, json.dumps(dict(bundled_preferences(), server_id='9', events={'private': 'event'})))
        write(self.profile/'schedule_state.json', 'do-not-change-schedule')

    def test_promotion_backs_up_and_preserves_live_data_and_secrets(self):
        alarm_before = (self.profile/'schedule_alarm_settings.json').read_bytes()
        result = promote(self.root, self.data)
        self.assertEqual((self.profile/'schedule_alarm_settings.json').read_bytes(), alarm_before)
        self.assertEqual((self.profile/'schedule_state.json').read_text(), 'do-not-change-schedule')
        for path in (self.root/'init/default_schedule_alarm_settings.json',
                     self.data/'init/default_schedule_alarm_settings.json', self.baseline/'schedule_alarm_settings.json'):
            self.assertEqual(json.loads(path.read_text()), self.alarm)
        backup = Path(result['previous_defaults'])
        self.assertEqual((backup/'project/init/schedule_fixed_bosses.txt').read_text(), 'old-project')
        self.assertEqual((backup/'appdata/init/schedule_fixed_bosses.txt').read_text(), 'old-shared')
        prefs = (self.root/'init/default_notice_settings.json').read_bytes()
        self.assertEqual(prefs, (self.root/'notice_module/payload/notice_defaults.json').read_bytes())
        self.assertNotIn('events', json.loads(prefs))
        self.assertNotIn('server_id', json.loads(prefs))
        for name in ('default_settings.ini', 'default_edge_tts.ini'):
            self.assertNotIn('SECRET', (self.root/'init'/name).read_text())
        self.assertTrue((Path(result['previous_baseline'])/'manifest.json').is_file())

    def test_empty_chime_rejected_before_promotion(self):
        self.alarm['chime_settings']['general'] = ''
        self.write(self.profile/'schedule_alarm_settings.json', json.dumps(self.alarm))
        original = (self.baseline/'manifest.json').read_bytes()
        with self.assertRaises(ValueError):
            promote(self.root, self.data)
        self.assertEqual((self.baseline/'manifest.json').read_bytes(), original)
        self.assertFalse((self.root/'settings_seed_backups').exists())

    def test_failed_write_restores_already_written_targets(self):
        original = (self.baseline/'boss_timer_settings.ini').read_bytes()
        def fail(path, data):
            if path == self.root/'init/default_schedule_alarm_settings.json':
                raise PermissionError('simulated write failure')
            atomic_write(path, data)
        with patch('promote_current_defaults.atomic_write', side_effect=fail), self.assertRaises(PermissionError):
            promote(self.root, self.data)
        self.assertEqual((self.baseline/'boss_timer_settings.ini').read_bytes(), original)
        self.assertFalse((self.baseline/'schedule_alarm_settings.json').exists())
        self.assertEqual((self.root/'init/schedule_fixed_bosses.txt').read_text(), 'old-project')


if __name__ == '__main__':
    unittest.main()
