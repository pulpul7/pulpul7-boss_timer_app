"""Maintenance lifecycle integration; no live user data, network, UI or builds."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import tempfile
import threading
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from boss_timer_gui import BossTimerApp
from boss_timer_discord_bot import ScheduleReader
from schedule_maintenance import (active_control, control_kind, fixed_visible,
                                  merge_controls, ordinary_visible, regular_boundary)
from schedule_maintenance_host import apply_transition
from notice_module.payload.notice_management import KST, NoticeStore
from notice_module.payload.notice_maintenance_lifecycle import import_transitions, transition_candidates
from notice_module.payload.main import NoticePlugin
import test_edge_tts_voice
from test_notice_server_open import article


def variable(value):
    values = [value]
    return NS(get=lambda:values[0], set=lambda value:values.__setitem__(0, value))


def app_at(now):
    app = BossTimerApp.__new__(BossTimerApp)
    app._get_schedule_reference_datetime = lambda:now
    app.schedule_server_profile_id = '9'
    app.current_season_no = '18'
    app.schedule_maintenance_weekday_var = variable('수요일')
    app.schedule_maintenance_hour_var = variable('08시')
    app.schedule_maintenance_minute_var = variable('00분')
    app.schedule_events = []
    app.schedule_active_entries = []
    app.schedule_control_events = []
    app.schedule_delete_active_cutoff_datetime = None
    app.schedule_last_import_meta = {'imported_at':now}
    app._save_schedule_state = Mock()
    app._refresh_schedule_view = Mock()
    app._append_debug_log = Mock()
    app._get_fixed_boss_effective_colors = lambda *args:('#fff', '#123')
    app._get_schedule_boss_display_name = lambda row, **kw:row['boss_name']
    app.fixed_boss_entries = [dict(boss_name='월드보스', time_text='13:00:00',
        days=['월','화','수','목','금','토','일'], repeat_mode='weekly', enabled=True)]
    return app


def request(start, end=None, *, action='start', kind='temporary_maintenance'):
    return dict(server_id='9', season='18', kind=kind, action=action,
                scheduled_at=start.replace(tzinfo=KST).isoformat(),
                event_until=end.replace(tzinfo=KST).isoformat() if end else None, source_id='source', key='key')


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 10, 29, 9)
        self.now = datetime(2026, 10, 29, 12)
        self.row = dict(control_type='temporary_maintenance', scheduled_at=self.start)

    def test_planned_end_does_not_end_maintenance_but_confirmed_opening_does(self):
        rows = [dict(self.row, planned_end_at=self.start+timedelta(hours=1))]
        self.assertFalse(fixed_visible(self.now+timedelta(hours=1), self.now, rows))
        rows = merge_controls(rows, [dict(control_type='server_open', scheduled_at=self.start+timedelta(hours=2))], self.now)
        self.assertTrue(fixed_visible(self.now+timedelta(hours=1), self.now, rows))
        self.assertFalse(fixed_visible(self.start+timedelta(minutes=30), self.now, rows))
        self.assertEqual(rows[0]['reopened_at'], self.start+timedelta(hours=2))

    def test_opening_does_not_resurrect_pre_maintenance_bosses_or_delete_them(self):
        rows = merge_controls([self.row], [dict(control_type='server_open', scheduled_at=self.now)], self.now)
        before = deepcopy(rows)
        future = self.now+timedelta(hours=1)
        self.assertFalse(ordinary_visible(future, self.start-timedelta(hours=1), self.now, rows))
        self.assertTrue(ordinary_visible(future, self.now, self.now, rows))
        self.assertEqual(rows, before)

    def test_old_unclosed_record_cannot_poison_next_week_and_history_is_retained(self):
        rows = [self.row]
        previous = datetime(2026, 11, 4, 8)
        now = datetime(2026, 11, 5, 12)
        self.assertTrue(fixed_visible(now+timedelta(hours=1), now, rows, cycle_start=previous))
        self.assertIsNone(active_control(rows, now, previous, now))
        self.assertEqual(rows, [self.row])

    def test_opening_closes_only_latest_matching_occurrence(self):
        next_row = dict(self.row, scheduled_at=self.start+timedelta(days=1))
        now = self.now+timedelta(days=1)
        rows = merge_controls([self.row, next_row], [dict(control_type='server_open', scheduled_at=now)], now)
        self.assertNotIn('reopened_at', rows[0])
        self.assertEqual(rows[1]['reopened_at'], now)
        self.assertTrue(fixed_visible(now+timedelta(hours=1), now, rows))

    def test_regular_exception_changes_only_one_calendar_week(self):
        exception = dict(control_type='regular_maintenance', scheduled_at=self.start)
        reference = datetime(2026, 10, 28, 9)
        self.assertEqual(regular_boundary(reference, 2, 8, 0, [exception]), self.start)
        self.assertEqual(regular_boundary(self.now, 2, 8, 0, [exception]), datetime(2026, 11, 4, 8))

    def test_cancelled_regular_occurrence_does_not_restore_that_weeks_template(self):
        row = dict(control_type='regular_maintenance', scheduled_at=self.start, cancelled_at=self.start-timedelta(hours=1))
        self.assertEqual(regular_boundary(datetime(2026,10,28,6), 2,8,0,[row]), datetime(2026,11,4,8))

    def test_legacy_regular_label_is_read_without_mutation(self):
        row = dict(self.row, display_name='정기점검')
        self.assertEqual(control_kind(row), 'regular_maintenance')
        self.assertEqual(row['control_type'], 'temporary_maintenance')

    def test_explicit_delete_cutoff_and_new_future_maintenance_remain_effective(self):
        future = self.now+timedelta(days=1)
        self.assertFalse(fixed_visible(future, self.now, [], cutoff=future))
        self.assertFalse(fixed_visible(future+timedelta(hours=1), self.now,
                                     [dict(self.row, scheduled_at=future)]))


class HostAndInputTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 29, 12)
        self.app = app_at(self.now)
        guard = patch('program_update_guard.active', return_value=False)
        guard.start()
        self.addCleanup(guard.stop)

    def test_control_cut_is_a_past_dated_control_and_normal_cut_is_unchanged(self):
        now = datetime(2026,10,30,0,15)
        app = test_edge_tts_voice.DiscordScheduleInputTests._parser_app(now)
        for text, kind in [('1031 서버오픈 컷','server_open'), ('1일 0900 정기점검 컷','regular_maintenance')]:
            rows, ignored = app._parse_schedule_input_lines(text, reference_datetime=now)
            self.assertEqual(ignored, 0)
            self.assertEqual(rows[0]['state'], 'control')
            self.assertEqual(rows[0]['control_type'], kind)
            self.assertNotIn('raw_key', rows[0])
            control = app._build_schedule_control_event_for_item(rows[0], now, now)
            self.assertEqual(control['scheduled_at'].date(), datetime(2026,10,29).date())
        self.assertEqual(app._parse_schedule_input_line('1031 보스 컷')['state'], 'scheduled')

    def test_dated_regular_does_not_rewrite_weekly_template_and_is_idempotent(self):
        start = datetime(2026,10,28,7)
        req = request(start, start+timedelta(hours=4), kind='regular_maintenance')
        self.assertEqual(apply_transition(self.app, req), 'applied')
        self.assertEqual(self.app.schedule_maintenance_hour_var.get(), '08시')
        self.assertEqual(self.app._get_schedule_previous_maintenance_datetime(self.now), start)
        self.assertEqual(self.app._get_schedule_maintenance_cutoff_datetime(self.now), datetime(2026,11,4,8))
        self.app._save_schedule_state.reset_mock()
        self.assertEqual(apply_transition(self.app, req), 'existing')
        self.app._save_schedule_state.assert_not_called()

    def test_transition_rollback_and_wrong_scope_do_not_mutate_controls(self):
        start = self.now+timedelta(hours=1)
        req = request(start, start+timedelta(hours=1))
        self.assertEqual(apply_transition(self.app, dict(req, server_id='8')), 'deferred')
        self.app._save_schedule_state.side_effect = OSError('disk full')
        with self.assertRaises(OSError):
            apply_transition(self.app, req)
        self.assertEqual(self.app.schedule_control_events, [])

    def test_completion_requires_matching_start_and_not_future_opening(self):
        start = self.now-timedelta(hours=3)
        self.assertEqual(apply_transition(self.app, request(start,self.now,action='complete')), 'conflict')
        self.app.schedule_control_events = [dict(control_type='temporary_maintenance',scheduled_at=start)]
        self.assertEqual(apply_transition(self.app, request(start,self.now+timedelta(hours=1),action='complete')), 'conflict')
        self.assertNotIn('reopened_at',self.app.schedule_control_events[0])

    def test_fixed_bosses_do_not_require_any_ordinary_boss_rows(self):
        rows = self.app._get_fixed_boss_schedule_rows_for_share_range(self.now,self.now.replace(hour=0),self.now.replace(hour=23))
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.app.schedule_events, [])


class NoticePipelineTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maintenance-lifecycle-unit-')
        self.addCleanup(temporary.cleanup)
        self.now = datetime(2026,9,23,6,tzinfo=KST)
        self.store = NoticeStore(temporary.name,'9',clock=lambda:self.now)
        self.app = app_at(self.now.replace(tzinfo=None))
        self.app._get_schedule_reference_datetime = lambda:self.now.replace(tzinfo=None)
        with self.store._transaction() as state:
            state['articles'] = {'start':article(title='임시 점검 안내')}
        guard=patch('program_update_guard.active',return_value=False)
        guard.start();self.addCleanup(guard.stop)

    def snapshot(self):
        controls=[]
        for row in self.app.schedule_control_events:
            controls.append({key:value.replace(tzinfo=KST).isoformat(timespec='seconds') if isinstance(value,datetime) else value
                             for key,value in row.items()})
        return dict(server_id='9',season='18',control_events=controls)

    def run_import(self):
        import_transitions(self.store,self.snapshot(),lambda req:apply_transition(self.app,req))

    def test_start_extension_completion_restart_and_discord_fixed_visibility(self):
        self.run_import()
        self.assertEqual(len(self.app.schedule_control_events),1)
        self.now=self.now.replace(hour=12)
        at=self.now.replace(hour=13,tzinfo=None)
        self.assertFalse(self.app._is_fixed_boss_schedule_row_visible(at,at-timedelta(hours=1),None))
        with self.store._transaction() as state:
            state['articles']['extension']=article(end='12:30',title='임시 점검 연장 안내')
        self.run_import()
        self.assertEqual(self.app.schedule_control_events[0]['planned_end_at'],at.replace(hour=12,minute=30))
        self.assertNotIn('reopened_at',self.app.schedule_control_events[0])
        self.now=self.now.replace(hour=13)
        with self.store._transaction() as state:
            state['articles']['complete']=article(end='12:20',title='임시 점검 완료 안내')
        self.run_import()
        self.assertEqual(self.app.schedule_control_events[0]['reopened_at'],at.replace(hour=12,minute=20))
        self.assertTrue(self.app._is_fixed_boss_schedule_row_visible(at,at,None))
        state=dict(schedule_events=[],schedule_control_events=self.app.schedule_control_events,
                   maintenance_weekday='수요일',maintenance_hour='08시',maintenance_minute='00분')
        wire=self.app._serialize_schedule_state_value(state)
        restored=self.app._deserialize_schedule_state_value(json.loads(json.dumps(wire)))
        fresh=app_at(at)
        fresh.schedule_control_events=restored['schedule_control_events']
        self.assertTrue(fresh._is_fixed_boss_schedule_row_visible(at,at,None))
        reader=ScheduleReader()
        entry=dict(boss_name='월드보스',time_value=at.time(),days={'수'},repeat_mode='weekly')
        with patch('boss_timer_discord_bot.load_json',return_value=wire),patch.object(reader,'_load_boss_definition_map',return_value={}),patch.object(reader,'_load_fixed_boss_entries',return_value=[entry]):
            self.assertEqual([row['name'] for row in reader.upcoming_rows(reference_datetime=at)],['월드보스'])
        count=self.app._save_schedule_state.call_count
        self.run_import()
        self.assertEqual(self.app._save_schedule_state.call_count,count)

    def test_cancellation_and_unmatched_completion_do_not_close_another_cycle(self):
        with self.store._transaction() as state:
            state['articles']['unmatched']=article(day=24,title='임시 점검 완료 안내')
        self.run_import()
        self.assertEqual(len(self.app.schedule_control_events),1)
        with self.store._transaction() as state:
            state['articles']['cancel']=article(title='임시 점검 취소 안내')
        self.run_import()
        self.assertIn('cancelled_at',self.app.schedule_control_events[0])
        self.assertNotIn('reopened_at',self.app.schedule_control_events[0])

    def test_conflicting_completions_are_held(self):
        self.run_import();self.now=self.now.replace(hour=13)
        with self.store._transaction() as state:
            state['articles']['one']=article(end='11:00',title='임시 점검 완료 안내')
            state['articles']['two']=article(end='12:00',title='임시 점검 완료 안내')
        self.run_import()
        self.assertNotIn('reopened_at',self.app.schedule_control_events[0])

    def test_late_regular_completion_creates_dated_occurrence_then_closes_it(self):
        self.now = self.now.replace(hour=13)
        with self.store._transaction() as state:
            state['articles'] = {'done': article(end='11:10', title='정기점검 완료 안내')}
        self.run_import()
        row = self.app.schedule_control_events[0]
        self.assertEqual(row['control_type'], 'regular_maintenance')
        self.assertEqual(row['scheduled_at'].hour, 7)
        self.assertEqual(row['reopened_at'].hour, 11)
        self.assertEqual(self.app.schedule_maintenance_hour_var.get(), '08시')

    def test_unreadable_extension_blocks_older_completion_for_same_occurrence(self):
        self.run_import()
        self.now = self.now.replace(hour=13)
        with self.store._transaction() as state:
            state['articles']['done'] = article(title='임시점검 완료 안내')
            state['articles']['extend'] = dict(article(title='임시점검 연장 안내'), body_error='unavailable')
        self.run_import()
        self.assertNotIn('reopened_at', self.app.schedule_control_events[0])

    def test_cancel_and_complete_conflict_is_held(self):
        self.run_import()
        self.now = self.now.replace(hour=13)
        with self.store._transaction() as state:
            state['articles']['done'] = article(title='임시점검 완료 안내')
            state['articles']['cancel'] = article(title='임시점검 취소 안내')
        self.run_import()
        row = self.app.schedule_control_events[0]
        self.assertNotIn('cancelled_at', row)
        self.assertNotIn('reopened_at', row)

    def test_new_callback_runs_on_observation_thread_without_old_importer(self):
        thread = threading.get_ident()
        def apply(req):
            self.assertEqual(threading.get_ident(), thread)
            return apply_transition(self.app, req)
        host = NS(data_root=self.store.root, get_server=lambda:('9','server'),
                  get_schedule_snapshot=self.snapshot, apply_maintenance_transition=Mock(side_effect=apply),
                  apply_regular_maintenance=Mock(), apply_temporary_maintenance=Mock(),
                  call_later=Mock(), cancel_later=Mock(), log=Mock())
        plugin = NoticePlugin(host)
        self.addCleanup(plugin.stop)
        plugin.stopped = False
        plugin.schedule_worker = Mock()
        plugin.schedule_worker.is_alive.return_value = True
        with patch('notice_module.payload.main.NoticeStore', return_value=self.store):
            plugin._schedule_tick()
        host.apply_maintenance_transition.assert_called_once()
        host.apply_regular_maintenance.assert_not_called()
        host.apply_temporary_maintenance.assert_not_called()
        host.log.assert_not_called()

    def test_fixed_jobs_and_public_snapshot_follow_same_confirmed_end(self):
        from test_web_boss_catalog import app_fixture
        from web_schedule_snapshot import collect_snapshot, validate_snapshot
        self.run_import()
        self.now = self.now.replace(hour=12)
        facade, _ = app_fixture()
        facade.fixed_boss_entries = self.app.fixed_boss_entries
        facade._get_schedule_reference_datetime = self.app._get_schedule_reference_datetime
        facade._get_schedule_visible_cutoff_datetime = self.app._get_schedule_visible_cutoff_datetime
        facade._normalize_fixed_boss_time_text = self.app._normalize_fixed_boss_time_text
        facade._get_schedule_boss_display_name = self.app._get_schedule_boss_display_name
        facade._is_fixed_boss_entry_active_on_date = self.app._is_fixed_boss_entry_active_on_date
        facade._get_schedule_share_server_name = lambda:'test'
        facade.current_season_no = '18'
        facade.schedule_active_entries = []
        facade.schedule_break_rows_enabled_var = variable(False)
        facade.schedule_control_events = self.app.schedule_control_events
        def collect(start,end,**kwargs):
            rows = self.app._get_fixed_boss_schedule_rows_for_share_range(self.now.replace(tzinfo=None),start,end)
            return [dict(kind='fixed',scheduled_at=row[0],boss_text=row[1],text_color=row[6],bg_color=row[7])
                    for row in rows],start,end
        facade._collect_schedule_share_rows = collect
        def fixed_today():
            raw = collect_snapshot(facade)
            raw.update(updated_at=raw['captured_at'], revision=1)
            data = validate_snapshot(raw)
            return [row for row in data['events'] if row['kind']=='fixed' and row['scheduled_at'].startswith('2026-09-23')]
        self.assertEqual(fixed_today(), [])
        with self.store._transaction() as state:
            state['articles']['done'] = article(end='11:30', title='임시점검 완료 안내')
        self.run_import()
        facade.schedule_control_events = self.app.schedule_control_events
        self.assertEqual(len(fixed_today()), 1)
        reader = ScheduleReader()
        at = self.now.replace(hour=13,tzinfo=None)
        class Clock:
            now = staticmethod(lambda:self.now.replace(tzinfo=None))
            combine = staticmethod(datetime.combine)
        entry = dict(boss_name='월드보스',time_value=at.time(),days={'수'},repeat_mode='weekly')
        state = dict(schedule_control_events=self.app.schedule_control_events)
        with patch('boss_timer_discord_bot.datetime', Clock), patch.object(reader,'_load_fixed_boss_entries',return_value=[entry]):
            self.assertTrue(reader._build_fixed_boss_jobs({},state))
            state['schedule_control_events'] = [dict(self.app.schedule_control_events[0])]
            state['schedule_control_events'][0].pop('reopened_at')
            self.assertEqual(reader._build_fixed_boss_jobs({},state), [])


if __name__=='__main__':
    unittest.main()
