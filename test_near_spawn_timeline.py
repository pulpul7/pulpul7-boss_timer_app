"""Real GUI methods with a virtual clock; no Tk, audio, network or settings I/O."""
import ast
import base64
from datetime import datetime as RealDatetime, timedelta
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


class DateMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, RealDatetime)


class NearSpawnTimelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = Path(__file__).with_name('boss_timer_gui.py').read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        names = {
            '_play_schedule_voice_broker_near_sequence',
            '_play_schedule_voice_broker_request',
            '_build_discord_near_confirmed_spawn_timed_clip_paths',
            '_build_discord_timed_voice_sequence_clip_paths',
            '_get_schedule_alarm_timed_sequence_lead_ms',
            '_play_schedule_alarm_boss_audio_file',
        }
        methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
        cls.code = compile(ast.Module(body=methods, type_ignores=[]), 'boss_timer_gui.py', 'exec')
        cls.host_source = source

    def setUp(self):
        self.start = RealDatetime.fromisoformat('2026-10-01T11:30:59.982630')
        self.now = self.start
        self.stop_at = None
        self.delays = []
        test = self

        class FakeDatetime(metaclass=DateMeta):
            @classmethod
            def now(cls):
                return test.now

        def sleep(seconds):
            self.delays.append(seconds)
            self.now += timedelta(seconds=seconds)
            if self.stop_at and self.now >= self.stop_at:
                self.app.schedule_voice_broker_stop_event.set()

        ns = dict(datetime=FakeDatetime, timedelta=timedelta,
                  time=SimpleNamespace(sleep=sleep, monotonic=lambda: (self.now-self.start).total_seconds()),
                  SCHEDULE_SECOND_PRECISION_NEAR_CHIME_LEAD_MS=3000,
                  base64=base64, json=json)
        exec(self.code, ns)
        self.app = SimpleNamespace()
        for name, value in ns.items():
            if name.startswith('_') and callable(value):
                setattr(self.app, name, value.__get__(self.app))
        app = self.app
        app.schedule_voice_broker_stop_event = threading.Event()
        app.schedule_alarm_boss_audio_request_id = 1
        app._normalize_schedule_voice_lane = lambda value: value or 'center'
        app._schedule_voice_broker_request_is_stale = Mock(return_value=False)
        app._handover_audio_blocked = Mock(return_value=False)
        app._is_schedule_alarm_chime_clip_path = lambda path: path == 'chime'
        app._get_schedule_alarm_voice_duration_ms = lambda path: 800 if path == 'chime' else 500
        app._append_discord_voice_bridge_request = Mock(return_value=False)
        app._should_mute_local_schedule_audio_for_discord_bot = Mock(return_value=False)
        app._set_schedule_voice_lane_busy_until = Mock()
        app._release_schedule_voice_lane_busy_until = Mock()
        app._load_schedule_alarm_boss_audio_paths = Mock()
        app._append_debug_log = Mock()
        app._write_schedule_alarm_voice_test_log = Mock()
        app._get_edge_tts_cached_path = Mock(side_effect=lambda text, **kw: text)
        app._prefetch_edge_tts_text = Mock()
        app._wait_for_edge_tts_audio = Mock(return_value=None)
        self.played = []
        self.real_audio_file_method = app._play_schedule_alarm_boss_audio_file
        app._play_schedule_alarm_boss_audio_file = Mock(
            side_effect=lambda path, **kw: self.played.append((self.now, path)) or True)
        names = ['라타토스크', '라이노르', '야른', '바우티', '흐니르', '파르바']
        times = ['03.382630', '09.328469', '19.288659', '27.273414', '35.431762', '45.426264']
        self.targets = [RealDatetime.fromisoformat('2026-10-01T11:31:' + t) for t in times]
        self.request = dict(phase='SPAWN_CONFIRMED_NEAR_SEQUENCE', lane='center', generation=1,
                            fallback_text='must never play as a single sentence',
                            target_time=self.targets[-1], recording_preferred=True,
                            spawn_members=[dict(target_time=t, name=n, clip_paths=[n, '젠'])
                                           for t, n in zip(self.targets, names)])

    def run_timeline(self):
        self.app._play_schedule_voice_broker_near_sequence(self.request, ['chime', 'flat audio'])

    def test_reported_six_bosses_wait_for_each_fractional_spawn(self):
        self.run_timeline()
        self.assertEqual([t for t, p in self.played if p == '젠'], self.targets)
        self.assertEqual(sum(p == 'chime' for _, p in self.played), 1)
        self.assertNotIn('flat audio', [p for _, p in self.played])
        self.assertGreater((self.now-self.start).total_seconds(), 45)
        self.assertLessEqual(max(self.delays), .02)
        self.app._release_schedule_voice_lane_busy_until.assert_called_once()
        for call in self.app._play_schedule_alarm_boss_audio_file.call_args_list:
            self.assertFalse(call.kwargs['wait_for_finish'])

    def test_tts_only_keeps_individual_gen_times(self):
        self.request['recording_preferred'] = False
        self.run_timeline()
        self.assertEqual([t for t, p in self.played if p == '젠'], self.targets)
        self.assertEqual(self.app._get_edge_tts_cached_path.call_count, 12)

    def test_unknown_and_long_durations_use_same_lead_and_spacing(self):
        for duration in (None, 0, -1, 1200):
            with self.subTest(duration=duration):
                self.now = self.start
                self.played.clear()
                self.app._get_schedule_alarm_voice_duration_ms = lambda path: duration
                self.run_timeline()
                self.assertEqual([t for t, p in self.played if p == '젠'], self.targets)

    def test_missing_audio_does_not_flatten_fallback(self):
        self.request['recording_preferred'] = False
        self.app._get_edge_tts_cached_path.return_value = None
        self.app._get_edge_tts_cached_path.side_effect = None
        self.run_timeline()
        self.assertEqual(self.played, [])
        self.app._append_discord_voice_bridge_request.assert_not_called()

    def test_stop_cancels_remaining_bosses_and_releases_lane(self):
        self.stop_at = self.targets[1] + timedelta(seconds=1)
        self.run_timeline()
        self.assertEqual([t for t, p in self.played if p == '젠'], self.targets[:2])
        self.app._release_schedule_voice_lane_busy_until.assert_called_once()

    def test_season_change_cancels_remaining_bosses(self):
        self.app._schedule_voice_broker_request_is_stale.side_effect = lambda *a: self.now > self.targets[0]
        self.run_timeline()
        self.assertEqual([t for t, p in self.played if p == '젠'], self.targets[:1])

    def test_handover_blocks_all_dispatch(self):
        self.app._handover_audio_blocked.return_value = True
        self.run_timeline()
        self.assertEqual(self.played, [])
        self.app._append_discord_voice_bridge_request.assert_not_called()

    def test_discord_muted_reserves_entire_timeline_not_twenty_seconds(self):
        self.app._append_discord_voice_bridge_request.return_value = True
        self.app._should_mute_local_schedule_audio_for_discord_bot.return_value = True
        self.run_timeline()
        self.assertEqual(self.played, [])
        self.app._append_discord_voice_bridge_request.assert_called_once()
        call = self.app._append_discord_voice_bridge_request.call_args
        self.assertEqual([t for t, p in call.kwargs['timed_clip_paths'] if p == '젠'], self.targets)
        self.assertGreater(self.app._set_schedule_voice_lane_busy_until.call_args.args[1], 45)
        self.assertGreater(self.now, self.targets[-1])

    def test_local_only_and_suppressed_chime(self):
        self.request.update(allow_discord_bridge=False, suppress_chime=True)
        self.run_timeline()
        self.app._append_discord_voice_bridge_request.assert_not_called()
        self.assertNotIn('chime', [p for _, p in self.played])
        self.assertEqual([t for t, p in self.played if p == '젠'], self.targets)

    def test_expired_member_is_not_replayed(self):
        self.now = self.targets[1] + timedelta(seconds=4)
        self.run_timeline()
        self.assertEqual([t for t, p in self.played if p == '젠'], self.targets[2:])

    def test_broker_routes_to_timeline_before_flat_fallback(self):
        app = self.app
        app._get_schedule_voice_lane_lock = lambda lane: threading.Lock()
        app._refresh_schedule_voice_request_countdown_block = lambda request: None
        app._get_schedule_voice_lane_busy_until = lambda lane: 0
        app._play_schedule_voice_broker_near_sequence = Mock()
        app._resolve_edge_tts_fallback_audio = Mock(side_effect=AssertionError('flat fallback'))
        app._play_schedule_voice_broker_request(self.request)
        app._play_schedule_voice_broker_near_sequence.assert_called_once()
        app._resolve_edge_tts_fallback_audio.assert_not_called()

    def test_host_command_is_nonblocking_only_for_timed_dispatch(self):
        stream = io.StringIO()
        self.app._ensure_schedule_alarm_boss_audio_host_process = lambda: SimpleNamespace(stdin=stream)
        self.app.schedule_alarm_boss_audio_host_io_lock = threading.Lock()
        self.real_audio_file_method('젠', request_id=1, expires_at=None, wait_for_finish=False)
        self.real_audio_file_method('젠', request_id=1, expires_at=None)
        commands = stream.getvalue().splitlines()
        self.assertTrue(commands[0].startswith('__PLAYNOW__|'))
        self.assertTrue(commands[1].startswith('__PLAY__|'))
        self.assertIn('[void](Play-Clip $clipPath $false 0 0)', self.host_source)


if __name__ == '__main__':
    unittest.main()
