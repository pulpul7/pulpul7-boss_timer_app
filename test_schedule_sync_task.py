"""Offline sync lifecycle, data validation and real GUI-method regressions."""
import ast
from datetime import datetime, timedelta
import os
from pathlib import Path
import re
import time
import traceback
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from schedule_sync_task import ScheduleSyncTask
from schedule_sync_validation import download_problem, schedule_content_problem


class Root:
    def __init__(self):
        self.now, self.serial, self.jobs = 0., 0, {}

    def after(self, delay, callback):
        self.serial += 1
        self.jobs[self.serial] = (self.now + delay / 1000, callback)
        return self.serial

    def after_cancel(self, job):
        self.jobs.pop(job, None)

    def advance(self, milliseconds):
        until = self.now + milliseconds / 1000
        while self.jobs:
            job = min(self.jobs, key=lambda key: self.jobs[key][0])
            when, callback = self.jobs[job]
            if when > until:
                break
            self.jobs.pop(job)
            self.now = when
            callback()
        self.now = until


class TaskTests(unittest.TestCase):
    def setUp(self):
        self.root = Root()
        self.progress, self.failure, self.close, self.release = [Mock() for _ in range(4)]
        self.task = ScheduleSyncTask(self.root, progress=self.progress, failure=self.failure,
            close=self.close, release=self.release, clock=lambda: self.root.now)

    def assert_closed(self):
        self.assertTrue(self.task.done)
        self.close.assert_called_once()
        self.release.assert_called_once()

    def test_worker_exception_is_reported_on_ui_poll_and_closes(self):
        self.task.run(lambda: (_ for _ in ()).throw(ValueError('not logged raw')))
        self.failure.assert_not_called()
        self.root.advance(50)
        self.assertEqual(self.failure.call_args.args[:2], ('exception', 'download'))
        self.assert_closed()

    def test_preparation_exception_closes(self):
        self.task.post_result(lambda: (_ for _ in ()).throw(RuntimeError('prepare')))
        self.root.advance(50)
        self.assertEqual(self.failure.call_args.args[:2], ('exception', 'prepare'))
        self.assert_closed()

    def test_apply_exception_and_normal_return_both_close(self):
        self.task.apply(lambda: (_ for _ in ()).throw(RuntimeError('apply')))
        self.assertEqual(self.failure.call_args.args[:2], ('exception', 'apply'))
        self.assert_closed()

    def test_normal_apply_return_closes_and_releases_controls(self):
        self.task.apply(lambda: None)
        self.failure.assert_not_called()
        self.assert_closed()

    def test_cancel_discards_queued_and_late_results(self):
        apply = Mock()
        self.task.post_result(apply)
        self.assertTrue(self.task.cancel())
        self.task.post_result(apply)
        self.task.apply(apply)
        self.root.advance(1000)
        apply.assert_not_called()
        self.assert_closed()

    def test_timeout_wins_over_a_queued_result(self):
        apply = Mock()
        self.task.post_result(apply)
        self.task.deadline = 0
        self.root.advance(50)
        apply.assert_not_called()
        self.assertEqual(self.failure.call_args.args[0], 'timeout')
        self.assert_closed()

    def test_expired_delayed_apply_never_writes(self):
        apply = Mock()
        self.task.deadline = 0
        self.task.apply(apply)
        apply.assert_not_called()
        self.assert_closed()

    def test_confirmation_and_writes_cannot_be_cancelled_halfway(self):
        def apply():
            self.assertFalse(self.task.cancel())
            self.root.advance(121000)
            self.assertFalse(self.task.done)
        self.task.apply(apply)
        self.failure.assert_not_called()
        self.assert_closed()

    def test_progress_is_queued_and_completion_is_idempotent(self):
        self.task.post_progress('download')
        self.progress.assert_not_called()
        self.root.advance(50)
        self.progress.assert_called_once_with('download')
        self.task.complete()
        self.task.complete()
        self.assert_closed()


class ValidationTests(unittest.TestCase):
    def check(self, actual='2026.10.18.002', expected='2026.10.18.002', local='2026.10.18.001', expected_hash='ok', actual_hash='ok'):
        return download_problem(dict(dataVersion=actual), label='스케쥴', index_version=expected,
            local_version=local, index_hash=expected_hash, content_hash=lambda _: actual_hash,
            compare_versions=lambda a,b: (a>b)-(a<b))

    def test_matching_new_file_is_allowed(self):
        self.assertEqual(self.check(), '')

    def test_new_index_with_old_file_is_blocked(self):
        self.assertIn('버전이 다릅니다', self.check(actual='2026.10.14.001'))

    def test_actual_file_older_than_local_is_blocked(self):
        self.assertIn('구버전', self.check(local='2026.10.18.003'))

    def test_missing_declared_version_is_not_assumed_current(self):
        self.assertIn('버전 없음', self.check(actual=''))

    def test_matching_version_with_wrong_content_is_blocked(self):
        self.assertIn('내용이 일치하지', self.check(actual_hash='wrong'))

    def test_empty_schedule_cannot_replace_current_data(self):
        self.assertIn('비어', schedule_content_problem({}, {}, datetime.now()))
        self.assertIn('비어', schedule_content_problem(dict(schedule_events=123), {}, datetime.now()))

    def test_only_past_remote_cannot_erase_future_local_schedule(self):
        now = datetime(2026,10,18,5,30)
        incoming = dict(schedule_events=[dict(scheduled_at=now-timedelta(days=4))])
        current = dict(schedule_events=[dict(scheduled_at=now+timedelta(days=3))])
        self.assertIn('지난 일정', schedule_content_problem(incoming, current, now))
        incoming['schedule_events'].append(dict(scheduled_at=(now+timedelta(days=1)).isoformat()))
        self.assertEqual(schedule_content_problem(incoming, current, now), '')


