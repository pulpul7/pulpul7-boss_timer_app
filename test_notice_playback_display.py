"""Read-only playback descriptions; no audio or application startup."""
from copy import deepcopy
from datetime import datetime
import unittest
from unittest.mock import Mock

from notice_module.payload.notice_management import KST
from notice_module.payload.notice_opportunities import playback_description
from notice_module.payload.notice_participation import evening_notices
from notice_module.payload.notice_management_ui import NoticeManagementWindow, EVENT_COLUMNS


class PlaybackDisplayTests(unittest.TestCase):
    def setUp(self):
        self.event = dict(id='notice', title='안내', category='general', enabled=True,
                          valid_from='2026-09-22T13:00:00+09:00', valid_until='2026-09-22T23:00:00+09:00')

    def test_once_shows_queue_not_guaranteed_exact_playback(self):
        before = deepcopy(self.event)
        self.assertEqual(playback_description(self.event), '09/22 13:00 이후 · 대기열 1회')
        self.assertEqual(self.event, before)
        self.event['trigger'] = 'major_boss_after'
        self.assertEqual(playback_description(self.event), '09/22 13:00 이후 · 주요 보스 안내 후 1회')

    def test_unconfirmed_and_malformed_plan_are_not_invented_times(self):
        self.event['valid_until'] = None
        self.assertIn('미정', playback_description(self.event))
        self.event['valid_until'] = 'bad date'
        self.assertIn('미정', playback_description(self.event))
        self.event['valid_until'] = '2026-09-22T23:00:00+09:00'
        self.event.update(policy='participation_slot', tts_values={'_slot_plan': {}})
        self.assertIn('미정', playback_description(self.event))

    def test_manual_participation_source_is_not_a_third_playback(self):
        self.event.update(id='manual-guild', category='participation')
        self.assertEqual(playback_description(self.event), '대상 선정용 · 1차/2차 알림 참조')

    def test_repeat_conditions_do_not_pretend_to_have_fixed_time(self):
        for policy, text in (('transfer_repeat', '판매 중'), ('deadline_repeat', '마감 전날부터'),
                             ('participation', '18~23시')):
            self.event['policy'] = policy
            self.assertTrue(playback_description(self.event).startswith(text))

    def test_two_slots_same_content_separate_times_and_independent_tree_rows(self):
        now = datetime(2026, 9, 22, 17, tzinfo=KST)
        snapshot = dict(server_id='odin9', season='18', events=[dict(boss_name='프레이', star=True,
            precision='minute', scheduled_at=now.replace(hour=22).isoformat())])
        events = evening_notices(snapshot, {'events': {}}, now)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['tts_template'], events[1]['tts_template'])
        self.assertEqual(events[0]['event_from'], events[1]['event_from'])
        self.assertNotEqual(events[0]['id'], events[1]['id'])
        self.assertEqual(playback_description(events[0]), '09/22 18:00 이후 · 대기열')
        self.assertEqual(playback_description(events[1]), '09/22 19:59 · 월드보스 안내 후')
        # Exercise actual refresh mapping without Tk or files, so offsets cannot
        # accidentally place the planned time in the title/checkbox columns.
        for event in events:
            event.update(created_at=now.isoformat(), enabled=True, tts_text='동일 문장', revision=1)
        ui = NoticeManagementWindow.__new__(NoticeManagementWindow)
        ui._guard = Mock(return_value=True)
        ui._refresh_sources = Mock()
        ui._details = Mock()
        ui._listen_label = Mock(return_value='▶ 듣기')
        ui.tree = Mock(selection=Mock(return_value=()), get_children=Mock(return_value=()))
        ui.history_tree = Mock(selection=Mock(return_value=()), get_children=Mock(return_value=()))
        ui.status = Mock()
        ui.server_name = '오9'
        ui.store = Mock(snapshot=Mock(return_value=dict(events={e['id']: e for e in events},
                                                        settings={'output_enabled': True})))
        ui._refresh()
        self.assertEqual(EVENT_COLUMNS[:3], ('enabled', 'playback', 'title'))
        rows = [call.kwargs for call in ui.tree.insert.call_args_list]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r['iid'] for r in rows}, {e['id'] for e in events})
        self.assertEqual({r['values'][1] for r in rows}, {playback_description(e) for e in events})
        self.assertTrue(all(r['values'][0] == '☑' and r['values'][-1] == '▶ 듣기' for r in rows))

    def test_selected_boss_uses_minute_before_including_date_rollover(self):
        self.event.update(policy='participation_slot', tts_values={'_slot_plan': {
            'kind': 'boss', 'at': '2026-09-23T00:00:00+09:00', 'boss_name': '프레이'}})
        self.assertEqual(playback_description(self.event), '09/22 23:59 · 프레이 안내 후')
        self.event['tts_values']['_slot_plan'] = dict(kind='world_boss', at='2026-09-22T12:00:00+09:00')
        self.assertEqual(playback_description(self.event), '09/22 11:59 · 월드보스 안내 후')


if __name__ == '__main__':
    unittest.main()
