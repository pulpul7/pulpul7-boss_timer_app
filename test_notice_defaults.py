from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from notice_module.payload.notice_defaults import export_preferences, bundled_preferences, validate_preferences
from notice_module.payload.notice_management import NoticeStore, KST, DEFAULT_RULES, CATEGORIES


class NoticeDefaultsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.now = datetime(2026, 9, 28, 18, tzinfo=KST)
        self.prefs = dict(schema=1, settings=dict(collection_enabled=True, output_enabled=True,
            categories=dict.fromkeys(CATEGORIES, True), rules=deepcopy(DEFAULT_RULES)),
            tts_templates={'manual.general': '테스트 안내입니다.'}, tts_template_enabled={'manual.general': False},
            tts_date_syntax=2, participation_template_merged=True)
        self.store = NoticeStore(self.root, '9', clock=lambda: self.now)

    def test_new_server_uses_promoted_settings_without_any_history(self):
        with patch('notice_module.payload.notice_defaults.bundled_preferences', return_value=self.prefs):
            state = self.store._load()
        self.assertEqual(state['settings'], self.prefs['settings'])
        self.assertEqual(state['tts_templates'], self.prefs['tts_templates'])
        self.assertEqual(state['events'], {})
        self.assertEqual(state['suppressed'], {})
        self.assertEqual(state['server_id'], '9')

    def test_existing_server_is_not_overwritten_by_packaged_defaults(self):
        with patch('notice_module.payload.notice_defaults.bundled_preferences', return_value=self.prefs):
            state = self.store._load()
            state['settings']['output_enabled'] = False
            self.store._save(state)
            self.assertFalse(self.store._load()['settings']['output_enabled'])

    def test_export_drops_server_notices_schedule_and_delivery_data(self):
        state = dict(self.prefs, server_id='9', events={'x': {}}, delivery_ledger=[{'at': 'x'}], token='private')
        exported = export_preferences(state)
        self.assertEqual(exported, self.prefs)
        with self.assertRaises(ValueError):
            validate_preferences(dict(exported, events={}))

    def test_rollback_retains_events_suppression_and_ledger_and_backs_up(self):
        with patch('notice_module.payload.notice_defaults.bundled_preferences', return_value=None):
            self.store.register(dict(id='test', title='공지', category='general',
                valid_from=self.now, valid_until=self.now + timedelta(hours=2), tts_text='사용자 문구'))
        before = self.store.snapshot()
        backup = self.store.restore_preferences(self.prefs)
        after = self.store.snapshot()
        self.assertEqual(before['events'], after['events'])
        self.assertEqual(before['suppressed'], after['suppressed'])
        self.assertGreater(after['generation'], before['generation'])
        self.assertEqual(after['settings'], self.prefs['settings'])
        self.assertEqual(json.loads(Path(backup).read_text(encoding='utf-8'))['settings'], before['settings'])

    def test_distribution_and_module_defaults_are_identical(self):
        default = Path(__file__).parent / 'init/default_notice_settings.json'
        if not default.exists():
            self.skipTest('Promotion has not run yet')
        self.assertEqual(json.loads(default.read_text(encoding='utf-8-sig')), bundled_preferences())

    def test_completed_notice_does_not_replay_after_settings_restore(self):
        self.store.register(dict(id='done', title='공지', category='general',
            valid_from=self.now, valid_until=self.now + timedelta(hours=2),
            tts_template='discovery.general', tts_values={'공지제목': '실제 공지'}))
        token = self.store.delivery_token('done')
        self.assertIsNotNone(token)
        self.assertTrue(self.store.complete_delivery(token))
        before = self.store.snapshot()
        prefs = deepcopy(self.prefs)
        prefs['tts_templates']['discovery.general'] = '새로운 안내: {공지제목}'
        self.store.restore_preferences(prefs)
        self.assertIsNone(self.store.delivery_token('done'))
        self.assertEqual(before.get('delivery_ledger'), self.store.snapshot().get('delivery_ledger'))


if __name__ == '__main__':
    unittest.main()