class Dialog:
    def __init__(self):
        self.exists, self.hidden, self.grabbed = True, False, True
    def winfo_exists(self): return self.exists
    def protocol(self, *args): pass
    def bind(self, *args): pass
    def update_idletasks(self): pass
    def grab_release(self): self.grabbed = False
    def withdraw(self): self.hidden = True
    def deiconify(self): self.hidden = False
    def lift(self): pass
    def grab_set(self): self.grabbed = True


class GuiSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        cls.nodes = [n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef)
            and n.name in {'_sync_selected_github_schedule','_confirm_schedule_sync_replace'}]

    def app(self):
        workers = []
        env = dict(time=time, traceback=traceback, os=os, datetime=datetime, re=re,
            tk=NS(TclError=type('TclError',(Exception,),{})),
            threading=NS(Thread=lambda **kw: NS(start=lambda: workers.append(kw['target']))))
        exec(compile(ast.Module(body=self.nodes,type_ignores=[]),'sync-gui-regression','exec'),env)
        self.sync = env['_sync_selected_github_schedule']
        self.confirm = env['_confirm_schedule_sync_replace']
        root, dialog = Root(), Dialog()
        entry = dict(id='9',name='server',schedule='data/schedule.json',scheduleVersion='2026.10.18.002')
        events = dict(schedule_events=[dict(scheduled_at=datetime.now()+timedelta(days=1))])
        wrapper = dict(dataVersion='2026.10.18.002',payload=events)
        app = NS(root=root, schedule_window=object(), schedule_status_var=Mock(),
            discord_handover_busy=False, schedule_github_server_combo=None,
            _get_schedule_github_sync_cooldown_remaining_seconds=lambda:0,
            _get_selected_github_server_entry=lambda:dict(entry),
            _get_schedule_state_storage_path=lambda:'same-profile',
            _create_schedule_restore_snapshot=lambda:events,
            _get_schedule_restore_snapshot_signature=lambda _: 'same',
            _get_github_import_meta_versions_for_entry=lambda _:('2026.10.18.001',''),
            _load_github_local_schedule_payload=lambda _:(None,''),
            _get_github_local_cache_versions=lambda *_:('',''),
            _get_github_cached_item_for_entry=lambda _:{},
            _show_schedule_github_sync_progress_dialog=Mock(return_value=(dialog,Mock(),Mock())),
            _github_get_json_file=Mock(side_effect=lambda path: (dict(servers=[entry]) if 'index' in path else wrapper,'sha','')),
            _extract_github_server_entries=lambda d:d['servers'],
            _find_github_server_entry=lambda entries,_:entries[0],
            _is_github_schedule_sync_current=lambda **_:False,
            _unwrap_github_schedule_payload=lambda d:d['payload'],
            _merge_local_github_server_entries=lambda e:e,
            _get_github_schedule_content_hash=lambda _: 'hash',
            _get_github_boss_config_content_hash=lambda _: 'hash',
            _compare_github_data_versions=lambda a,b:(a>b)-(a<b),
            _compact_schedule_payload_for_retention=lambda p:(p,False),
            _widget_available=lambda w:w is not None)
        for name in ('_start_schedule_github_sync_cooldown','_set_schedule_github_controls_state',
            '_cache_github_server_entries_from_index','_save_github_server_entries_cache',
            '_append_debug_log','_append_current_schedule_restore_history_marker',
            '_update_github_import_meta','_save_github_local_payload_cache','_save_schedule_state'):
            setattr(app,name,Mock())
        app._hide_schedule_github_sync_progress_dialog = Mock(side_effect=lambda *_:setattr(dialog,'exists',False))
        app._apply_loaded_schedule_shared_payload = Mock(return_value=True)
        app._confirm_schedule_sync_replace = Mock(return_value=True)
        return app, workers, dialog, wrapper

    def run_sync(self, app, workers):
        self.sync(app)
        workers.pop(0)()
        app.root.advance(100)

    def test_confirmation_hides_progress_and_success_releases_everything(self):
        app,workers,dialog,_ = self.app()
        def confirm(*_,**__):
            self.assertTrue(dialog.hidden)
            self.assertFalse(dialog.grabbed)
            return True
        app._confirm_schedule_sync_replace.side_effect = confirm
        self.run_sync(app,workers)
        app._apply_loaded_schedule_shared_payload.assert_called_once()
        self.assertIsNone(app._schedule_sync_task)
        app._hide_schedule_github_sync_progress_dialog.assert_called_once()
        self.assertTrue(app._set_schedule_github_controls_state.call_args.args[0])

    def test_worker_exception_does_not_leave_modal_open(self):
        app,workers,_,_ = self.app()
        app._github_get_json_file.side_effect = ValueError('secret must not be logged')
        self.run_sync(app,workers)
        app._apply_loaded_schedule_shared_payload.assert_not_called()
        self.assertIsNone(app._schedule_sync_task)
        self.assertIn('ValueError', app.schedule_status_var.set.call_args.args[0])
        self.assertNotIn('secret',str(app._append_debug_log.call_args))

    def test_prepare_exception_does_not_leave_modal_open(self):
        app,workers,_,_ = self.app()
        app._merge_local_github_server_entries = Mock(side_effect=RuntimeError('prepare'))
        self.run_sync(app,workers)
        app._apply_loaded_schedule_shared_payload.assert_not_called()
        self.assertIsNone(app._schedule_sync_task)

    def test_rejected_confirmation_never_changes_schedule_or_versions(self):
        app,workers,_,_ = self.app()
        app._confirm_schedule_sync_replace.return_value = False
        self.run_sync(app,workers)
        app._apply_loaded_schedule_shared_payload.assert_not_called()
        app._update_github_import_meta.assert_not_called()
        self.assertIsNone(app._schedule_sync_task)

    def test_apply_exception_is_reported_and_closed(self):
        app,workers,_,_ = self.app()
        app._apply_loaded_schedule_shared_payload.side_effect = RuntimeError('apply')
        self.run_sync(app,workers)
        self.assertIn('RuntimeError',app.schedule_status_var.set.call_args.args[0])
        self.assertIsNone(app._schedule_sync_task)

    def test_empty_or_past_only_remote_never_replaces_future_schedule(self):
        for incoming in ([],[dict(scheduled_at=datetime.now()-timedelta(days=4))]):
            app,workers,_,wrapper = self.app()
            wrapper['payload'] = dict(schedule_events=incoming)
            self.run_sync(app,workers)
            app._apply_loaded_schedule_shared_payload.assert_not_called()
            app._update_github_import_meta.assert_not_called()

    def test_index_file_version_mismatch_never_updates_local_schedule(self):
        app,workers,_,wrapper = self.app()
        wrapper['dataVersion'] = '2026.10.14.001'
        self.run_sync(app,workers)
        app._apply_loaded_schedule_shared_payload.assert_not_called()
        app._update_github_import_meta.assert_not_called()
        self.assertIn('버전이 다릅니다',app.schedule_status_var.set.call_args.args[0])

    def test_duplicate_sync_does_not_start_a_second_worker(self):
        app,workers,_,_ = self.app()
        self.sync(app)
        self.sync(app)
        self.assertEqual(len(workers),1)
        app._schedule_sync_task.cancel()

    def test_profile_switch_before_apply_preserves_current_schedule(self):
        app,workers,_,_ = self.app()
        self.sync(app)
        workers.pop(0)()
        app._get_schedule_state_storage_path = lambda:'another-profile'
        app.root.advance(100)
        app._apply_loaded_schedule_shared_payload.assert_not_called()
        app._update_github_import_meta.assert_not_called()

    def test_bad_boss_config_version_prevents_partial_schedule_apply(self):
        app,workers,_,wrapper = self.app()
        entry = dict(app._get_selected_github_server_entry(),bosses='data/bosses.json',
                     bossConfigVersion='2026.10.18.002')
        app._get_selected_github_server_entry = lambda:entry
        def get(path):
            if 'index' in path:
                return dict(servers=[entry]),'sha',''
            if 'bosses' in path:
                return dict(dataVersion='2026.10.14.001',boss_definitions=[]),'sha',''
            return wrapper,'sha',''
        app._github_get_json_file.side_effect = get
        app._unwrap_github_boss_config_payload = lambda raw:raw
        self.run_sync(app,workers)
        app._apply_loaded_schedule_shared_payload.assert_not_called()
        app._update_github_import_meta.assert_not_called()
        self.assertIn('버전이 다릅니다',app.schedule_status_var.set.call_args.args[0])

    def test_replace_confirmation_uses_schedule_window_as_parent(self):
        app,_,_,_ = self.app()
        app._current_schedule_has_data = lambda:True
        app._normalize_schedule_server_profile_season_key = lambda _: 'season_18'
        app._get_active_schedule_server_profile_season_key = lambda:'season_18'
        app._show_centered_messagebox = Mock(return_value=False)
        self.assertFalse(self.confirm(app,{},local_version='1',remote_version='2',local_dirty=True))
        self.assertIs(app._show_centered_messagebox.call_args.kwargs['parent'],app.schedule_window)


if __name__ == '__main__':
    unittest.main()
