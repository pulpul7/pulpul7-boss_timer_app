"""Check warning deadlines without importing/running the GUI or Discord bot.

Only the selected source definitions are loaded. No audio, network requests,
Tk windows or user settings are accessed.
"""
import ast
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch


@lru_cache(maxsize=None)
def source_tree(filename):
    return ast.parse(Path(__file__).with_name(filename).read_text(encoding="utf-8-sig"))


def load_definitions(filename, class_name, method_names, globals_dict, module_names=()):
    tree = source_tree(filename)
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    selected = [node for node in cls.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name in method_names]
    if class_name == "VoiceBridgeJob":
        selected = cls.body
    isolated_cls = ast.ClassDef(name=class_name, bases=[], keywords=[], body=selected,
                               decorator_list=cls.decorator_list)
    module_nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in module_names]
    module = ast.fix_missing_locations(ast.Module(body=module_nodes + [isolated_cls], type_ignores=[]))
    exec(compile(module, filename, "exec"), globals_dict)
    return globals_dict[class_name]


class WarningDeadlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gui_globals = {"datetime": datetime, "timedelta": timedelta,
                       "SCHEDULE_FIXED_BOSS_SPECIAL_ALERT_SECONDS": 60}
        cls.GuiPolicy = load_definitions("boss_timer_gui.py", "BossTimerApp", {
            "_get_schedule_voice_request_expire_seconds", "_get_schedule_voice_request_expire_seconds_for_request",
            "_get_schedule_voice_request_deadline", "_schedule_voice_broker_request_is_stale",
            "_refresh_schedule_voice_request_countdown_block", "_build_schedule_voice_broker_pre_alert_sequence",
        }, gui_globals)
        cls.BridgeJob = load_definitions("boss_timer_discord_bot.py", "VoiceBridgeJob", (), {
            "datetime": datetime, "dataclass": dataclass, "Any": Any,
        }, module_names=("parse_datetime",))

    def setUp(self):
        self.gui = self.GuiPolicy()
        self.gui.schedule_voice_broker_generation = 7
        self.gui._normalize_schedule_voice_lane = lambda lane: lane or "center"
        self.gui._is_schedule_alarm_chime_clip_path = lambda path: path == "chime.wav"
        self.target = datetime(2026, 10, 6, 23, 32, 18, 790291)

    def request(self, **extra):
        return {"phase": "PRE_ALERT", "target_time": self.target, "offset_sec": 60,
                "generation": 7, "lane": "center", **extra}

    def job(self, **extra):
        values = dict(id="warning", created_at=self.target, phase="PRE_ALERT_SEQUENCE",
                      category="general", lane="center", volume=1.0, clip_paths=(),
                      target_time=self.target.isoformat(), offset_sec=60)
        values.update(extra)
        return self.BridgeJob(**values)

    def test_logged_bauti_and_parba_warnings_are_expired(self):
        for target, spoken in (("23:32:18.790291", "23:33:47.205"),
                               ("23:32:54.790291", "23:33:42.814")):
            target = datetime.fromisoformat("2026-10-06T" + target)
            spoken = datetime.fromisoformat("2026-10-06T" + spoken)
            request = self.request(target_time=target, earliest_play_at=spoken)
            self.assertTrue(self.gui._schedule_voice_broker_request_is_stale(request, spoken))
            self.assertTrue(self.job(target_time=target.isoformat()).is_expired(spoken))

    def test_initial_delay_is_allowed_but_new_countdowns_cannot_renew_it(self):
        first_release = self.target - timedelta(seconds=50)
        request = self.request(earliest_play_at=first_release)
        request["expires_at"] = self.gui._get_schedule_voice_request_deadline(request)
        self.assertFalse(self.gui._schedule_voice_broker_request_is_stale(request, first_release))
        self.gui.schedule_voice_broker_countdown_block_until = self.target + timedelta(seconds=80)
        self.gui._refresh_schedule_voice_request_countdown_block(request)
        self.assertEqual(request["expires_at"], self.target - timedelta(seconds=30))
        self.assertTrue(self.gui._schedule_voice_broker_request_is_stale(request, request["expires_at"]))

    def test_spawn_is_an_absolute_cutoff_even_with_future_expiry(self):
        for phase in ("PRE_ALERT", "FIXED_PRE_ALERT"):
            request = self.request(phase=phase, expires_at=self.target + timedelta(seconds=120))
            self.assertTrue(self.gui._schedule_voice_broker_request_is_stale(request, self.target))
            self.assertTrue(self.job(phase=phase + "_SEQUENCE",
                                     expires_at=request["expires_at"].isoformat()).is_expired(self.target))

    def test_logged_normal_warning_and_fourth_floor_five_minutes_are_kept(self):
        for target, spoken, offset in (("23:33:21.778043", "23:32:19.335", 60),
                                       ("23:39:00", "23:33:58.332", 300)):
            target = datetime.fromisoformat("2026-10-06T" + target)
            spoken = datetime.fromisoformat("2026-10-06T" + spoken)
            request = self.request(target_time=target, offset_sec=offset)
            self.assertFalse(self.gui._schedule_voice_broker_request_is_stale(request, spoken))
            self.assertFalse(self.job(target_time=target.isoformat(), offset_sec=offset).is_expired(spoken))

    def test_merge_keeps_earliest_deadline_even_if_first_boss_is_later(self):
        requests = [self.request(target_time=self.target + timedelta(seconds=10)), self.request()]
        for request in requests:
            request["expires_at"] = self.gui._get_schedule_voice_request_deadline(request)
        merged = self.gui._build_schedule_voice_broker_pre_alert_sequence(requests)
        self.assertEqual(merged["expires_at"], requests[1]["expires_at"])

    def test_countdown_and_spawn_sequences_can_cross_spawn(self):
        for phase in ("COUNTDOWN_SEQUENCE", "SPAWN_CONFIRMED_SEQUENCE", "SPAWN_CONFIRMED_NEAR_SEQUENCE"):
            job = self.job(phase=phase, expires_at=self.target.isoformat())
            self.assertIsNone(job.playback_deadline)
            self.assertFalse(job.is_expired(self.target + timedelta(seconds=10)))

    def test_bridge_explicit_deadline_and_legacy_missing_deadline(self):
        cutoff = self.target - timedelta(seconds=20)
        self.assertTrue(self.job(expires_at=cutoff.isoformat()).is_expired(cutoff))
        self.assertFalse(self.job().is_expired(cutoff))
        self.assertTrue(self.job().is_expired(self.target))

    def test_generation_change_still_cancels_request(self):
        request = self.request(generation=6)
        self.assertTrue(self.gui._schedule_voice_broker_request_is_stale(request, self.target - timedelta(seconds=60)))


class ClipDeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_expiration_while_waiting_for_voice_does_not_start_audio(self):
        now = datetime(2026, 10, 6, 23, 31, 20)
        clock = SimpleNamespace(now=lambda: now)
        globals_dict = {"datetime": datetime, "VOICE_CLIP_GAP_SEC": 0, "asyncio": asyncio,
                        "Any": Any}
        Bot = load_definitions("boss_timer_discord_bot.py", "DiscordScheduleBot", {"_play_clip"}, globals_dict)
        globals_dict["datetime"] = clock
        bot = Bot()
        bot.voice_client = SimpleNamespace(is_playing=lambda: True, is_paused=lambda: False, play=Mock())
        bot.voice_play_lock = asyncio.Lock()
        bot.current_voice_playback_token = None
        bot.current_timed_composite_source = None
        bot._is_voice_bridge_scope_cancelled = lambda scope: False
        bot._prepare_playback_source = AsyncMock()
        async def advance_clock(delay):
            nonlocal now
            now += timedelta(seconds=1)
        with patch.object(asyncio, "sleep", advance_clock):
            self.assertFalse(await bot._play_clip("minute.wav", expires_at=now + timedelta(seconds=1)))
        bot.voice_client.play.assert_not_called()
        bot._prepare_playback_source.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
