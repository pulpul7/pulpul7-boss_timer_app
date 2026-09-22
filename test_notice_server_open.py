"""Collected maintenance -> input default, without real UI/network/user data."""
import ast
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace, MethodType
from unittest.mock import Mock
import tempfile
import unittest

from notice_module.payload.notice_management import KST, NoticeStore
from notice_module.payload.notice_server_open import server_open_override
from notice_module.payload.notice_analysis import analyze_notice
from notice_module.payload.main import NoticePlugin
from notice_runtime import NoticeRuntime


def article(day=23, end='11:00', title='정기 점검 안내', key='regular'):
    item = dict(id=key, category='maintenance', title=title, published_date=f'2026-09-{day-1:02}',
                first_seen=f'2026-09-{day-1:02}T18:00:00+09:00', pinned=True,
                body=f'점검 일시: 2026년 9월 {day}일 07:00 ~ {end}', url='https://example.invalid/notice')
    item['analysis'] = analyze_notice(item)
    return item


class ServerOpenRulesTests(unittest.TestCase):
    def setUp(self):
        self.state = {'articles': {'regular': article()}}
        self.now = datetime(2026, 9, 23, 11, tzinfo=KST)

    def value(self, now=None, reference=None):
        return server_open_override(self.state, now or self.now, reference)

    def test_published_before_maintenance_does_not_apply_until_exact_end(self):
        for now in (self.now.replace(day=22, hour=18), self.now.replace(hour=7), self.now - timedelta(microseconds=1)):
            self.assertIsNone(self.value(now))
        self.assertEqual(self.value()['server_open'], '2026-09-23T11:00:00+09:00')
        self.assertEqual(self.value()['valid_until'], '2026-09-30T07:00:00+09:00')

    def test_only_this_cycle_and_actual_time_not_future_reference(self):
        self.assertIsNone(self.value(self.now.replace(day=22), self.now))
        self.assertIsNone(self.value(reference=self.now.replace(day=22)))
        self.assertIsNotNone(self.value(self.now.replace(day=29)))
        self.assertIsNone(self.value(self.now.replace(day=30, hour=7)))

    def test_next_week_publication_does_not_replace_current_week(self):
        self.state['articles']['next'] = article(day=30, end='12:00', key='next')
        now = self.now.replace(day=29)
        self.assertEqual(self.value(now)['server_open'], '2026-09-23T11:00:00+09:00')
        self.assertIsNone(self.value(self.now.replace(day=30, hour=11)))
        self.assertEqual(self.value(self.now.replace(day=30, hour=12))['server_open'], '2026-09-30T12:00:00+09:00')

    def test_extension_then_explicit_completion_and_conflict(self):
        self.state['articles']['extend'] = article(end='12:00', title='정기점검 연장 안내', key='extend')
        self.assertIsNone(self.value())
        self.assertEqual(self.value(self.now.replace(hour=12))['server_open'], '2026-09-23T12:00:00+09:00')
        self.state['articles']['another'] = article(end='13:00', title='정기점검 연장 안내', key='another')
        self.assertIsNone(self.value(self.now.replace(hour=14)))
        self.state['articles']['done'] = article(end='11:30', title='정기점검 완료 안내', key='done')
        self.assertEqual(self.value(self.now.replace(hour=14))['server_open'], '2026-09-23T11:30:00+09:00')

    def test_temporary_ambiguous_cancelled_and_fetch_error_are_not_used(self):
        for changes in ({'title': '임시점검 안내'}, {'title': '정기점검 취소 안내'},
                        {'body_error': 'fetch failed'}, {'analysis': {'windows': []}}):
            with self.subTest(changes=changes):
                self.state['articles']['regular'] = dict(article(), **changes)
                self.assertIsNone(self.value())

    def test_unknown_extension_on_maintenance_day_blocks_original_end(self):
        self.state['articles']['extend'] = dict(article(title='정기점검 연장 안내'),
            published_date='2026-09-23', analysis={'windows': []})
        self.assertIsNone(self.value(self.now.replace(hour=13)))

    def test_unpinned_facts_remain_usable_for_week_without_mutation(self):
        self.state['articles']['regular'].update(pinned=False, removed_at=self.now.isoformat())
        before = deepcopy(self.state)
        self.assertIsNotNone(self.value())
        self.assertEqual(self.state, before)


class ServerOpenIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree = ast.parse(Path('boss_timer_gui.py').read_text(encoding='utf-8-sig'))
        names = {'_get_schedule_default_server_open_datetime', '_get_schedule_input_server_open_datetime',
                 '_get_schedule_input_auto_server_open_datetime', '_remember_schedule_input_server_open_datetime',
                 '_clear_schedule_input_custom_server_open_datetime', '_open_schedule_input_window_normal',
                 '_apply_notice_temporary_maintenance'}
        methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
        cls.namespace = {'datetime': datetime, 'timedelta': timedelta}
        exec(compile(ast.Module(body=methods, type_ignores=[]), 'schedule-open-adapter', 'exec'), cls.namespace)

    def setUp(self):
        self.now = datetime(2026, 9, 23, 12)
        self.auto = {'server_open': '2026-09-23T11:00:00+09:00', 'valid_until': '2026-09-30T07:00:00+09:00'}
        self.app = SimpleNamespace(notice_runtime=Mock(), schedule_server_profile_id='odin9',
            schedule_input_custom_server_open_datetime=None, schedule_input_custom_server_open_expires_at=None,
            schedule_input_custom_server_open_saved_at=None,
            _get_schedule_reference_datetime=lambda: self.now,
            _has_schedule_input_custom_server_open_expired_by_maintenance=lambda now: False,
            _get_schedule_next_reset_datetime_for_custom_server_open=lambda now: datetime(2026, 9, 30, 7),
            _append_debug_log=Mock())
        self.app.notice_runtime.get_server_open_override.return_value = self.auto
        for name, value in self.namespace.items():
            if name.startswith(('_get_', '_remember_', '_clear_', '_open_', '_apply_notice_')):
                setattr(self.app, name, MethodType(value, self.app))

    def test_temporary_import_host_checks_profile_duplicate_conflict_and_save_failure(self):
        app = self.app
        app.current_season_no = '18'
        app.schedule_control_events = []
        request = dict(server_id='odin9', season='18', key='CT9G/1', scheduled_at='2026-09-23T14:00:00+09:00')
        app._add_temporary_maintenance_control_event = Mock()
        app.discord_handover_busy = True
        self.assertEqual(app._apply_notice_temporary_maintenance(request), 'deferred')
        app.discord_handover_busy = False
        self.assertEqual(app._apply_notice_temporary_maintenance(dict(request, server_id='odin8')), 'deferred')
        self.assertEqual(app._apply_notice_temporary_maintenance(request), 'applied')
        app._add_temporary_maintenance_control_event.assert_called_once_with(self.now.replace(hour=14),
            source_label='알리미 공지', notice_key='CT9G/1')
        app.schedule_control_events = [dict(control_type='temporary_maintenance', scheduled_at=self.now.replace(hour=14))]
        self.assertEqual(app._apply_notice_temporary_maintenance(request), 'existing')
        app.schedule_control_events[0]['scheduled_at'] = self.now.replace(hour=15)
        self.assertEqual(app._apply_notice_temporary_maintenance(request), 'conflict')
        app.schedule_control_events = []
        def fail(*_args, **_kwargs):
            app.schedule_control_events.append({'unsaved': True})
            raise OSError('disk full')
        app._add_temporary_maintenance_control_event.side_effect = fail
        with self.assertRaises(OSError):
            app._apply_notice_temporary_maintenance(request)
        self.assertEqual(app.schedule_control_events, [])

    def test_open_input_resolves_notice_but_existing_editor_is_not_overwritten(self):
        for name in ('_focus_existing_schedule_input_window', '_ensure_notice_runtime', '_reset_schedule_input_mode_state',
                     '_reset_schedule_input_ocr_view_filter_defaults', '_set_schedule_base_datetime_fields',
                     '_clear_schedule_input_ocr_results', 'open_schedule_input_window', '_schedule_auto_open_schedule_input_ocr_addon'):
            setattr(self.app, name, Mock())
        self.app.schedule_input_invasion_var = Mock()
        self.app._focus_existing_schedule_input_window.return_value = True
        self.app._open_schedule_input_window_normal()
        self.app._set_schedule_base_datetime_fields.assert_not_called()
        self.app._ensure_notice_runtime.assert_not_called()
        self.app._focus_existing_schedule_input_window.return_value = False
        self.app._open_schedule_input_window_normal()
        self.app._set_schedule_base_datetime_fields.assert_called_once_with(self.now.replace(hour=11))
        self.app._ensure_notice_runtime.assert_called_once()

    def test_global_default_unchanged_and_input_uses_notice(self):
        self.assertEqual(self.app._get_schedule_default_server_open_datetime(self.now).hour, 10)
        self.assertEqual(self.app._get_schedule_input_server_open_datetime().hour, 11)
        self.app.notice_runtime.get_server_open_override.assert_called_with('odin9', self.now, self.now)

    def test_manual_override_including_10am_wins_and_auto_not_saved_as_custom(self):
        self.app._remember_schedule_input_server_open_datetime(self.now.replace(hour=10), self.now)
        self.assertEqual(self.app._get_schedule_input_server_open_datetime().hour, 10)
        self.app._remember_schedule_input_server_open_datetime(self.now.replace(hour=11), self.now)
        self.assertIsNone(self.app.schedule_input_custom_server_open_datetime)
        self.assertEqual(self.app._get_schedule_input_server_open_datetime().hour, 11)

    def test_failures_handover_no_module_and_pre_end_keep_default(self):
        self.app.notice_runtime.get_server_open_override.side_effect = ValueError('bad data')
        self.assertEqual(self.app._get_schedule_input_server_open_datetime().hour, 10)
        self.app.notice_runtime.get_server_open_override.side_effect = None
        self.app.discord_handover_busy = True
        self.assertEqual(self.app._get_schedule_input_server_open_datetime().hour, 10)
        self.app.discord_handover_busy = False
        self.now = self.now.replace(hour=9)
        result = self.app._get_schedule_input_server_open_datetime()
        self.assertEqual(result, datetime(2026, 9, 16, 10))
        self.app.notice_runtime = None
        self.assertEqual(self.app._get_schedule_input_server_open_datetime(), result)

    def test_readonly_plugin_server_scope_and_old_runtime_compatibility(self):
        with tempfile.TemporaryDirectory(prefix='notice-server-open-unit-') as directory:
            host = SimpleNamespace(data_root=Path(directory), get_server=lambda: ('odin9', '오9'))
            plugin = NoticePlugin(host)
            plugin.stopped = False
            store = NoticeStore(host.data_root, 'odin9', clock=lambda: self.now)
            with store._transaction() as state:
                state['articles'] = {'regular': article()}
            before = store.path.read_bytes()
            self.assertIsNotNone(plugin.get_server_open_override('odin9', self.now))
            self.assertEqual(store.path.read_bytes(), before)
            self.assertIsNone(plugin.get_server_open_override('odin8', self.now))
            plugin.stopped = True
            self.assertIsNone(plugin.get_server_open_override('odin9', self.now))
        runtime = NoticeRuntime.__new__(NoticeRuntime)
        runtime.session = SimpleNamespace(plugin=SimpleNamespace())
        self.assertIsNone(runtime.get_server_open_override('odin9', self.now))
        runtime.session = None
        self.assertIsNone(runtime.get_server_open_override('odin9', self.now))


if __name__ == '__main__':
    unittest.main()
