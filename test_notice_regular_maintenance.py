"""Confirmed notice -> saved regular schedule -> prerecorded alarm; offline only."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import re
import tempfile
import threading
from types import MethodType, SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from notice_module.payload.notice_analysis import analyze_notice
from notice_module.payload.notice_management import KST, NoticeStore
from notice_module.payload.notice_maintenance_import import (import_regular_maintenance,
                                                            regular_maintenance_candidate)
from test_notice_server_open import article
from notice_module.payload.main import NoticePlugin


class RegularImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='boss-regular-maintenance-unit-')
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 9, 23, 7, 50, tzinfo=KST)
        self.store = NoticeStore(self.temp.name, '9', clock=lambda: self.now)
        self.snapshot = dict(server_id='9', season='18', events=[],
                             regular_maintenance='2026-09-23T08:00:00+09:00')
        self.apply = Mock(return_value='applied')
        with self.store._transaction() as state:
            state['articles'] = {'regular': article()}

    def import_once(self):
        import_regular_maintenance(self.store, self.snapshot, self.apply)

    def test_ongoing_seven_oclock_notice_updates_eight_oclock_schedule_once(self):
        self.import_once()
        request = self.apply.call_args.args[0]
        self.assertEqual(request['scheduled_at'], '2026-09-23T07:00:00+09:00')
        self.assertEqual(request['server_id'], '9')
        self.assertEqual(request['season'], '18')
        self.assertEqual(request['event_until'], '2026-09-23T11:00:00+09:00')
        receipt = next(iter(self.store.snapshot()['regular_maintenance_imports'].values()))
        self.assertEqual(receipt['status'], 'applied')
        self.import_once()
        self.apply.assert_called_once()

    def test_upcoming_notice_imports_before_start_and_other_server_is_ignored(self):
        self.now = self.now.replace(day=22, hour=18)
        import_regular_maintenance(self.store, dict(self.snapshot, server_id='8'), self.apply)
        self.apply.assert_not_called()
        self.import_once()
        self.apply.assert_called_once()

    def test_extension_does_not_undo_manual_change_but_corrected_start_applies(self):
        self.import_once()
        with self.store._transaction() as state:
            state['articles']['regular'] = article(end='12:00', title='정기점검 연장 안내')
        self.import_once()
        self.apply.assert_called_once()
        with self.store._transaction() as state:
            item = state['articles']['regular']
            item['body'] = item['body'].replace('07:00', '06:30')
            item['analysis'] = analyze_notice(item)
        self.import_once()
        self.assertEqual(self.apply.call_count, 2)
        self.assertIn('T06:30:', self.apply.call_args.args[0]['scheduled_at'])
        with self.store._transaction() as state:
            state['articles']['regular'] = article()
        self.import_once()
        self.assertEqual(self.apply.call_count, 3)
        self.assertIn('T07:00:', self.apply.call_args.args[0]['scheduled_at'])

    def test_deferred_retries_but_failed_or_interrupted_does_not_blindly_repeat(self):
        self.apply.return_value = 'deferred'
        self.import_once()
        self.assertEqual(self.store.snapshot()['regular_maintenance_imports'], {})
        self.apply.side_effect = OSError('disk full')
        self.import_once()
        self.assertEqual(next(iter(self.store.snapshot()['regular_maintenance_imports'].values()))['status'], 'failed')
        self.import_once()
        self.assertEqual(self.apply.call_count, 2)

    def test_ambiguous_cancelled_unpinned_and_previous_days_finished_are_ignored(self):
        for changes in ({'title': '정기점검 취소 안내'}, {'body_error': 'failed'},
                        {'pinned': False}, {'category': 'general'}):
            with self.subTest(changes=changes):
                with self.store._transaction() as state:
                    state['articles'] = {'regular': dict(article(), **changes)}
                self.import_once()
        self.apply.assert_not_called()
        with self.store._transaction() as state:
            state['articles'] = {'regular': article()}
        self.now = self.now.replace(day=24, hour=12)
        self.import_once()
        self.apply.assert_not_called()

    def test_same_day_finished_notice_keeps_original_date_and_time(self):
        self.now = self.now.replace(hour=13)
        self.import_once()
        self.apply.assert_called_once()
        self.assertEqual(self.apply.call_args.args[0]['scheduled_at'], '2026-09-23T07:00:00+09:00')
        receipt = next(iter(self.store.snapshot()['regular_maintenance_imports'].values()))
        self.assertEqual(receipt['scheduled_at'], '2026-09-23T07:00:00+09:00')

    def test_conflicting_start_or_unknown_extension_is_held(self):
        with self.store._transaction() as state:
            other = article(key='other')
            other['analysis'] = deepcopy(other['analysis'])
            other['analysis']['windows'][0]['start'] = '2026-09-23T08:00:00+09:00'
            state['articles']['other'] = other
        self.import_once()
        self.apply.assert_not_called()
        with self.store._transaction() as state:
            state['articles']['other'] = dict(article(key='other', title='정기점검 연장 안내'),
                                               analysis={'windows': []})
        self.import_once()
        self.apply.assert_not_called()

    def test_collection_or_maintenance_category_off_is_preserved(self):
        for mode in ('collection', 'category'):
            with self.store._transaction() as state:
                state['settings']['collection_enabled'] = mode != 'collection'
                state['settings']['categories']['maintenance'] = mode != 'category'
            self.import_once()
        self.apply.assert_not_called()

    def test_current_cycle_wins_over_already_pinned_next_week(self):
        with self.store._transaction() as state:
            state['articles']['next'] = article(day=30, key='next')
            state['articles']['next']['published_date'] = '2026-09-23'
        self.import_once()
        self.assertIn('2026-09-23T07:', self.apply.call_args.args[0]['scheduled_at'])

    def test_plugin_calls_host_on_observation_thread_not_audio_worker(self):
        current_thread = threading.get_ident()
        self.apply.side_effect = lambda _: ('applied' if threading.get_ident() == current_thread
                                           else self.fail('host callback ran outside the observation thread'))
        host = NS(data_root=self.store.root, get_server=lambda: ('9', 'server'),
                  get_schedule_snapshot=lambda: self.snapshot, call_later=Mock(), cancel_later=Mock(), log=Mock())
        plugin = NoticePlugin(host)
        self.addCleanup(plugin.stop)
        host.apply_regular_maintenance = self.apply
        plugin.stopped = False
        plugin.schedule_worker = Mock()
        plugin.schedule_worker.is_alive.return_value = True
        elapsed = [0]
        plugin.change_gate.clock = lambda: elapsed[0]
        with patch('notice_module.payload.main.NoticeStore', return_value=self.store):
            plugin._schedule_tick()
            self.apply.assert_called_once()  # No five-second/audio-worker delay.
            elapsed[0] = 6
            plugin._schedule_tick()
        self.apply.assert_called_once()

    def test_missing_date_reference_is_held_instead_of_crashing(self):
        with self.store._transaction() as state:
            state['articles']['regular'].update(published_date=None, first_seen=None)
        self.import_once()
        self.apply.assert_not_called()


class RegularHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        names = {'_apply_notice_regular_maintenance', '_get_schedule_maintenance_cutoff_datetime',
                 '_get_schedule_previous_maintenance_datetime', '_get_schedule_maintenance_alarm_candidates',
                 '_process_schedule_maintenance_alarm_tick', '_get_schedule_share_maintenance_rows'}
        methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
        cls.methods = dict(datetime=datetime, timedelta=timedelta, re=re)
        exec(compile(ast.Module(body=methods, type_ignores=[]), '<regular-maintenance-host>', 'exec'), cls.methods)

    def app(self):
        def variable(value):
            slot = [value]
            return NS(get=lambda: slot[0], set=lambda updated: slot.__setitem__(0, updated))
        now = datetime(2026, 9, 23, 6, 59, 30)
        app = NS(schedule_server_profile_id='9', current_season_no='18',
                 schedule_maintenance_weekday_var=variable('수요일'),
                 schedule_maintenance_hour_var=variable('08시'), schedule_maintenance_minute_var=variable('00분'),
                 _get_schedule_reference_datetime=Mock(return_value=now), _get_schedule_maintenance_weekday_index=lambda: 2,
                 _save_schedule_state=Mock(), _refresh_schedule_tree_scope=Mock(), _append_debug_log=Mock(),
                 _get_schedule_server_open_alarm_datetime=lambda _: None, schedule_control_events=[],
                 _build_schedule_alarm_due_key=Mock(), _should_fire_schedule_alarm_key=Mock(return_value=True),
                 _build_schedule_maintenance_alarm_audio_paths=Mock(return_value=['regular.wav', '1min.wav']),
                 _submit_schedule_voice_request=Mock())
        for name, value in self.methods.items():
            if name in {'datetime', 'timedelta', 're', '__builtins__'}:
                continue
            setattr(app, name, MethodType(value, app))
        return app

    def request(self):
        return dict(server_id='9', season='18', key='18/2026-09-23T07:00:00+09:00',
                    scheduled_at='2026-09-23T07:00:00+09:00', event_until='2026-09-23T11:00:00+09:00')

    def apply(self, app, request=None):
        with patch('program_update_guard.active', return_value=False):
            return app._apply_notice_regular_maintenance(request or self.request())

    def test_saved_time_drives_prerecorded_alarm_at_seven_and_never_eight(self):
        app = self.app()
        self.assertEqual(self.apply(app), 'applied')
        self.assertEqual(app.schedule_maintenance_hour_var.get(), '07시')
        app._save_schedule_state.assert_called_once_with(mark_github_dirty=True, raise_on_error=True)
        rows = app._get_schedule_maintenance_alarm_candidates(datetime(2026, 9, 23, 6, 59, 30))
        self.assertIn((datetime(2026, 9, 23, 7), '정기점검', 'regular_maintenance'), rows)
        self.assertEqual(app._get_schedule_maintenance_alarm_candidates(datetime(2026, 9, 23, 7, 59, 30)), [])
        app._process_schedule_maintenance_alarm_tick(datetime(2026, 9, 23, 6, 59))
        self.assertEqual(app._submit_schedule_voice_request.call_args.kwargs['target_time'], datetime(2026, 9, 23, 7))
        self.assertEqual(app._submit_schedule_voice_request.call_args.kwargs['clip_paths'], ['regular.wav', '1min.wav'])
        app._process_schedule_maintenance_alarm_tick(datetime(2026, 9, 23, 7, 59))
        app._submit_schedule_voice_request.assert_called_once()

    def test_already_started_notice_updates_without_speaking_retroactively(self):
        app = self.app()
        app._get_schedule_reference_datetime.return_value = datetime(2026, 9, 23, 7, 50)
        self.assertEqual(self.apply(app), 'applied')
        self.assertEqual(app._get_schedule_maintenance_alarm_candidates(datetime(2026, 9, 23, 7, 50)), [])
        self.assertEqual(app._get_schedule_previous_maintenance_datetime(datetime(2026, 9, 23, 7, 50)),
                         datetime(2026, 9, 23, 7))

    def test_october_21_elapsed_start_is_today_at_seven_in_schedule_rows(self):
        for hour in (8, 13, 23):
            with self.subTest(collection_hour=hour):
                app = self.app()
                now = datetime(2026, 10, 21, hour, 10)
                app._get_schedule_reference_datetime.return_value = now
                request = dict(self.request(), scheduled_at='2026-10-21T07:00:00+09:00',
                               event_until='2026-10-21T12:00:00+09:00')
                self.assertEqual(self.apply(app, request), 'applied')
                rows = app._get_schedule_share_maintenance_rows(
                    datetime(2026, 10, 21), datetime(2026, 10, 22, 23, 59, 59), reference_now=now)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['scheduled_at'], datetime(2026, 10, 21, 7))
                self.assertTrue(rows[0]['is_elapsed'])
                app._process_schedule_maintenance_alarm_tick(now)
                app._submit_schedule_voice_request.assert_not_called()
                self.assertEqual(app._get_schedule_maintenance_alarm_candidates(datetime(2026, 10, 22, 6, 59)), [])

    def test_wrong_profile_handover_and_update_defer_without_mutation(self):
        app = self.app()
        self.assertEqual(self.apply(app, dict(self.request(), server_id='8')), 'deferred')
        self.assertEqual(self.apply(app, dict(self.request(), season='17')), 'deferred')
        app.discord_handover_busy = True
        self.assertEqual(self.apply(app), 'deferred')
        app.discord_handover_busy = False
        with patch('program_update_guard.active', return_value=True):
            self.assertEqual(app._apply_notice_regular_maintenance(self.request()), 'deferred')
        app._save_schedule_state.assert_not_called()
        self.assertEqual(app.schedule_maintenance_hour_var.get(), '08시')

    def test_save_failure_restores_original_time_and_existing_value_does_not_save(self):
        app = self.app()
        app._save_schedule_state.side_effect = OSError('disk full')
        with self.assertRaises(OSError):
            self.apply(app)
        self.assertEqual(app.schedule_maintenance_hour_var.get(), '08시')
        app._refresh_schedule_tree_scope.assert_not_called()
        app._save_schedule_state.reset_mock(side_effect=True)
        app.schedule_maintenance_hour_var.set('07시')
        self.assertEqual(self.apply(app), 'existing')
        app._save_schedule_state.assert_not_called()

    def test_disabled_regular_maintenance_is_not_automatically_enabled(self):
        app = self.app()
        app.schedule_maintenance_hour_var.set('00시')
        self.assertEqual(self.apply(app), 'conflict')
        app._save_schedule_state.assert_not_called()


if __name__ == '__main__':
    unittest.main()
