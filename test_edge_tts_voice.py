import asyncio
import base64
import io
import inspect
import json
import os
import queue
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import boss_timer_gui
from boss_timer_discord_bot import (
    BUILTIN_DISCORD_VOICE_COMMANDS,
    DISCORD_COUNTDOWN_COMPOSITE_WARMUP_SEC,
    DiscordScheduleBot,
    TIMED_REPLACE_CURRENT_AUDIO_PHASES,
    VoiceBridgeControl,
    VoiceBridgeJob,
    VoiceBridgeReader,
    compact_alert_names,
    load_custom_discord_voice_commands,
    load_disabled_builtin_discord_voice_commands,
    normalize_discord_voice_command_name,
    parse_discord_schedule_message,
    save_custom_discord_voice_commands,
)
from boss_timer_gui import BossTimerApp
from edge_tts_voice import (
    EdgeTtsCache,
    EdgeTtsSettings,
    load_edge_tts_settings,
    save_edge_tts_settings,
)
from edge_tts_module import get_edge_tts_module_status, install_edge_tts_module


class _FakeCommunicate:
    call_count = 0
    last_options = {}

    def __init__(self, text, voice, **options):
        type(self).call_count += 1
        type(self).last_options = {"text": text, "voice": voice, **options}

    def save_sync(self, path):
        Path(path).write_bytes(b"ID3" + (b"\x00" * 64))


class _FallbackCommunicate:
    voices = []

    def __init__(self, text, voice, **options):
        self.voice = voice
        type(self).voices.append(voice)

    def save_sync(self, path):
        if self.voice != "ko-KR-HyunsuMultilingualNeural":
            raise RuntimeError("NoAudioReceived")
        Path(path).write_bytes(b"ID3" + (b"\x00" * 64))


class EdgeTtsModuleInstallTests(unittest.TestCase):
    def test_installer_replaces_module_only_after_valid_zip_is_downloaded(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module_dir = Path(temp_dir) / "tts_module"
            payload_buffer = io.BytesIO()
            with zipfile.ZipFile(payload_buffer, "w") as archive:
                archive.writestr(
                    "module.json",
                    json.dumps(
                        {
                            "module": "boss_timer_edge_tts",
                            "version": "test",
                            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
                        }
                    ),
                )
                archive.writestr("packages/edge_tts/__init__.py", "__version__ = 'test'\n")
            archive_bytes = payload_buffer.getvalue()

            def fake_urlopen(_request, timeout=0):
                self.assertGreater(timeout, 0)
                return io.BytesIO(archive_bytes)

            status = install_edge_tts_module(
                str(module_dir),
                download_url="https://example.invalid/tts.zip",
                urlopen=fake_urlopen,
            )
            self.assertTrue(status.installed)
            self.assertEqual(status.version, "test")
            self.assertTrue((module_dir / "packages" / "edge_tts" / "__init__.py").is_file())
            self.assertTrue(get_edge_tts_module_status(str(module_dir)).installed)


class DiscordScheduleInputTests(unittest.TestCase):
    def test_near_confirmed_spawn_uses_timed_replacement_path(self):
        self.assertIn("SPAWN_CONFIRMED_NEAR_SEQUENCE", TIMED_REPLACE_CURRENT_AUDIO_PHASES)

    @staticmethod
    def _parser_app(reference_datetime):
        app = BossTimerApp.__new__(BossTimerApp)
        app._get_schedule_reference_datetime = lambda: reference_datetime
        app._normalize_schedule_input_boss_name = lambda name: {
            "raw_key": f"normal:{name}",
            "raw_name": name,
            "boss_name": name,
            "display_name": name,
        }
        return app

    def test_discord_message_recognizes_batch_and_comma_delete(self):
        parsed = parse_discord_schedule_message("1511 티르\n1755 스카디\n151105 토르")
        self.assertEqual(parsed["operation"], "apply")
        self.assertEqual(parsed["line_count"], 3)
        deleted = parse_discord_schedule_message("삭제 티르, 스카디,")
        self.assertEqual(deleted["operation"], "delete")
        self.assertEqual(deleted["boss_names"], ["티르", "스카디"])
        self.assertIsNone(parse_discord_schedule_message("일반 대화입니다"))
        self.assertIsNone(parse_discord_schedule_message("/보탐"))

    def test_discord_message_expands_comma_separated_bosses_at_same_time(self):
        parsed = parse_discord_schedule_message("1800 스카디, 안나")

        self.assertEqual(parsed["operation"], "apply")
        self.assertEqual(parsed["line_count"], 2)
        self.assertEqual(parsed["raw_text"], "1800 스카디\n1800 안나")

        reference = datetime(2026, 9, 2, 17, 0, 0)
        app = self._parser_app(reference)
        parsed_items, ignored = app._parse_schedule_input_lines(
            parsed["raw_text"],
            reference_datetime=reference,
        )
        self.assertEqual(ignored, 0)
        self.assertEqual(
            [(item["clock_hours"], item["clock_minutes"], item["boss_name"]) for item in parsed_items],
            [(18, 0, "스카디"), (18, 0, "안나")],
        )

    def test_discord_comma_expansion_applies_cut_suffix_to_each_boss(self):
        parsed = parse_discord_schedule_message("1511 티르, 스카디 컷")

        self.assertEqual(parsed["raw_text"], "1511 티르 컷\n1511 스카디 컷")

    def test_discord_request_monitor_resets_once_at_eight_am(self):
        app = object.__new__(BossTimerApp)
        app.discord_schedule_monitor_reset_key = app._get_discord_schedule_monitor_reset_key(
            datetime(2026, 9, 2, 7, 59, 59)
        )
        app._clear_discord_schedule_monitor_entries = mock.Mock()

        self.assertTrue(app._reset_discord_schedule_monitor_if_due(datetime(2026, 9, 2, 8, 0, 0)))
        self.assertFalse(app._reset_discord_schedule_monitor_if_due(datetime(2026, 9, 2, 8, 30, 0)))
        app._clear_discord_schedule_monitor_entries.assert_called_once_with()

    def test_discord_request_monitor_manual_clear_only_empties_widget(self):
        app = object.__new__(BossTimerApp)
        monitor = mock.Mock()
        app.discord_schedule_monitor_text = monitor
        app._widget_available = lambda widget: widget is monitor

        app._clear_discord_schedule_monitor_entries()

        self.assertEqual(
            monitor.config.call_args_list,
            [mock.call(state="normal"), mock.call(state="disabled")],
        )
        monitor.delete.assert_called_once_with("1.0", "end")

    def test_extended_clock_is_next_day_and_seconds_are_preserved(self):
        reference = datetime(2026, 9, 2, 15, 30, 0)
        app = self._parser_app(reference)
        parsed, ignored = app._parse_schedule_input_lines(
            "2510 티르\n251005 스카디",
            reference_datetime=reference,
        )
        self.assertEqual(ignored, 0)
        self.assertEqual(
            [
                (item["day_offset"], item["clock_hours"], item["clock_minutes"], item["clock_seconds"])
                for item in parsed
            ],
            [(1, 1, 10, 0), (1, 1, 10, 5)],
        )

    def test_standard_clock_is_future_and_cut_clock_is_past(self):
        reference = datetime(2026, 9, 2, 15, 30, 0)
        app = self._parser_app(reference)
        future, _ignored = app._parse_schedule_input_lines("1511 티르", reference_datetime=reference)
        past_cut, _ignored = app._parse_schedule_input_lines("1511 티르 컷", reference_datetime=reference)
        self.assertEqual(future[0]["day_offset"], 1)
        self.assertEqual(past_cut[0]["day_offset"], 0)
        self.assertTrue(past_cut[0]["cut_applied"])

    def test_discord_delete_removes_only_named_schedule_chains(self):
        app = BossTimerApp.__new__(BossTimerApp)
        app.schedule_events = [
            {"raw_key": "normal:티르", "scheduled_at": datetime(2026, 9, 2, 16, 0)},
            {"raw_key": "normal:티르", "scheduled_at": datetime(2026, 9, 3, 16, 0)},
            {"raw_key": "normal:스카디", "scheduled_at": datetime(2026, 9, 2, 17, 0)},
        ]
        app.schedule_active_entries = [{"raw_key": "normal:티르"}]
        app._normalize_schedule_input_boss_name = lambda name: {
            "raw_key": f"normal:{name}", "boss_name": name, "display_name": name
        }
        app._get_schedule_boss_display_name = lambda item, **_kwargs: item["display_name"]
        app._get_schedule_item_identity = lambda kind, item: (kind, item["raw_key"], str(item.get("scheduled_at")), "")
        app._purge_schedule_cut_state = lambda **_kwargs: None
        app._clear_schedule_second_precision_offsets_for_raw_keys = lambda _keys: 0
        app._reset_schedule_alarm_event_index = lambda: None
        app._save_schedule_state = lambda **_kwargs: None
        app._refresh_schedule_view = lambda: None

        success, summary = app._delete_discord_schedule_bosses(["티르"])

        self.assertTrue(success)
        self.assertIn("3건", summary)
        self.assertEqual([item["raw_key"] for item in app.schedule_events], ["normal:스카디"])
        self.assertEqual(app.schedule_active_entries, [])

    def test_fixed_followup_can_use_fifteen_minute_recording(self):
        app = BossTimerApp.__new__(BossTimerApp)
        app._get_schedule_alarm_voice_files_by_stem_prefix = lambda subdir, prefix: [f"{subdir}/{prefix}01.wav"]

        paths = app._get_schedule_alarm_boss_audio_available_paths(15 * 60)

        self.assertEqual(paths, ["min/15min01.wav"])


class EdgeTtsSettingsTests(unittest.TestCase):
    def test_missing_settings_use_distribution_voice_controls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            loaded = load_edge_tts_settings(os.path.join(temp_dir, "missing.ini"))

        self.assertEqual(loaded.voice, "ko-KR-SunHiNeural")
        self.assertEqual(loaded.rate, 0)
        self.assertEqual(loaded.volume, 0)
        self.assertEqual(loaded.pitch, 15)

    def test_settings_round_trip_and_normalization(self):
        settings = EdgeTtsSettings(
            enabled=True,
            voice="ko-KR-InJoonNeural",
            rate=25,
            volume=-10,
            pitch=15,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "edge_tts.ini")
            save_edge_tts_settings(path, settings)
            loaded = load_edge_tts_settings(path)
        self.assertEqual(loaded, settings)
        self.assertEqual(loaded.rate_value, "+25%")
        self.assertEqual(loaded.volume_value, "-10%")
        self.assertEqual(loaded.pitch_value, "+15Hz")


class EdgeTtsCacheTests(unittest.TestCase):
    def setUp(self):
        _FakeCommunicate.call_count = 0
        self.settings = EdgeTtsSettings(
            enabled=True,
            voice="ko-KR-SunHiNeural",
            rate=10,
            volume=5,
            pitch=-10,
        )

    @mock.patch("edge_tts_voice._edge_tts.Communicate", _FakeCommunicate)
    def test_synthesize_caches_mp3_with_selected_controls(self):
        cache = EdgeTtsCache(self.settings)
        first = cache.synthesize("test boss")
        second = cache.synthesize("test boss")
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertTrue(Path(first).is_file())
        self.assertTrue(cache.owns_path(first))
        self.assertEqual(_FakeCommunicate.call_count, 1)
        self.assertEqual(_FakeCommunicate.last_options["voice"], "ko-KR-SunHiNeural")
        self.assertEqual(_FakeCommunicate.last_options["rate"], "+10%")
        self.assertEqual(_FakeCommunicate.last_options["volume"], "+5%")
        self.assertEqual(_FakeCommunicate.last_options["pitch"], "-10Hz")
        cache.stop()
        self.assertFalse(Path(first).exists())

    @mock.patch("edge_tts_voice._edge_tts.Communicate", _FakeCommunicate)
    def test_prefetch_deduplicates_requests(self):
        cache = EdgeTtsCache(self.settings)
        self.assertTrue(cache.prefetch("custom event"))
        self.assertTrue(cache.prefetch("custom event"))
        deadline = time.monotonic() + 2.0
        cached_path = None
        while time.monotonic() < deadline:
            cached_path = cache.get("custom event")
            if cached_path:
                break
            time.sleep(0.01)
        self.assertIsNotNone(cached_path)
        self.assertEqual(_FakeCommunicate.call_count, 1)
        cache.stop()

    @mock.patch("edge_tts_voice._edge_tts.Communicate", _FakeCommunicate)
    def test_wait_returns_prefetched_edge_tts(self):
        cache = EdgeTtsCache(self.settings)
        self.assertTrue(cache.prefetch("wait for edge"))
        cached_path = cache.wait("wait for edge", timeout=2.0)
        self.assertIsNotNone(cached_path)
        self.assertTrue(Path(cached_path).is_file())
        self.assertEqual(_FakeCommunicate.call_count, 1)
        cache.stop()

    @mock.patch("edge_tts_voice._edge_tts.Communicate", _FakeCommunicate)
    def test_rate_steps_use_a_distinct_faster_cache_entry(self):
        cache = EdgeTtsCache(self.settings)
        normal_path = cache.synthesize("십오")
        fast_path = cache.synthesize("십오", rate_steps=3)
        self.assertIsNotNone(normal_path)
        self.assertIsNotNone(fast_path)
        self.assertNotEqual(normal_path, fast_path)
        self.assertEqual(_FakeCommunicate.call_count, 2)
        self.assertEqual(_FakeCommunicate.last_options["rate"], "+40%")
        cache.stop()

    @mock.patch("edge_tts_voice._edge_tts.Communicate", _FakeCommunicate)
    def test_volume_steps_create_a_distinct_louder_cache_entry(self):
        cache = EdgeTtsCache(self.settings)
        normal_path = cache.synthesize("십", rate_steps=3)
        louder_path = cache.synthesize("십", rate_steps=3, volume_steps=20)

        self.assertIsNotNone(normal_path)
        self.assertIsNotNone(louder_path)
        self.assertNotEqual(normal_path, louder_path)
        self.assertEqual(_FakeCommunicate.call_count, 2)
        self.assertEqual(_FakeCommunicate.last_options["rate"], "+40%")
        self.assertEqual(_FakeCommunicate.last_options["volume"], "+25%")
        cache.stop()

    @mock.patch("edge_tts_voice._edge_tts.Communicate", _FakeCommunicate)
    def test_persistent_sec_cache_survives_restart_and_checks_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first_cache = EdgeTtsCache(self.settings, persistent_dir=temp_dir)
            first_path = first_cache.synthesize(
                "십오",
                rate_steps=3,
                persistent_relpath="sec/15.mp3",
            )
            self.assertIsNotNone(first_path)
            first_cache.stop()
            self.assertTrue(Path(first_path).is_file())

            restarted_cache = EdgeTtsCache(self.settings, persistent_dir=temp_dir)
            self.assertEqual(
                restarted_cache.get("십오", rate_steps=3),
                first_path,
            )
            self.assertEqual(_FakeCommunicate.call_count, 1)
            restarted_cache.stop()

            changed_settings = EdgeTtsSettings(
                enabled=True,
                voice=self.settings.voice,
                rate=self.settings.rate + 5,
                volume=self.settings.volume,
                pitch=self.settings.pitch,
            )
            changed_cache = EdgeTtsCache(changed_settings, persistent_dir=temp_dir)
            self.assertIsNone(changed_cache.get("십오", rate_steps=3))
            self.assertFalse(Path(first_path).exists())
            self.assertGreaterEqual(changed_cache.startup_pruned_count, 1)
            changed_cache.stop()

    @mock.patch("edge_tts_voice._edge_tts.Communicate", _FakeCommunicate)
    def test_manifest_replace_retries_transient_windows_permission_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = EdgeTtsCache(self.settings, persistent_dir=temp_dir)
            real_replace = os.replace
            denied_attempts = 0

            def replace_with_transient_lock(source, target):
                nonlocal denied_attempts
                if str(target).endswith("manifest.json") and denied_attempts < 2:
                    denied_attempts += 1
                    raise PermissionError(13, "transient manifest lock", str(target))
                return real_replace(source, target)

            with mock.patch("edge_tts_voice.os.replace", side_effect=replace_with_transient_lock):
                cached_path = cache.synthesize(
                    "manifest retry",
                    persistent_relpath="message/manifest-retry.mp3",
                )

            self.assertIsNotNone(cached_path)
            self.assertEqual(denied_attempts, 2)
            manifest_payload = json.loads((Path(temp_dir) / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(manifest_payload.get("entries"))
            cache.stop()

    @mock.patch("edge_tts_voice._edge_tts.Communicate", _FakeCommunicate)
    def test_settings_change_deletes_previous_persistent_cache(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = EdgeTtsCache(self.settings, persistent_dir=temp_dir)
            cached_path = cache.synthesize(
                "persistent message",
                persistent_relpath="message/persistent.mp3",
            )
            self.assertIsNotNone(cached_path)
            self.assertTrue(Path(cached_path).is_file())

            changed_settings = EdgeTtsSettings(
                enabled=True,
                voice=self.settings.voice,
                rate=self.settings.rate,
                volume=self.settings.volume + 5,
                pitch=self.settings.pitch,
            )
            removed_count = cache.update_settings(changed_settings)

            self.assertGreaterEqual(removed_count, 1)
            self.assertFalse(Path(cached_path).exists())
            self.assertTrue(Path(temp_dir).is_dir())
            cache.stop()

    def test_legacy_voice_fallback_cache_is_removed_on_startup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy_path = Path(temp_dir) / "message" / "legacy.mp3"
            legacy_path.parent.mkdir(parents=True)
            legacy_path.write_bytes(b"ID3" + (b"\x00" * 64))
            manifest_path = Path(temp_dir) / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "entries": {
                            "legacy-key": {
                                "path": "message/legacy.mp3",
                                "text": "legacy voice",
                                "rate_steps": 0,
                                "settings": "ko-KR-SunHiNeural|+10%|+5%|-10Hz",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            cache = EdgeTtsCache(self.settings, persistent_dir=temp_dir)

            self.assertFalse(legacy_path.exists())
            self.assertGreaterEqual(cache.startup_pruned_count, 1)
            cache.stop()

    @mock.patch("edge_tts_voice._edge_tts.Communicate", _FallbackCommunicate)
    def test_failed_selected_voice_never_switches_to_a_different_gender(self):
        _FallbackCommunicate.voices = []
        cache = EdgeTtsCache(self.settings)
        cached_path = cache.wait("edge voice fallback", timeout=2.0)
        self.assertIsNone(cached_path)
        self.assertEqual(_FallbackCommunicate.voices, ["ko-KR-SunHiNeural"])
        self.assertEqual(cache.last_voice, "")
        cache.stop()


class BossTimerEdgeTtsPriorityTests(unittest.TestCase):
    def test_server_profile_paths_are_isolated_under_appdata(self):
        app = object.__new__(BossTimerApp)
        app.schedule_server_profile_id = "odin-9"
        app.schedule_server_profile_name = "오딘9"
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("boss_timer_gui.get_user_config_dir", return_value=temp_dir):
                self.assertEqual(
                    app._get_schedule_state_storage_path(),
                    os.path.join(temp_dir, "server_profiles", "season_unset", "odin-9", "schedule_state.json"),
                )
                self.assertEqual(
                    app._get_schedule_alarm_settings_storage_path(),
                    os.path.join(temp_dir, "server_profiles", "season_unset", "odin-9", "schedule_alarm_settings.json"),
                )
                self.assertEqual(
                    app._get_schedule_boss_definitions_storage_path(),
                    os.path.join(temp_dir, "server_profiles", "season_unset", "odin-9", "init", "schedule_boss_definitions.txt"),
                )

    def test_cache_generation_partition_keeps_ready_and_only_returns_missing_once(self):
        app = object.__new__(BossTimerApp)
        app.edge_tts_cache = mock.Mock()
        app.edge_tts_cache.get.side_effect = lambda text, **_kwargs: (
            "ready.mp3" if text == "준비 문장" else None
        )
        jobs = [
            {"text": "준비 문장", "rate": 1, "volume_steps": 0},
            {"text": "신규 문장", "rate": 1, "volume_steps": 5},
            {"text": "신규 문장", "rate": 1, "volume_steps": 5},
        ]

        ready_jobs, missing_jobs = app._partition_edge_tts_cache_jobs(jobs)

        self.assertEqual([job["text"] for job in ready_jobs], ["준비 문장"])
        self.assertEqual([job["text"] for job in missing_jobs], ["신규 문장"])
        self.assertEqual(app.edge_tts_cache.get.call_count, 2)
        app.edge_tts_cache.get.assert_any_call("신규 문장", rate_steps=1, volume_steps=5)

    def test_existing_voice_file_wins_over_edge_tts(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_alarm_boss_voice_name_candidates = lambda **_kwargs: ["custom boss"]
        app._get_schedule_alarm_voice_file_by_stem = lambda subdir, _stem: "local.wav" if subdir == "boss" else None
        app._get_edge_tts_cached_path = lambda _text: "edge.mp3"
        app._prefetch_edge_tts_text = lambda _text: self.fail("local file should prevent edge-tts prefetch")
        self.assertEqual(app._get_schedule_alarm_boss_voice_path(boss_name="custom boss"), "local.wav")

    def test_rapid_chain_voice_test_does_not_enable_regular_610_second_alert(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_reference_datetime = lambda: datetime(2026, 8, 31, 18, 0, 0)
        app._get_schedule_alarm_voice_rule_rows = lambda: [(f"row {index}",) for index in range(26)]
        app._get_schedule_alarm_voice_test_general_boss_pool = lambda: ["바우티", "토르", "10층"]
        app._get_schedule_alarm_voice_test_fixed_boss_pool = lambda: []
        app._build_schedule_alarm_voice_test_event = lambda boss_name, scheduled_at, **kwargs: {
            "boss_name": boss_name,
            "scheduled_at": scheduled_at,
            "precision": kwargs.get("precision", "minute"),
        }
        app._normalize_schedule_alarm_offsets = lambda values: sorted({int(value) for value in values}, reverse=True)
        app._normalize_schedule_event_items = lambda values: list(values)

        cases = BossTimerApp._build_schedule_alarm_voice_test_cases(
            app,
            delay_seconds=1,
            selected_rule_index=17,
        )

        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]["normal_offsets"], [60])
        self.assertNotIn(610, cases[0]["normal_offsets"])

    def test_spawn_voice_tests_are_scheduled_six_seconds_after_start(self):
        app = object.__new__(BossTimerApp)
        now = datetime(2026, 8, 31, 18, 0, 0)
        app._get_schedule_reference_datetime = lambda: now
        app._get_schedule_alarm_voice_rule_rows = lambda: [(f"row {index}",) for index in range(22)]
        app._get_schedule_alarm_voice_test_general_boss_pool = lambda: ["테스트보스"]
        app._get_schedule_alarm_voice_test_fixed_boss_pool = lambda: []
        app._build_schedule_alarm_voice_test_event = lambda boss_name, scheduled_at, **kwargs: {
            "boss_name": boss_name,
            "scheduled_at": scheduled_at,
            "precision": kwargs.get("precision", "minute"),
        }
        app._normalize_schedule_alarm_offsets = lambda values: sorted({int(value) for value in values}, reverse=True)
        app._normalize_schedule_event_items = lambda values: list(values)

        for rule_index in (0, 1):
            cases = BossTimerApp._build_schedule_alarm_voice_test_cases(
                app,
                delay_seconds=1,
                selected_rule_index=rule_index,
            )
            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["target_at"], now + timedelta(seconds=6))

    def test_complex_general_and_invasion_voice_tests_keep_requested_schedule_and_precision(self):
        app = object.__new__(BossTimerApp)
        now = datetime(2026, 8, 31, 18, 0, 0)
        app._get_schedule_reference_datetime = lambda: now
        app._get_schedule_alarm_voice_rule_rows = lambda: [(f"row {index}",) for index in range(26)]
        app._get_schedule_alarm_voice_test_general_boss_pool = lambda: ["테스트보스"]
        app._get_schedule_alarm_voice_test_fixed_boss_pool = lambda: []
        app._build_schedule_alarm_voice_test_event = lambda boss_name, scheduled_at, **kwargs: {
            "boss_name": boss_name,
            "scheduled_at": scheduled_at,
            "precision": kwargs.get("precision", "minute"),
            "is_invasion": kwargs.get("invasion", False),
        }
        app._normalize_schedule_alarm_offsets = lambda values: sorted({int(value) for value in values}, reverse=True)
        app._normalize_schedule_event_items = lambda values: list(values)
        expected_rows = [
            ("라이노르", 0, False),
            ("브륀힐드", 0, False),
            ("니드호그", 60, False),
            ("셀로비아", 60, False),
            ("라타토스크", 60, True),
            ("비요른", 60, True),
            ("헤르모드", 60, True),
            ("페티", 360, False),
            ("라이노르", 420, True),
        ]

        for rule_index, precision in ((9, "minute"), (10, "second")):
            cases = BossTimerApp._build_schedule_alarm_voice_test_cases(
                app,
                delay_seconds=1,
                selected_rule_index=rule_index,
            )

            self.assertEqual(len(cases), 1)
            self.assertEqual(cases[0]["duration_seconds"], 440)
            self.assertEqual(
                [
                    (
                        event["boss_name"],
                        int((event["scheduled_at"] - cases[0]["target_at"]).total_seconds()),
                        event["is_invasion"],
                    )
                    for event in cases[0]["events"]
                ],
                expected_rows,
            )
            self.assertTrue(all(event["precision"] == precision for event in cases[0]["events"]))

    def test_invasion_pre_alert_stays_in_center_queue_with_normal_spawn(self):
        app = object.__new__(BossTimerApp)
        app.schedule_voice_broker_generation = 0
        app._schedule_voice_broker_request_is_stale = lambda _request, _now: False
        now = datetime.now().replace(microsecond=0)
        requests = [
            {
                "phase": "SPAWN_CONFIRMED",
                "boss_id": "일반",
                "target_time": now,
                "offset_sec": 0,
                "lane": "center",
                "volume": 1.0,
                "is_invasion": False,
                "dedupe_key": "normal-spawn",
            },
            {
                "phase": "PRE_ALERT",
                "boss_id": "침공",
                "target_time": now + timedelta(minutes=5),
                "offset_sec": 300,
                "lane": "center",
                "volume": 1.0,
                "is_invasion": True,
                "dedupe_key": "invasion-pre-alert",
            },
        ]

        prepared = BossTimerApp._schedule_voice_broker_prepare_requests(app, requests)

        self.assertEqual([request["phase"] for request in prepared], ["SPAWN_CONFIRMED", "PRE_ALERT"])
        self.assertEqual([request["lane"] for request in prepared], ["center", "center"])

    def test_pre_alert_merge_has_no_four_item_limit(self):
        app = object.__new__(BossTimerApp)
        app.schedule_voice_broker_generation = 0
        app._schedule_voice_broker_request_is_stale = lambda _request, _now: False
        merged_group_sizes = []
        now = datetime.now().replace(microsecond=0)

        def merge_group(group):
            merged_group_sizes.append(len(group))
            return {
                "phase": "PRE_ALERT",
                "target_time": group[0]["target_time"],
                "offset_sec": group[0]["offset_sec"],
                "lane": "center",
                "dedupe_key": "merged",
            }

        app._build_schedule_voice_broker_merged_pre_alert = merge_group
        app._build_schedule_voice_broker_pre_alert_sequence = lambda requests: requests[0]
        requests = [
            {
                "phase": "PRE_ALERT",
                "boss_id": f"보스{index}",
                "target_time": now,
                "offset_sec": 300,
                "lane": "center",
                "volume": 1.0,
                "is_invasion": index % 2 == 0,
                "merge_items": [{"boss_name": f"보스{index}"}],
                "dedupe_key": f"pre-alert-{index}",
            }
            for index in range(5)
        ]

        BossTimerApp._schedule_voice_broker_prepare_requests(app, requests)

        self.assertEqual(merged_group_sizes, [5])

    def test_pre_alerts_for_adjacent_spawn_times_are_not_merged(self):
        app = object.__new__(BossTimerApp)
        app.schedule_voice_broker_generation = 0
        app._schedule_voice_broker_request_is_stale = lambda _request, _now: False
        merged_group_sizes = []
        now = datetime.now().replace(microsecond=0)
        app._build_schedule_voice_broker_merged_pre_alert = (
            lambda group: merged_group_sizes.append(len(group)) or group[0]
        )
        app._build_schedule_voice_broker_pre_alert_sequence = lambda requests: requests[0]
        requests = [
            {
                "phase": "PRE_ALERT",
                "boss_id": f"보스{index}",
                "target_time": now + timedelta(seconds=index * 60),
                "offset_sec": 60,
                "lane": "center",
                "volume": 1.0,
                "is_invasion": False,
                "merge_items": [{"boss_name": f"보스{index}"}],
                "dedupe_key": f"pre-alert-{index}",
            }
            for index in range(2)
        ]

        BossTimerApp._schedule_voice_broker_prepare_requests(app, requests)

        self.assertEqual(merged_group_sizes, [])

    def test_pre_alert_membership_merges_spawns_within_twenty_five_seconds(self):
        app = object.__new__(BossTimerApp)
        base_at = datetime(2026, 9, 7, 18, 0, 0)
        app._get_schedule_alarm_event_identity = lambda item: item["boss_name"]
        app._get_schedule_boss_display_name = lambda item, **_kwargs: item["boss_name"]
        near_first = {"boss_name": "니드호그", "scheduled_at": base_at}
        near_second = {"boss_name": "라타토스크", "scheduled_at": base_at + timedelta(seconds=1)}
        next_group = {"boss_name": "페티", "scheduled_at": base_at + timedelta(seconds=60)}
        membership = BossTimerApp._build_schedule_alarm_same_time_membership_by_offset(
            app,
            [
                {"item": near_first, "offsets": [60, 300]},
                {"item": near_second, "offsets": [60, 300]},
                {"item": next_group, "offsets": [60, 300]},
            ],
        )

        self.assertEqual(
            [item["boss_name"] for item in membership[60]["니드호그"]],
            ["니드호그", "라타토스크"],
        )
        self.assertNotIn("페티", membership[60])

    def test_merged_group_pre_alert_uses_compact_tts_segments(self):
        app = object.__new__(BossTimerApp)
        app._build_schedule_alarm_custom_audio_paths = mock.Mock(return_value=["boss.wav", "extra.wav"])
        app._with_schedule_alarm_chime_paths = lambda paths, _key: ["chime.wav", *paths]
        app._get_schedule_alarm_compact_group_context = lambda _items: (None, "니드호그 외 4개", 4, False)
        app._format_schedule_alarm_remaining_speech = lambda _seconds: "5분"
        app._build_schedule_alarm_event_group_identity = lambda _items: "group-id"
        requests = [
            {
                "offset_sec": 300,
                "merge_items": [{"boss_name": "니드호그"}, {"boss_name": "셀로비아"}],
                "chime_key": "general",
                "volume": 1.0,
                "is_invasion": False,
            }
        ]

        merged = BossTimerApp._build_schedule_voice_broker_merged_pre_alert(app, requests)

        self.assertEqual(merged["fallback_text"], "니드호그 외 4개 5분 남았습니다.")
        self.assertEqual(merged["tts_segments"], ["니드호그 외 4개", "5분 남았습니다."])

    def test_cached_edge_tts_is_used_when_only_chime_exists(self):
        app = object.__new__(BossTimerApp)
        app._is_schedule_alarm_chime_clip_path = lambda path: path == "chime.wav"
        app._get_edge_tts_cached_path = lambda _text, **_kwargs: "edge.mp3"
        app._prefetch_edge_tts_text = lambda _text, **_kwargs: self.fail("ready cache should not prefetch")
        paths, ready = app._resolve_edge_tts_fallback_audio(["chime.wav"], "custom event alert")
        self.assertEqual(paths, ["chime.wav", "edge.mp3"])
        self.assertTrue(ready)

    def test_edge_tts_is_prefetched_when_not_ready(self):
        app = object.__new__(BossTimerApp)
        queued = []
        app._is_schedule_alarm_chime_clip_path = lambda _path: True
        app._get_edge_tts_cached_path = lambda _text, **_kwargs: None
        app._prefetch_edge_tts_text = lambda text, **_kwargs: queued.append(text) or True
        paths, ready = app._resolve_edge_tts_fallback_audio([], "fixed boss alert")
        self.assertEqual(paths, [])
        self.assertFalse(ready)
        self.assertEqual(queued, ["fixed boss alert"])

    def test_edge_tts_segments_are_joined_without_an_artificial_gap(self):
        app = object.__new__(BossTimerApp)
        app._is_schedule_alarm_chime_clip_path = lambda path: path == "chime.wav"
        app._get_edge_tts_cached_path = lambda text, **_kwargs: f"{text}.mp3"

        paths, ready = app._resolve_edge_tts_segment_audio(
            ["chime.wav"],
            ["first boss", "second boss", "1 minute before"],
            rate=1,
        )

        self.assertTrue(ready)
        self.assertEqual(
            paths,
            [
                "chime.wav",
                "first boss.mp3",
                "second boss.mp3",
                "1 minute before.mp3",
            ],
        )

    def test_alarm_tts_worker_plays_edge_audio_without_ms_tts(self):
        app = object.__new__(BossTimerApp)
        app.schedule_alarm_tts_stop_event = threading.Event()
        app.schedule_alarm_tts_queue = queue.Queue()
        app.schedule_alarm_boss_audio_request_id = 7
        played = []
        app._wait_for_edge_tts_audio = lambda text, timeout=45.0, rate=0: "edge.mp3"
        app._load_schedule_alarm_boss_audio_paths = lambda _paths: True
        app._play_schedule_alarm_boss_audio_paths = lambda paths, **_kwargs: played.append(paths) or True
        app._get_schedule_alarm_clip_sequence_duration_ms = lambda _paths, fallback_ms=1800: fallback_ms
        app._write_schedule_alarm_voice_test_log = lambda *_args, **_kwargs: None
        app._speak_schedule_alarm_text = lambda *_args, **_kwargs: self.fail("MS TTS must stay suppressed")
        app.schedule_alarm_tts_queue.put(
            {
                "text": "edge only",
                "beep": False,
                "category": "general",
                "rate": 0,
                "purge": False,
                "async_mode": False,
                "expires_at": None,
            }
        )
        app.schedule_alarm_tts_queue.put(None)
        app._schedule_alarm_tts_worker_loop()
        self.assertEqual(played, [["edge.mp3"]])

    def test_alarm_tts_worker_uses_recorded_chime_in_same_sequence(self):
        app = object.__new__(BossTimerApp)
        app.schedule_alarm_tts_stop_event = threading.Event()
        app.schedule_alarm_tts_queue = queue.Queue()
        app.schedule_alarm_boss_audio_request_id = 3
        app.schedule_alarm_tts_category_generation = {}
        played = []
        app._wait_for_edge_tts_audio = lambda text, timeout=45.0, rate=0: "edge.mp3"
        app._get_schedule_alarm_chime_path = lambda category, countdown=False: "recorded_chime.wav"
        app._load_schedule_alarm_boss_audio_paths = lambda _paths: True
        app._play_schedule_alarm_boss_audio_paths = lambda paths, **_kwargs: played.append(paths) or True
        app._get_schedule_alarm_clip_sequence_duration_ms = lambda _paths, fallback_ms=1800: fallback_ms
        app._write_schedule_alarm_voice_test_log = lambda *_args, **_kwargs: None
        app.schedule_alarm_tts_queue.put(
            {
                "text": "boss alert",
                "beep": True,
                "category": "general",
                "rate": 1,
                "expires_at": None,
                "play_at": None,
                "generation": 0,
            }
        )
        app.schedule_alarm_tts_queue.put(None)
        app._schedule_alarm_tts_worker_loop()
        self.assertEqual(played, [["recorded_chime.wav", "edge.mp3"]])

    def test_countdown_tts_worker_uses_nonblocking_countdown_host(self):
        app = object.__new__(BossTimerApp)
        app.schedule_alarm_tts_stop_event = threading.Event()
        app.schedule_alarm_tts_queue = queue.Queue()
        app.schedule_alarm_tts_category_generation = {}
        loaded = []
        played = []
        app._wait_for_edge_tts_audio = lambda text, timeout=45.0, rate=0: "sec/15.mp3"
        app._load_schedule_alarm_countdown_audio_clip = lambda path: loaded.append(path) or True
        app._stop_schedule_alarm_countdown_audio = lambda **_kwargs: 41
        app._play_schedule_alarm_countdown_audio_clip = (
            lambda path, **kwargs: played.append((path, kwargs)) or True
        )
        app._get_schedule_alarm_clip_sequence_duration_ms = lambda _paths, fallback_ms=1800: fallback_ms
        app._write_schedule_alarm_voice_test_log = lambda *_args, **_kwargs: None
        app._load_schedule_alarm_boss_audio_paths = lambda _paths: self.fail(
            "countdown edge TTS must not use the blocking boss-audio host"
        )
        app._play_schedule_alarm_boss_audio_paths = lambda *_args, **_kwargs: self.fail(
            "countdown edge TTS must not use the blocking boss-audio host"
        )
        app.schedule_alarm_tts_queue.put(
            {
                "text": "십오",
                "beep": False,
                "category": "countdown",
                "rate": 3,
                "expires_at": None,
                "play_at": None,
                "generation": 0,
            }
        )
        app.schedule_alarm_tts_queue.put(None)

        app._schedule_alarm_tts_worker_loop()

        self.assertEqual(loaded, ["sec/15.mp3"])
        self.assertEqual(played[0][0], "sec/15.mp3")
        self.assertEqual(played[0][1]["request_id"], 41)
        self.assertTrue(played[0][1]["use_host"])

    def test_discord_countdown_bridge_uses_edge_cache_when_recordings_are_not_preferred(self):
        app = object.__new__(BossTimerApp)
        app.discord_countdown_sequence_bridge_keys = set()
        app._is_schedule_alarm_ai_recording_preferred = lambda: False
        app._get_schedule_alarm_countdown_audio_paths = lambda _seconds: self.fail(
            "recorded countdown files must not leak into TTS mode"
        )
        app._get_schedule_alarm_countdown_completion_audio_paths = lambda: self.fail(
            "recorded gen file must not leak into TTS mode"
        )
        app._get_edge_tts_cached_path = lambda text, **_kwargs: f"tts/{text}.mp3"
        app._prefetch_edge_tts_text = lambda *_args, **_kwargs: self.fail("cache is already ready")
        app._write_schedule_alarm_voice_test_log = lambda *_args, **_kwargs: None
        captured = {}
        app._append_discord_voice_bridge_request = (
            lambda **kwargs: captured.update(kwargs) or True
        )
        target_time = datetime.now().replace(microsecond=0) + timedelta(seconds=20)

        emitted = app._append_discord_countdown_sequence_bridge_request(
            scheduled_at=target_time,
            countdown_start_seconds=3,
            group_identity="test-group",
            display_text="테스트보스",
        )

        self.assertTrue(emitted)
        timed_clips = captured["timed_clip_paths"]
        self.assertEqual([path for _play_at, path in timed_clips], [
            "tts/삼.mp3",
            "tts/이.mp3",
            "tts/일.mp3",
            "tts/젠.mp3",
        ])
        self.assertEqual(timed_clips[0][0], target_time - timedelta(seconds=3, milliseconds=100))
        self.assertEqual(timed_clips[-1][0], target_time - timedelta(milliseconds=100))

    def test_prescheduled_discord_countdown_suppresses_local_tts_gen(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_alarm_countdown_completion_audio_paths = lambda: []
        app._drop_pending_schedule_alarm_queue_items = lambda **_kwargs: None
        app._stop_schedule_alarm_countdown_audio = lambda **_kwargs: 1
        app._has_discord_countdown_sequence_bridge_for_target = lambda _target: True
        app._should_mute_local_schedule_audio_for_discord_bot = lambda emitted: emitted
        app._write_schedule_alarm_voice_test_log = lambda *_args, **_kwargs: None
        app._queue_schedule_alarm_speech = lambda *_args, **_kwargs: self.fail(
            "local TTS gen must stay muted when Discord owns the sequence"
        )

        self.assertTrue(
            app._play_schedule_alarm_countdown_completion(
                prefer_audio=False,
                anchor_datetime=datetime.now(),
            )
        )

    def test_stale_offline_discord_status_is_refreshed_before_bridge(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_voice_bridge_online = False
        app.discord_bot_voice_bridge_status_checked_at = 0.0
        app._query_discord_bot_status_port = lambda timeout=0.35: {
            "online": True,
            "voice_connected": True,
            "voice_bridge_enabled": True,
        }

        self.assertTrue(app._is_discord_bot_online_for_voice_bridge())

    def test_second_precision_fallback_splits_gen_word(self):
        app = object.__new__(BossTimerApp)
        self.assertEqual(
            app._split_schedule_alarm_gen_fallback_text("스쿨드 젠"),
            ("스쿨드", "젠"),
        )

    def test_second_precision_tts_prepares_separate_timed_gen_clip(self):
        app = object.__new__(BossTimerApp)
        captured = {}
        app._wait_for_edge_tts_audio = lambda text, timeout=45.0, rate=0: f"{text}.mp3"
        app._is_schedule_alarm_chime_clip_path = lambda path: path.endswith("chime.wav")
        app._get_schedule_alarm_chime_path = lambda _category: "general_chime.wav"
        app._load_schedule_alarm_boss_audio_paths = lambda _paths: True
        app._load_schedule_alarm_second_precision_gen_audio_clip = lambda _path: True
        app._write_schedule_alarm_voice_test_log = lambda *_args, **_kwargs: None
        app._summarize_schedule_alarm_voice_request_for_log = lambda request: request
        app._play_schedule_voice_broker_second_precision_gen_request = (
            lambda request, paths: captured.update(request=request, paths=paths) or True
        )
        request = {
            "phase": "SPAWN_CONFIRMED",
            "target_time": datetime.now() + timedelta(seconds=3),
            "fallback_text": "스쿨드 젠",
            "category": "general",
            "rate": 1,
        }
        self.assertTrue(app._play_schedule_voice_broker_second_precision_tts_request(request, []))
        self.assertTrue(captured["request"]["edge_tts_timed_gen"])
        self.assertEqual(
            captured["paths"],
            ["general_chime.wav", "스쿨드.mp3", "젠.mp3"],
        )

    def test_countdown_tts_completion_is_dispatched_100ms_early(self):
        class _Root:
            def __init__(self):
                self.delay_ms = None

            def winfo_exists(self):
                return True

            def after(self, delay_ms, _callback):
                self.delay_ms = delay_ms

        app = object.__new__(BossTimerApp)
        app.root = _Root()
        now_value = datetime(2026, 8, 31, 12, 0, 0)
        app._get_schedule_reference_datetime = lambda: now_value
        app._get_schedule_alarm_countdown_completion_audio_paths = lambda: []
        app.schedule_alarm_countdown_enabled_var = mock.Mock()
        app._schedule_schedule_alarm_countdown_completion(
            scheduled_at=now_value + timedelta(seconds=2),
            boss_name="스쿨드",
            prefer_audio=False,
        )
        self.assertEqual(app.root.delay_ms, 1900)

    def test_valhalla_start_pre_alert_is_a_plain_one_minute_notice(self):
        app = object.__new__(BossTimerApp)
        scheduled_at = datetime(2026, 8, 31, 18, 0, 0)
        app._get_next_schedule_alarm_target_after = lambda *_args: (
            scheduled_at + timedelta(minutes=20, seconds=12),
            "드라우그",
        )

        message = app._build_schedule_fixed_alarm_message(
            scheduled_at - timedelta(minutes=1),
            scheduled_at,
            "발할라 대전",
            60,
        )

        self.assertEqual(
            message,
            "발할라 대전 1분 전입니다.",
        )

    def test_valhalla_end_notice_is_nineteen_minutes_after_start(self):
        app = object.__new__(BossTimerApp)
        started_at = datetime(2026, 8, 31, 18, 0, 0)
        event_end_at = started_at + timedelta(minutes=20)
        notice_at = event_end_at - timedelta(minutes=1)
        app._get_next_schedule_alarm_target_after = lambda *_args: (
            notice_at + timedelta(minutes=20, seconds=12),
            "드라우그",
        )

        message = app._build_schedule_valhalla_end_alarm_message(notice_at, event_end_at)

        self.assertEqual(
            message,
            "곧 발할라 대전이 종료합니다. 다음 보스는 20분 후 드라우그입니다.",
        )

    def test_tts_pre_alert_includes_chime_and_sentence_duration(self):
        app = object.__new__(BossTimerApp)
        app._with_schedule_alarm_chime_paths = lambda _paths, _category: ["chime.wav"]
        app._get_edge_tts_cached_path = lambda *_args, **_kwargs: "message.mp3"
        app._prefetch_edge_tts_text = lambda *_args, **_kwargs: True
        app._get_schedule_alarm_voice_duration_ms = lambda path: 992 if path == "chime.wav" else None

        lead_seconds = app._get_schedule_alarm_tts_sequence_lead_seconds(
            "드라우그 5분 남았습니다.",
            category="general",
            rate=1,
        )

        self.assertEqual(lead_seconds, 3)

    def test_cache_builder_creates_all_1_to_59_second_targets(self):
        app = object.__new__(BossTimerApp)
        jobs = app._build_edge_tts_cache_jobs(
            include_catalogs=False,
            include_seconds=True,
            include_messages=False,
        )
        sec_jobs = {
            str(job["relpath"]): job
            for job in jobs
            if str(job.get("relpath") or "").startswith("sec/")
        }
        self.assertEqual(len(sec_jobs), 59)
        self.assertEqual(sec_jobs["sec/1.mp3"]["text"], "일")
        self.assertEqual(sec_jobs["sec/15.mp3"]["text"], "십오")
        self.assertEqual(sec_jobs["sec/59.mp3"]["text"], "오십구")
        self.assertTrue(all(int(job["rate"]) == 3 for job in sec_jobs.values()))

    def test_cache_builder_includes_runtime_messages_for_new_name(self):
        app = object.__new__(BossTimerApp)
        app.schedule_alarm_common_offsets = [60, 300]
        app.schedule_boss_alarm_settings = {}
        app.schedule_fixed_boss_alarm_settings = {}
        jobs = app._build_edge_tts_cache_jobs(
            names={"새 이벤트"},
            include_catalogs=False,
            include_seconds=False,
            include_messages=False,
        )
        messages = {(str(job["text"]), int(job["rate"])) for job in jobs}
        self.assertIn(("새 이벤트 젠", 1), messages)
        self.assertIn(("새 이벤트 1분 전입니다.", 1), messages)
        self.assertIn(("곧 새 이벤트 타임입니다.", 1), messages)
        self.assertIn(("새 이벤트 5분 남았습니다.", 1), messages)
        self.assertIn(("새 이벤트 1분 전입니다.", 1), messages)

    def test_countdown_voice_test_waits_for_missing_tts_cache(self):
        app = object.__new__(BossTimerApp)
        app.schedule_alarm_voice_test_selected_rule_index = 4
        app.schedule_alarm_voice_test_status_var = mock.Mock()
        app.schedule_alarm_voice_rule_window = None
        app.edge_tts_cache = mock.Mock(configured=True)
        cache_jobs = [{"text": "십오", "rate": 3, "relpath": "sec/15.mp3"}]
        app._build_edge_tts_cache_jobs = mock.Mock(return_value=cache_jobs)
        app._get_missing_edge_tts_cache_jobs = mock.Mock(return_value=cache_jobs)
        app._start_edge_tts_cache_generation = mock.Mock(return_value=True)
        app._begin_schedule_alarm_voice_test_button_cooldown = mock.Mock(
            side_effect=AssertionError("test must not start before cache is ready")
        )

        app._start_schedule_alarm_voice_test()

        app._start_edge_tts_cache_generation.assert_called_once()
        app._begin_schedule_alarm_voice_test_button_cooldown.assert_not_called()

    def test_voice_test_dataset_requires_all_runtime_items_to_be_test_items(self):
        app = object.__new__(BossTimerApp)
        app.schedule_alarm_voice_test_active = False
        app.schedule_control_events = []
        app.fixed_boss_entries = []
        app.schedule_events = [
            {"raw_key": "normal:기존보스", "boss_name": "기존보스"},
            {
                "raw_key": "normal:과거테스트",
                "boss_name": "과거테스트",
                "voice_test_marker": "old",
            },
        ]

        self.assertFalse(app._is_current_schedule_alarm_voice_test_dataset())

        app.schedule_events = [
            {
                "raw_key": "voice-test:1:테스트보스",
                "boss_name": "테스트보스",
                "voice_test_marker": "current",
            }
        ]
        self.assertTrue(app._is_current_schedule_alarm_voice_test_dataset())

    def test_voice_test_backup_replaces_stale_memory_for_normal_schedule(self):
        app = object.__new__(BossTimerApp)
        fresh_snapshot = {"schedule": {"schedule_events": [{"boss_name": "원본"}]}}
        app.schedule_alarm_voice_test_backup_snapshot = {"schedule": {"schedule_events": []}}
        app.schedule_alarm_voice_test_backup_version = "stale"
        app.schedule_events = [{"boss_name": "원본"}]
        app.schedule_active_entries = []
        app.schedule_control_events = []
        app._is_current_schedule_alarm_voice_test_dataset = lambda: False
        app._create_schedule_alarm_voice_test_snapshot = lambda: fresh_snapshot
        app._write_schedule_alarm_voice_test_snapshot_file = mock.Mock(return_value=True)
        app._refresh_schedule_alarm_voice_test_restore_button_state = lambda: None
        app._append_debug_log = lambda *_args, **_kwargs: None

        self.assertTrue(app._save_schedule_alarm_voice_test_backup())
        self.assertIs(app.schedule_alarm_voice_test_backup_snapshot, fresh_snapshot)
        app._write_schedule_alarm_voice_test_snapshot_file.assert_called_once()

    def test_voice_test_restore_prefers_disk_original_over_memory(self):
        app = object.__new__(BossTimerApp)
        disk_snapshot = {"schedule": {"schedule_events": [{"boss_name": "디스크원본"}]}}
        app.schedule_alarm_voice_test_backup_snapshot = {
            "schedule": {"schedule_events": [{"boss_name": "오래된메모리"}]}
        }
        app.schedule_alarm_voice_test_backup_version = "old"
        app.schedule_alarm_voice_test_status_var = mock.Mock()
        app._load_schedule_alarm_voice_test_original_snapshot = lambda: disk_snapshot
        app._apply_schedule_alarm_voice_test_snapshot = mock.Mock(return_value=True)
        app._remove_schedule_alarm_voice_test_session_files = mock.Mock()
        app._refresh_schedule_alarm_voice_test_restore_button_state = lambda: None
        app._return_schedule_view_to_today_after_voice_test = lambda: None

        self.assertTrue(app._restore_schedule_alarm_voice_test_backup(skip_runtime_stop=True))
        app._apply_schedule_alarm_voice_test_snapshot.assert_called_once_with(disk_snapshot)
        app._remove_schedule_alarm_voice_test_session_files.assert_called_once()
        self.assertIsNone(app.schedule_alarm_voice_test_backup_snapshot)

    def test_voice_test_original_snapshot_round_trip_uses_separate_file(self):
        app = object.__new__(BossTimerApp)
        app._append_debug_log = lambda *_args, **_kwargs: None
        snapshot = {
            "voice_rule_version": "test",
            "saved_at": datetime(2026, 9, 1, 13, 30, 0),
            "schedule": {
                "schedule_events": [
                    {
                        "boss_name": "원본보스",
                        "scheduled_at": datetime(2026, 9, 1, 14, 0, 0),
                    }
                ]
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            original_path = os.path.join(temp_dir, "voice_test_original.json")
            runtime_path = os.path.join(temp_dir, "voice_test_runtime.json")
            with (
                mock.patch("boss_timer_gui.SCHEDULE_ALARM_VOICE_TEST_ORIGINAL_PATH", original_path),
                mock.patch("boss_timer_gui.SCHEDULE_ALARM_VOICE_TEST_RUNTIME_PATH", runtime_path),
            ):
                self.assertTrue(
                    app._write_schedule_alarm_voice_test_snapshot_file(
                        original_path,
                        snapshot,
                        snapshot_type="original",
                    )
                )
                restored = app._load_schedule_alarm_voice_test_original_snapshot()
                self.assertEqual(
                    restored["schedule"]["schedule_events"][0]["scheduled_at"],
                    datetime(2026, 9, 1, 14, 0, 0),
                )
                app._remove_schedule_alarm_voice_test_session_files()
                self.assertFalse(os.path.exists(original_path))


class ScheduleBossMetricFormTests(unittest.TestCase):
    def test_duration_field_updates_form_state_while_typing(self):
        app = object.__new__(BossTimerApp)
        app.schedule_boss_metric_source_mode_var = mock.Mock()
        app.schedule_boss_metric_user_duration_var = mock.Mock()
        app.schedule_boss_metric_score_var = mock.Mock()
        app.schedule_boss_metric_war_score_var = mock.Mock()
        app._on_schedule_boss_metric_form_value_changed = mock.Mock()

        app._bind_schedule_boss_metric_form_value_traces()

        app.schedule_boss_metric_user_duration_var.trace_add.assert_called_once_with(
            "write",
            app._on_schedule_boss_metric_form_value_changed,
        )


class DiscordTimedCompositeTests(unittest.TestCase):
    def test_legacy_bot_group_uses_one_name_and_does_not_repeat_invasion(self):
        primary_name, additional_count, all_invasion = compact_alert_names(
            ("니드호그", "셀로비아", "침공 라타토스크", "침공 비요른")
        )

        self.assertEqual(primary_name, "니드호그")
        self.assertEqual(additional_count, 3)
        self.assertFalse(all_invasion)

    def test_edge_tts_cache_gain_is_uniform_for_countdown_and_messages(self):
        bot = object.__new__(DiscordScheduleBot)

        self.assertAlmostEqual(
            bot._get_clip_playback_volume(r"C:\BossTimer\tts_캐쉬\sec\15.mp3", 0.8),
            1.44,
        )
        self.assertEqual(bot._get_clip_playback_volume(r"C:\BossTimer\voice\sec\15.wav", 0.8), 0.8)
        self.assertEqual(
            bot._get_clip_playback_volume(r"C:\BossTimer\tts_캐쉬\sec\10.mp3", 1.0),
            1.8,
        )
        self.assertEqual(
            bot._get_clip_playback_volume(r"C:\BossTimer\tts_캐쉬\message\chain.mp3", 1.0),
            1.8,
        )

    def test_countdown_host_preloads_without_playing_audio_during_startup(self):
        app = object.__new__(BossTimerApp)
        command = app._build_schedule_alarm_countdown_audio_host_command(
            [r"C:\BossTimer\tts_캐쉬\sec\10.mp3"]
        )
        script = base64.b64decode(command[-1]).decode("utf-16le")

        self.assertNotIn("$primePlayer.Play()", script)
        self.assertIn("선로딩은 Open까지만 한다", script)
        self.assertNotIn("$boostPlayers", script)
        self.assertNotIn("$activeBoostPlayer", script)

    def test_timed_composite_prewarms_and_mixes_overlapping_clip_tails(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.client = mock.Mock(loop=mock.Mock())
        bot.discord = type("_DiscordModule", (), {"AudioSource": object})
        first_sample = int(1000).to_bytes(2, "little", signed=True)
        second_sample = int(2000).to_bytes(2, "little", signed=True)
        first_pcm = first_sample * (48000 * 2 * 4 // 100)
        second_pcm = second_sample * (48000 * 2 * 2 // 100)
        bot._read_timed_clip_pcm = lambda path, _volume, _lane="center": first_pcm if path == "first" else second_pcm
        first_at = datetime(2026, 8, 31, 12, 0, 0)
        job = VoiceBridgeJob(
            id="overlap-test",
            created_at=first_at,
            phase="COUNTDOWN_SEQUENCE",
            category="countdown",
            lane="center",
            volume=1.0,
            clip_paths=(),
            timed_clips=((first_at, "first"), (first_at + timedelta(milliseconds=20), "second")),
        )

        source, stream_start_at, _duration_ms = bot._create_timed_composite_source(job)

        self.assertEqual(
            stream_start_at,
            first_at - timedelta(seconds=DISCORD_COUNTDOWN_COMPOSITE_WARMUP_SEC),
        )
        second_offset = int(
            round((first_at + timedelta(milliseconds=20) - stream_start_at).total_seconds() * 48000.0)
        ) * 4
        mixed_sample = int.from_bytes(
            source.template_data[second_offset:second_offset + 2],
            "little",
            signed=True,
        )
        self.assertEqual(mixed_sample, 3000)

    def test_timed_composite_accepts_edge_tts_mp3_pcm_source(self):
        class _Source:
            def __init__(self):
                self.chunks = [b"pcm", b""]

            def read(self):
                return self.chunks.pop(0)

            def cleanup(self):
                return None

        bot = object.__new__(DiscordScheduleBot)
        bot.timed_pcm_cache = {}
        bot.timed_pcm_cache_lock = threading.Lock()
        bot._create_playback_source = lambda *_args, **_kwargs: _Source()
        bot._cleanup_audio_source = lambda source: source.cleanup()
        with tempfile.TemporaryDirectory() as temp_dir:
            clip_path = Path(temp_dir) / "countdown.mp3"
            clip_path.write_bytes(b"ID3 fake edge cache")
            pcm = bot._read_timed_clip_pcm(str(clip_path), 1.0)

        self.assertEqual(pcm, b"pcm")

    def test_timed_composite_pans_right_lane_pcm(self):
        frame = int(1200).to_bytes(2, "little", signed=True) * 2

        balanced = DiscordScheduleBot._apply_timed_pcm_lane(frame, "right")

        self.assertEqual(int.from_bytes(balanced[0:2], "little", signed=True), 0)
        self.assertEqual(int.from_bytes(balanced[2:4], "little", signed=True), 1200)

    def test_discord_invasion_timed_sequence_cancels_right_lane_attenuation(self):
        job = VoiceBridgeJob(
            id="invasion-volume",
            created_at=datetime.now(),
            phase="SPAWN_CONFIRMED_SEQUENCE",
            category="general",
            lane="right",
            volume=0.8,
            clip_paths=(),
            timed_clips=(),
            fallback_text="침공 비요른 젠",
        )

        self.assertAlmostEqual(DiscordScheduleBot._get_timed_job_playback_volume(job), 1.0)


class DiscordGatewayRecoveryTests(unittest.TestCase):
    @staticmethod
    def _schedule_message_for_guild(guild_id, channel_id=700):
        guild = type("Guild", (), {"id": guild_id})()
        channel = type("TextChannel", (), {"id": channel_id})()
        author = type("Author", (), {"bot": False, "id": 55, "display_name": "테스터"})()
        message = type(
            "Message",
            (),
            {
                "id": 99,
                "guild": guild,
                "channel": channel,
                "author": author,
                "content": "1800 스카디",
                "add_reaction": mock.AsyncMock(),
            },
        )()
        return message, channel

    @staticmethod
    def _message_bot_for_server(server_id, target_channel):
        bot = object.__new__(DiscordScheduleBot)
        bot.config = {"server_id": str(server_id)}
        bot.message_content_enabled = True
        bot._resolve_text_channel = mock.AsyncMock(return_value=target_channel)
        bot._queue_local_schedule_request = mock.AsyncMock()
        return bot

    def test_each_same_token_instance_only_handles_its_owned_server_message(self):
        message, channel = self._schedule_message_for_guild(800)
        odin8_bot = self._message_bot_for_server(800, channel)
        odin9_bot = self._message_bot_for_server(900, channel)

        async def run_both_instances():
            await odin8_bot._handle_schedule_text_message(message)
            await odin9_bot._handle_schedule_text_message(message)

        asyncio.run(run_both_instances())

        odin8_bot._queue_local_schedule_request.assert_awaited_once()
        queued_payload = odin8_bot._queue_local_schedule_request.await_args.args[0]
        self.assertEqual(queued_payload["server_id"], "800")
        odin9_bot._resolve_text_channel.assert_not_awaited()
        odin9_bot._queue_local_schedule_request.assert_not_awaited()
        message.add_reaction.assert_awaited_once_with("✅")

    def test_builtin_voice_command_is_queued_only_from_configured_text_channel(self):
        message, channel = self._schedule_message_for_guild(800)
        message.content = "광역체크"
        bot = self._message_bot_for_server(800, channel)

        asyncio.run(bot._handle_schedule_text_message(message))

        bot._queue_local_schedule_request.assert_awaited_once()
        payload = bot._queue_local_schedule_request.await_args.args[0]
        self.assertEqual(payload["operation"], "voice_play")
        self.assertEqual(payload["voice_command"], "광역체크")
        self.assertEqual(payload["tts_text"], "광역 체크해주세요.")
        message.add_reaction.assert_awaited_once_with("🔊")

    def test_builtin_voice_command_is_ignored_in_other_text_channel(self):
        message, _message_channel = self._schedule_message_for_guild(800, channel_id=701)
        message.content = "집결지"
        target_channel = type("TextChannel", (), {"id": 700})()
        bot = self._message_bot_for_server(800, target_channel)

        asyncio.run(bot._handle_schedule_text_message(message))

        bot._queue_local_schedule_request.assert_not_awaited()
        message.add_reaction.assert_not_awaited()

    def test_all_slash_commands_have_owned_server_guard(self):
        source = inspect.getsource(DiscordScheduleBot._bind_commands)

        self.assertEqual(source.count("@self.tree.command"), 9)
        self.assertEqual(
            source.count("if not self._should_handle_interaction(interaction):"),
            9,
        )
        self.assertNotIn('self.config["server_id"]', source)
        self.assertNotIn('save_config_value("server_id"', source)

    def test_custom_voice_command_registry_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "voice_commands.json"
            commands = {
                "바보": ("바보", "바보"),
                "모여주세요": ("모여 주세요", "모여 주세요"),
            }

            save_custom_discord_voice_commands(commands, registry_path)
            restored = load_custom_discord_voice_commands(registry_path)

        self.assertEqual(restored, commands)
        self.assertNotIn("광역체크", restored)

    def test_builtin_voice_commands_can_be_deleted_per_server(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.custom_voice_commands = {}
        bot.disabled_builtin_voice_commands = set()

        with mock.patch("boss_timer_discord_bot.save_custom_discord_voice_commands"):
            deleted, result = bot._delete_discord_voice_command("광역체크")

        self.assertTrue(deleted)
        self.assertIn("광역", result)
        self.assertIn(normalize_discord_voice_command_name("광역체크"), bot.disabled_builtin_voice_commands)
        self.assertIsNone(bot._resolve_discord_voice_command("광역체크"))
        self.assertNotIn(
            "광역체크",
            {name for _normalized, name, _tts_text in bot._get_discord_voice_command_menu_entries()},
        )

    def test_disabled_builtin_voice_command_registry_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            registry_path = Path(temp_dir) / "voice_commands.json"
            disabled = {normalize_discord_voice_command_name("광역체크")}
            save_custom_discord_voice_commands(
                {},
                registry_path,
                disabled_builtin_commands=disabled,
            )
            restored = load_disabled_builtin_discord_voice_commands(registry_path)

        self.assertEqual(restored, disabled)

    def test_interaction_owner_check_accepts_only_configured_server(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.config = {"server_id": "800"}
        owned_interaction = type("Interaction", (), {"guild_id": 800, "command": None})()
        foreign_interaction = type("Interaction", (), {"guild_id": 900, "command": None})()

        with mock.patch("boss_timer_discord_bot.log"):
            self.assertTrue(bot._should_handle_interaction(owned_interaction))
            self.assertFalse(bot._should_handle_interaction(foreign_interaction))

    def test_text_channel_resolution_rejects_foreign_guild_before_lookup(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.config = {"server_id": "800", "text_channel_id": "700"}
        bot.client = mock.Mock()
        foreign_guild = type("Guild", (), {"id": 900})()

        with mock.patch("boss_timer_discord_bot.log"):
            channel = asyncio.run(bot._resolve_text_channel(foreign_guild))

        self.assertIsNone(channel)
        bot.client.get_channel.assert_not_called()
        bot.client.fetch_channel.assert_not_called()

    def test_wrong_guild_configured_text_channel_falls_back_only_inside_owned_guild(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.config = {"server_id": "800", "text_channel_id": "701"}
        foreign_channel = type(
            "TextChannel",
            (),
            {"id": 701, "guild": type("Guild", (), {"id": 900})(), "send": mock.AsyncMock()},
        )()
        owned_channel = type(
            "TextChannel",
            (),
            {
                "id": 702,
                "name": "보탐매니저",
                "guild": type("Guild", (), {"id": 800})(),
                "send": mock.AsyncMock(),
            },
        )()
        owned_guild = type("Guild", (), {"id": 800, "text_channels": [owned_channel]})()
        bot.client = mock.Mock()
        bot.client.get_channel.return_value = foreign_channel

        with (
            mock.patch("boss_timer_discord_bot.log"),
            mock.patch("boss_timer_discord_bot.save_config_value") as save_mock,
        ):
            channel = asyncio.run(bot._resolve_text_channel(owned_guild))

        self.assertIs(channel, owned_channel)
        self.assertEqual(bot.config["text_channel_id"], "702")
        save_mock.assert_called_once_with("text_channel_id", "702")

    def test_voice_connection_rejects_channel_from_foreign_server(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.config = {"server_id": "800"}
        bot.voice_client = None
        foreign_channel = mock.Mock()
        foreign_channel.guild.id = 900
        bot.client = mock.Mock()
        bot.client.get_channel.return_value = foreign_channel

        with (
            mock.patch("boss_timer_discord_bot.STATUS"),
            mock.patch("boss_timer_discord_bot.log"),
        ):
            connected, message = asyncio.run(bot._connect_voice_channel("456"))

        self.assertFalse(connected)
        self.assertIn("서버", message)
        foreign_channel.connect.assert_not_called()

    def test_command_sync_is_guild_scoped_and_never_global(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.config = {"server_id": "800"}
        guild_object = object()
        bot.discord = mock.Mock()
        bot.discord.Object.return_value = guild_object
        bot.tree = mock.Mock()
        bot.tree.sync = mock.AsyncMock()

        with mock.patch("boss_timer_discord_bot.log"):
            asyncio.run(bot._sync_commands())

        bot.discord.Object.assert_called_once_with(id=800)
        bot.tree.copy_global_to.assert_called_once_with(guild=guild_object)
        bot.tree.sync.assert_awaited_once_with(guild=guild_object)

    def test_command_sync_without_server_id_registers_nothing(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.config = {"server_id": ""}
        bot.tree = mock.Mock()
        bot.tree.sync = mock.AsyncMock()

        with (
            mock.patch("boss_timer_discord_bot.STATUS"),
            mock.patch("boss_timer_discord_bot.log"),
        ):
            asyncio.run(bot._sync_commands())

        bot.tree.copy_global_to.assert_not_called()
        bot.tree.sync.assert_not_awaited()

    def test_discord_settings_require_owned_server_and_voice_channel_ids(self):
        validate = BossTimerApp._get_discord_bot_settings_validation_error

        self.assertEqual(
            validate(
                token="token",
                application_id="100",
                server_id="800",
                voice_channel_id="801",
                text_channel_id="",
            ),
            "",
        )
        self.assertIn(
            "서버 ID",
            validate(
                token="token",
                application_id="100",
                server_id="odin8",
                voice_channel_id="801",
                text_channel_id="",
            ),
        )
        self.assertIn(
            "음성채널 ID",
            validate(
                token="token",
                application_id="100",
                server_id="800",
                voice_channel_id="voice",
                text_channel_id="",
            ),
        )

    def test_gui_bridge_status_rejects_bot_connected_to_other_server(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_server_id = "800"
        app.discord_bot_voice_channel_id = "801"

        app._set_discord_bot_status_payload(
            {
                "online": True,
                "voice_connected": True,
                "voice_bridge_enabled": True,
                "guild_id": "900",
                "voice_channel_id": "901",
            }
        )

        self.assertFalse(app.discord_bot_voice_bridge_online)
        self.assertEqual(app._get_discord_bot_status_kind(), "error")

    def test_fixed_group_preserve_flag_reaches_voice_broker_request(self):
        app = object.__new__(BossTimerApp)
        app.schedule_voice_broker_stop_event = mock.Mock()
        app.schedule_voice_broker_stop_event.is_set.return_value = False
        app.schedule_voice_broker_queue = queue.Queue()
        app.schedule_voice_broker_generation = 7
        app.schedule_voice_broker_countdown_block_until = None
        app._ensure_schedule_voice_broker_thread = mock.Mock()
        app._normalize_schedule_voice_lane = lambda lane: str(lane)
        app._is_schedule_alarm_ai_recording_preferred = lambda: False
        app._filter_schedule_alarm_recording_paths = lambda paths: list(paths)
        app._prefetch_edge_tts_text = mock.Mock()
        app._write_schedule_alarm_voice_test_log = mock.Mock()
        app._summarize_schedule_alarm_voice_request_for_log = lambda request: request

        app._submit_schedule_voice_request(
            phase="FIXED_PRE_ALERT",
            boss_id="지옥성채 정예|빛빛고블린",
            target_time=datetime(2026, 9, 2, 20, 0, 0),
            offset_sec=60,
            fallback_text="지옥성채 정예, 빛빛고블린 1분 전입니다.",
            preserve_fixed_message=True,
        )

        request = app.schedule_voice_broker_queue.get_nowait()
        self.assertTrue(request["preserve_fixed_message"])
        self.assertFalse(request["recording_preferred"])
        self.assertFalse(request["countdown_enabled_at_submit"])

    def test_alarm_tick_reschedules_after_non_tk_runtime_error(self):
        app = object.__new__(BossTimerApp)
        app.schedule_alarm_after_id = None
        app.scheduler_worker_mode = False
        app.schedule_alarm_second_precision_gen_pending_keys = set()
        app.root = mock.Mock()
        app.root.winfo_exists.return_value = True
        app.root.after.return_value = "next-alarm-tick"
        app._process_schedule_alarm_tick = mock.Mock(side_effect=RuntimeError("test failure"))
        app._get_wall_clock_aligned_delay_ms = mock.Mock(return_value=100)
        app._append_debug_log = mock.Mock()
        app._trace_periodic_callback_duration = mock.Mock()

        with mock.patch("builtins.open", mock.mock_open()):
            app._schedule_alarm_tick()

        app.root.after.assert_called_once_with(100, app._schedule_alarm_tick)
        self.assertEqual(app.schedule_alarm_after_id, "next-alarm-tick")

    def test_voice_bridge_heartbeat_ack_clears_pending_health_failure(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_expected_running = True
        app.discord_bot_reconnect_in_progress = False
        app.discord_bot_voice_bridge_recovery_grace_until = 0.0
        app.discord_bot_status_failure_count = 0
        app.discord_bot_voice_bridge_heartbeat_failure_count = 1
        app.discord_bot_voice_bridge_heartbeat_pending_id = "heartbeat-test"
        app.discord_bot_voice_bridge_heartbeat_pending_offset = 321
        app.discord_bot_voice_bridge_heartbeat_sent_at = time.monotonic()
        app.discord_bot_voice_bridge_heartbeat_last_sent_at = time.monotonic()
        app._recover_discord_bot_runtime = mock.Mock()

        app._monitor_discord_bot_voice_bridge_health(
            {
                "ok": True,
                "online": True,
                "voice_connected": True,
                "voice_bridge_enabled": True,
                "voice_bridge_offset": 321,
            }
        )

        self.assertEqual(app.discord_bot_voice_bridge_heartbeat_pending_id, "")
        self.assertEqual(app.discord_bot_voice_bridge_heartbeat_failure_count, 0)
        app._recover_discord_bot_runtime.assert_not_called()

    def test_three_failed_bot_status_checks_start_full_reconnect(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_expected_running = True
        app.discord_bot_reconnect_in_progress = False
        app.discord_bot_voice_bridge_recovery_grace_until = 0.0
        app.discord_bot_status_failure_count = 0
        app._recover_discord_bot_runtime = mock.Mock()

        for _index in range(3):
            app._monitor_discord_bot_voice_bridge_health({})

        app._recover_discord_bot_runtime.assert_called_once_with(
            "상태 포트 또는 디스코드 음성 연결 응답 끊김"
        )

    def test_auto_reconnect_notice_is_queued_only_after_voice_is_healthy(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_pending_auto_reconnect_notice = {"reason": "하트비트 응답 끊김"}
        app.discord_bot_server_id = "123"
        app.discord_bot_voice_channel_id = "456"
        app._append_discord_voice_bridge_text_notice = mock.Mock(return_value=True)
        app._append_discord_schedule_monitor_entry = mock.Mock()
        app._append_debug_log = mock.Mock()

        self.assertFalse(app._maybe_emit_discord_auto_reconnect_notice({"online": True}))
        self.assertIsNotNone(app.discord_bot_pending_auto_reconnect_notice)

        emitted = app._maybe_emit_discord_auto_reconnect_notice(
            {
                "ok": True,
                "online": True,
                "voice_connected": True,
                "voice_bridge_enabled": True,
                "guild_id": "123",
                "voice_channel_id": "456",
            }
        )

        self.assertTrue(emitted)
        self.assertIsNone(app.discord_bot_pending_auto_reconnect_notice)
        notice = app._append_discord_voice_bridge_text_notice.call_args.args[0]
        self.assertIn("자동 재접속했습니다", notice)
        self.assertIn("하트비트 응답 끊김", notice)
        app._append_discord_schedule_monitor_entry.assert_called_once()

    def test_auto_recovery_marks_reconnect_request_for_completion_notice(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_reconnect_in_progress = False
        app.discord_bot_voice_bridge_recovery_grace_until = 0.0
        app.discord_bot_server_id = "123"
        app.discord_bot_voice_channel_id = "456"
        app._append_debug_log = mock.Mock()
        app._reconnect_discord_bot_runtime_from_request = mock.Mock(return_value=(True, "예약됨"))

        app._recover_discord_bot_runtime("응답 끊김")

        request = app._reconnect_discord_bot_runtime_from_request.call_args.args[0]
        self.assertTrue(request["automatic_recovery"])
        self.assertEqual(request["reconnect_reason"], "응답 끊김")

    def test_successful_auto_reconnect_waits_with_pending_notice_until_health_check(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_reconnect_in_progress = False
        app.discord_bot_startup_cleanup_failed = True
        app.discord_bot_server_id = "123"
        app.discord_bot_voice_channel_id = "456"
        app.discord_bot_pending_auto_reconnect_notice = None
        app._save_discord_bot_settings = mock.Mock(return_value=True)
        app._set_discord_bot_toggle_locked = mock.Mock()
        app._stop_discord_bot_runtime_core = mock.Mock(return_value=True)
        app._refresh_discord_bot_status_ui = mock.Mock()
        app._start_discord_bot_runtime = mock.Mock(return_value=True)
        app.schedule_status_var = mock.Mock()
        app.root = mock.Mock()
        scheduled_callbacks = []
        app.root.after.side_effect = lambda delay, callback: scheduled_callbacks.append((delay, callback))

        success, _message = app._reconnect_discord_bot_runtime_from_request({
            "server_id": "123",
            "voice_channel_id": "456",
            "automatic_recovery": True,
            "reconnect_reason": "로컬 응답 끊김",
        })

        self.assertTrue(success)
        self.assertEqual(app.discord_bot_pending_auto_reconnect_notice["reason"], "로컬 응답 끊김")
        self.assertEqual(scheduled_callbacks[0][0], 3000)
        scheduled_callbacks[0][1]()
        self.assertFalse(app.discord_bot_reconnect_in_progress)
        self.assertIsNotNone(app.discord_bot_pending_auto_reconnect_notice)

    def test_monitor_position_prefers_right_then_above_then_below(self):
        selector = BossTimerApp._select_discord_schedule_monitor_position
        self.assertEqual(selector((100, 100, 600, 500), (0, 0, 1400, 900), (300, 250)), (708, 100))
        self.assertEqual(selector((400, 300, 600, 400), (0, 0, 1000, 900), (300, 250)), (550, 42))
        self.assertEqual(selector((400, 0, 600, 400), (0, 0, 1000, 900), (300, 250)), (550, 408))

    def test_voice_bridge_reader_accepts_text_notice_without_audio_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "queue.jsonl"
            path.write_text("", encoding="utf-8")
            reader = VoiceBridgeReader(path)
            control = reader._parse_job(json.dumps({
                "id": "notice-1",
                "created_at": "2026-09-02T20:00:00",
                "action": "text_notice",
                "message": "자동 재접속했습니다.",
            }, ensure_ascii=False))

        self.assertIsInstance(control, VoiceBridgeControl)
        self.assertEqual(control.action, "text_notice")
        self.assertEqual(control.message, "자동 재접속했습니다.")

    def test_bot_sends_text_notice_control_to_configured_channel(self):
        bot = object.__new__(DiscordScheduleBot)
        channel = mock.Mock(id=789)
        channel.send = mock.AsyncMock()
        bot._resolve_text_channel = mock.AsyncMock(return_value=channel)
        embed = mock.Mock()
        bot.discord = mock.Mock()
        bot.discord.Embed.return_value = embed

        asyncio.run(bot._handle_voice_bridge_control(VoiceBridgeControl(
            id="notice-1",
            created_at=datetime.now(),
            action="text_notice",
            message="자동 재접속했습니다.",
        )))

        channel.send.assert_awaited_once_with(embed=embed)

    def test_packaged_ffmpeg_is_preferred_over_system_path(self):
        with tempfile.TemporaryDirectory() as resource_dir, tempfile.TemporaryDirectory() as app_dir:
            packaged_ffmpeg = Path(resource_dir) / "ffmpeg.exe"
            packaged_ffmpeg.write_bytes(b"ffmpeg")
            with (
                mock.patch("boss_timer_discord_bot.sys._MEIPASS", resource_dir, create=True),
                mock.patch("boss_timer_discord_bot.APP_ROOT", Path(app_dir)),
                mock.patch("boss_timer_discord_bot.shutil.which", return_value=None),
            ):
                resolved = DiscordScheduleBot._get_ffmpeg_executable()

        self.assertEqual(resolved, str(packaged_ffmpeg))

    def test_distribution_cache_is_seeded_once_and_existing_manifest_is_preserved(self):
        with tempfile.TemporaryDirectory() as resource_dir, tempfile.TemporaryDirectory() as app_dir:
            resource_cache = Path(resource_dir) / "tts_캐쉬"
            resource_cache.mkdir()
            (resource_cache / "manifest.json").write_text('{"version": 1}', encoding="utf-8")
            (resource_cache / "sec").mkdir()
            (resource_cache / "sec" / "1.mp3").write_bytes(b"first")
            runtime_cache = Path(app_dir) / "tts_캐쉬"
            seed_marker = Path(app_dir) / "edge_tts_cache_distribution_v5.seed"
            app = object.__new__(BossTimerApp)
            app._append_debug_log = mock.Mock()

            with (
                mock.patch("boss_timer_gui.get_resource_root", return_value=resource_dir),
                mock.patch("boss_timer_gui.EDGE_TTS_CACHE_DIR", str(runtime_cache)),
                mock.patch("boss_timer_gui.EDGE_TTS_DISTRIBUTION_CACHE_SEED_MARKER_PATH", str(seed_marker)),
            ):
                app._seed_runtime_edge_tts_cache_from_resources()
                self.assertEqual((runtime_cache / "sec" / "1.mp3").read_bytes(), b"first")
                self.assertTrue(seed_marker.is_file())
                (runtime_cache / "sec" / "1.mp3").write_bytes(b"user-cache")
                (resource_cache / "sec" / "1.mp3").write_bytes(b"new-distribution")
                app._seed_runtime_edge_tts_cache_from_resources()

            self.assertEqual((runtime_cache / "sec" / "1.mp3").read_bytes(), b"user-cache")

    def test_invite_alias_prefers_callers_current_voice_channel(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.config = {"voice_channel_id": "222"}
        caller_channel = type("VoiceChannel", (), {"id": 111})()
        voice_state = type("VoiceState", (), {"channel": caller_channel})()
        user = type("User", (), {"voice": voice_state})()
        interaction = type("Interaction", (), {"user": user})()

        channel_id, description = bot._resolve_invite_voice_channel(interaction, "보탐매니저")

        self.assertEqual(channel_id, "111")
        self.assertIn("참여 중", description)

    def test_invite_alias_falls_back_to_configured_voice_channel(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.config = {"voice_channel_id": "222"}
        user = type("User", (), {"voice": None})()
        interaction = type("Interaction", (), {"user": user})()

        channel_id, description = bot._resolve_invite_voice_channel(interaction, "보탐")

        self.assertEqual(channel_id, "222")
        self.assertIn("기본 음성채널", description)

    def test_local_invite_request_stops_then_restarts_bot_after_three_seconds(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_reconnect_in_progress = False
        app.discord_bot_startup_cleanup_failed = True
        app.discord_bot_server_id = "123"
        app.discord_bot_voice_channel_id = "old-channel"
        app._save_discord_bot_settings = mock.Mock(return_value=True)
        app._set_discord_bot_toggle_locked = mock.Mock()
        app._stop_discord_bot_runtime_core = mock.Mock(return_value=True)
        app._refresh_discord_bot_status_ui = mock.Mock()
        app._start_discord_bot_runtime = mock.Mock(return_value=True)
        app.schedule_status_var = mock.Mock()
        scheduled_callbacks = []
        app.root = mock.Mock()
        app.root.after.side_effect = lambda delay, callback: scheduled_callbacks.append((delay, callback))

        success, _message = app._apply_discord_schedule_request(
            {
                "operation": "discord_reconnect",
                "server_id": "123",
                "voice_channel_id": "456",
            }
        )

        self.assertTrue(success)
        self.assertEqual(app.discord_bot_server_id, "123")
        self.assertEqual(app.discord_bot_voice_channel_id, "456")
        app._save_discord_bot_settings.assert_called_once_with()
        app._stop_discord_bot_runtime_core.assert_called_once_with(graceful_timeout=2.5, force_timeout=1.0)
        self.assertEqual(scheduled_callbacks[0][0], 3000)
        self.assertTrue(app.discord_bot_reconnect_in_progress)

        scheduled_callbacks[0][1]()

        app._start_discord_bot_runtime.assert_called_once_with()
        self.assertFalse(app.discord_bot_reconnect_in_progress)

    def test_local_reconnect_request_cannot_change_owned_server(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_reconnect_in_progress = False
        app.discord_bot_server_id = "800"
        app.discord_bot_voice_channel_id = "801"
        app._append_debug_log = mock.Mock()
        app._save_discord_bot_settings = mock.Mock(return_value=True)
        app._stop_discord_bot_runtime_core = mock.Mock(return_value=True)

        success, message = app._apply_discord_schedule_request(
            {
                "operation": "discord_reconnect",
                "server_id": "900",
                "voice_channel_id": "901",
            }
        )

        self.assertFalse(success)
        self.assertIn("다른 서버", message)
        self.assertEqual(app.discord_bot_server_id, "800")
        self.assertEqual(app.discord_bot_voice_channel_id, "801")
        app._save_discord_bot_settings.assert_not_called()
        app._stop_discord_bot_runtime_core.assert_not_called()

    def test_new_runtime_disconnects_stale_voice_and_waits_before_connecting(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.voice_client = None
        bot.config = {"server_id": "123"}
        stale_channel = mock.Mock(id=456)
        guild = mock.Mock()
        guild.me.voice.channel = stale_channel
        guild.change_voice_state = mock.AsyncMock()
        bot.client = mock.Mock()
        bot.client.get_guild.return_value = guild
        fake_status = mock.Mock()

        with (
            mock.patch("boss_timer_discord_bot.STATUS", fake_status),
            mock.patch("boss_timer_discord_bot.asyncio.sleep", new=mock.AsyncMock()) as sleep_mock,
        ):
            disconnected = asyncio.run(bot._disconnect_stale_configured_voice_session())

        self.assertTrue(disconnected)
        guild.change_voice_state.assert_awaited_once_with(channel=None)
        sleep_mock.assert_awaited_once_with(3.0)
        fake_status.update.assert_called_once_with(voice_connected=False)

    def test_new_runtime_forces_voice_reset_even_when_cache_has_no_stale_state(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.voice_client = None
        bot.config = {"server_id": "123"}
        guild = mock.Mock()
        guild.me.voice = None
        guild.change_voice_state = mock.AsyncMock()
        bot.client = mock.Mock()
        bot.client.get_guild.return_value = guild

        with (
            mock.patch("boss_timer_discord_bot.STATUS"),
            mock.patch("boss_timer_discord_bot.asyncio.sleep", new=mock.AsyncMock()) as sleep_mock,
        ):
            disconnected = asyncio.run(bot._disconnect_stale_configured_voice_session())

        self.assertTrue(disconnected)
        guild.change_voice_state.assert_awaited_once_with(channel=None)
        sleep_mock.assert_awaited_once_with(3.0)

    def test_stuck_gateway_closes_client_so_runtime_can_restart(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.client = mock.Mock()
        bot.client.is_ready.return_value = False
        bot.client.close = mock.AsyncMock()
        bot.gateway_disconnected_at = 1.0
        fake_status = mock.Mock()
        fake_status.shutdown_requested.is_set.return_value = False

        with (
            mock.patch("boss_timer_discord_bot.STATUS", fake_status),
            mock.patch("boss_timer_discord_bot.asyncio.sleep", new=mock.AsyncMock()),
            mock.patch("boss_timer_discord_bot.time.monotonic", return_value=22.0),
        ):
            asyncio.run(bot._gateway_recovery_loop())

        bot.client.close.assert_awaited_once_with()
        self.assertTrue(any(call.kwargs.get("online") is False for call in fake_status.update.call_args_list))

    def test_ready_gateway_reconnects_voice_without_waiting_for_alert(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.client = mock.Mock()
        bot.client.is_ready.return_value = True
        bot.voice_client = None
        bot.gateway_disconnected_at = None
        bot.last_voice_reconnect_attempt_at = 0.0
        bot._connect_configured_voice_channel = mock.AsyncMock()
        fake_status = mock.Mock()
        fake_status.shutdown_requested.is_set.side_effect = [False, True]

        with (
            mock.patch("boss_timer_discord_bot.STATUS", fake_status),
            mock.patch("boss_timer_discord_bot.asyncio.sleep", new=mock.AsyncMock()),
            mock.patch("boss_timer_discord_bot.time.monotonic", return_value=6.0),
        ):
            asyncio.run(bot._gateway_recovery_loop())

        bot._connect_configured_voice_channel.assert_awaited_once_with()

    def test_voice_watchdog_reconnect_success_publishes_recovery_log(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.client = mock.Mock()
        bot.client.is_ready.return_value = True
        bot.voice_client = None
        bot.gateway_disconnected_at = None
        bot.last_voice_reconnect_attempt_at = 0.0
        bot._connect_configured_voice_channel = mock.AsyncMock(return_value=True)
        bot._publish_voice_reconnect_recovery_log = mock.AsyncMock()
        fake_status = mock.Mock()
        fake_status.shutdown_requested.is_set.side_effect = [False, True]

        with (
            mock.patch("boss_timer_discord_bot.STATUS", fake_status),
            mock.patch("boss_timer_discord_bot.asyncio.sleep", new=mock.AsyncMock()),
            mock.patch("boss_timer_discord_bot.time.monotonic", return_value=6.0),
        ):
            asyncio.run(bot._gateway_recovery_loop())

        bot._connect_configured_voice_channel.assert_awaited_once_with()
        bot._publish_voice_reconnect_recovery_log.assert_awaited_once_with()

    def test_voice_reconnect_recovery_log_goes_to_discord_and_local_gui(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.config = {
            "server_id": "800",
            "voice_channel_id": "801",
            "text_channel_id": "802",
        }
        bot.client = mock.Mock()
        bot.client.user.id = 803
        channel = mock.Mock(id=802)
        channel.send = mock.AsyncMock()
        bot._resolve_text_channel = mock.AsyncMock(return_value=channel)
        bot._queue_local_schedule_request = mock.AsyncMock()
        embed = mock.Mock()
        bot.discord = mock.Mock()
        bot.discord.Embed.return_value = embed

        asyncio.run(bot._publish_voice_reconnect_recovery_log())

        channel.send.assert_awaited_once_with(embed=embed)
        bot._queue_local_schedule_request.assert_awaited_once()
        payload = bot._queue_local_schedule_request.await_args.args[0]
        self.assertEqual(payload["operation"], "connection_log")
        self.assertEqual(payload["server_id"], "800")
        self.assertIn("자동 재접속했습니다", payload["raw_text"])

    def test_gui_accepts_voice_connection_log_without_changing_schedule(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_server_id = "800"

        success, message = app._apply_discord_schedule_request({
            "operation": "connection_log",
            "server_id": "800",
            "raw_text": "음성 연결 자동 재접속",
        })

        self.assertTrue(success)
        self.assertIn("자동 재접속 완료", message)


class ScheduleAlarmOrderingTests(unittest.TestCase):
    def test_fixed_due_world_boss_uses_recorded_boss_and_time_clips(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_alarm_boss_voice_path = lambda *, boss_name="", **_kwargs: (
            "boss:월드보스.wav" if boss_name == "월드보스" else None
        )
        app._get_schedule_alarm_info_audio_path = lambda token: "info:타임입니다.wav" if token == "타임입니다" else None
        app._get_schedule_alarm_random_voice_path_by_prefix = lambda _folder, _token: None

        self.assertEqual(
            app._build_schedule_fixed_due_time_audio_paths("월드보스"),
            ["boss:월드보스.wav", "info:타임입니다.wav"],
        )

    def test_invasion_confirmed_bridge_does_not_overlap_short_lead_clips(self):
        app = object.__new__(BossTimerApp)
        captured = {}
        app._get_schedule_alarm_voice_duration_ms = lambda path: {
            "chime.wav": 1000,
            "invasion.wav": 500,
            "boss.wav": 600,
            "gen.wav": 700,
        }[path]
        app._append_discord_voice_bridge_request = lambda **kwargs: captured.update(kwargs) or True
        target_at = datetime.now() + timedelta(seconds=10)

        self.assertTrue(app._append_discord_second_precision_spawn_bridge_request(
            target_time=target_at,
            lead_clip_paths=["chime.wav", "invasion.wav", "boss.wav"],
            gen_clip_path="gen.wav",
        ))

        timed = captured["timed_clip_paths"]
        self.assertEqual(timed[1][0] - timed[0][0], timedelta(milliseconds=1000))
        self.assertEqual(timed[2][0] - timed[1][0], timedelta(milliseconds=500))
        self.assertEqual(timed[-1], (target_at, "gen.wav"))

    def test_delayed_fixed_group_switches_to_soon_after_chime_enters_40_seconds(self):
        app = object.__new__(BossTimerApp)
        target_at = datetime(2026, 9, 8, 18, 1, 0)
        app._get_schedule_reference_datetime = lambda: target_at - timedelta(seconds=41)
        app._is_schedule_alarm_chime_clip_path = lambda path: path == "fixed-chime.wav"
        app._get_schedule_alarm_voice_duration_ms = lambda path: 1973 if path == "fixed-chime.wav" else 700
        app._summarize_schedule_alarm_group_names = lambda names: (" ".join(names), max(0, len(names) - 1))
        app._get_schedule_alarm_info_audio_path = lambda token: f"info:{token}"
        app._get_schedule_alarm_random_voice_path_by_prefix = lambda _folder, token: f"info:{token}"
        app._get_schedule_alarm_boss_voice_path = lambda *, boss_name="", **_kwargs: f"boss:{boss_name}"
        app._with_schedule_alarm_chime_paths = lambda paths, _category: ["fixed-chime.wav", *paths]
        app._write_schedule_alarm_voice_test_log = mock.Mock()
        request = {
            "target_time": target_at,
            "offset_sec": 60,
            "countdown_enabled_at_submit": True,
            "recording_preferred": True,
            "boss_id": "지옥성채 정예|핏빛고블린",
            "fixed_group_names": ["지옥성채 정예", "핏빛고블린"],
            "fallback_text": "지옥성채 정예 핏빛고블린 일분 전입니다.",
            "clip_paths": ["fixed-chime.wav", "old.wav"],
            "chime_key": "fixed",
        }

        self.assertTrue(app._adjust_schedule_fixed_voice_request_for_playback(request))

        self.assertEqual(request["fallback_text"], "곧 지옥성채 정예 핏빛고블린 타임입니다.")
        self.assertEqual(request["clip_paths"], [
            "fixed-chime.wav", "info:곧", "boss:지옥성채 정예", "boss:핏빛고블린", "info:타임입니다",
        ])

    def test_delayed_fixed_alert_keeps_one_minute_message_without_countdown(self):
        app = object.__new__(BossTimerApp)
        target_at = datetime(2026, 9, 8, 18, 1, 0)
        app._get_schedule_reference_datetime = lambda: target_at - timedelta(seconds=30)
        request = {
            "target_time": target_at,
            "offset_sec": 60,
            "countdown_enabled_at_submit": False,
            "recording_preferred": True,
            "boss_id": "지옥성채 정예",
            "fallback_text": "지옥성채 정예 1분 전입니다.",
            "clip_paths": ["fixed-chime.wav", "boss:지옥성채 정예", "info:1분전"],
        }

        self.assertTrue(app._adjust_schedule_fixed_voice_request_for_playback(request))
        self.assertEqual(request["fallback_text"], "지옥성채 정예 1분 전입니다.")
        self.assertEqual(request["clip_paths"], ["fixed-chime.wav", "boss:지옥성채 정예", "info:1분전"])

    def test_exact_time_clusters_do_not_consume_next_minute_group(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_boss_display_name = lambda item, **_kwargs: item["name"]
        app._get_schedule_alarm_event_identity = lambda item: item["name"]
        base_at = datetime(2026, 9, 7, 18, 0, 0)
        items = [
            {"name": "라이노르", "scheduled_at": base_at},
            {"name": "브륀힐드", "scheduled_at": base_at},
            {"name": "니드호그", "scheduled_at": base_at + timedelta(minutes=1)},
            {"name": "셀로비아", "scheduled_at": base_at + timedelta(minutes=1)},
        ]

        groups = app._build_schedule_alarm_event_clusters(items, max_gap_seconds=0)

        self.assertEqual([[item["name"] for item in group] for group in groups], [
            ["라이노르", "브륀힐드"],
            ["니드호그", "셀로비아"],
        ])

    def test_pre_alert_collision_waits_behind_current_spawn(self):
        app = object.__new__(BossTimerApp)
        current_spawn_at = datetime(2026, 9, 7, 18, 0, 0)
        next_spawn_at = current_spawn_at + timedelta(minutes=1)
        runtime_events = [{"scheduled_at": current_spawn_at}]

        release_at = app._get_schedule_pre_alert_spawn_collision_release_at(
            runtime_events,
            next_spawn_at,
            60,
        )

        self.assertEqual(release_at, current_spawn_at + timedelta(milliseconds=250))

    def test_pre_alert_expiration_follows_delayed_barrier(self):
        app = object.__new__(BossTimerApp)
        app.schedule_voice_broker_generation = 3
        target_at = datetime(2026, 9, 7, 18, 1, 0)
        request = {
            "phase": "PRE_ALERT",
            "target_time": target_at,
            "offset_sec": 60,
            "generation": 3,
            "earliest_play_at": target_at + timedelta(seconds=30),
        }

        self.assertFalse(app._schedule_voice_broker_request_is_stale(
            request,
            target_at + timedelta(seconds=49),
        ))
        self.assertTrue(app._schedule_voice_broker_request_is_stale(
            request,
            target_at + timedelta(seconds=51),
        ))

    def test_second_precision_same_time_group_submits_once(self):
        app = object.__new__(BossTimerApp)
        now = datetime(2026, 9, 7, 18, 0, 0)
        target_at = now + timedelta(seconds=2)
        items = [
            {"name": "니드호그", "boss_name": "니드호그", "scheduled_at": target_at, "invasion": False},
            {"name": "셀로비아", "boss_name": "셀로비아", "scheduled_at": target_at, "invasion": False},
            {"name": "침공 라타토스크", "boss_name": "라타토스크", "scheduled_at": target_at, "invasion": True},
        ]
        app.schedule_alarm_second_precision_gen_pending_keys = set()
        app._has_active_main_schedule_countdown_window = lambda _now: False
        app._iter_schedule_alarm_events_between = lambda *_args: [
            (item, item["scheduled_at"]) for item in items
        ]
        app._is_schedule_second_precision = lambda _item: True
        app._is_schedule_alarm_event_visible = lambda *_args: True
        app._is_schedule_invasion_item = lambda item: bool(item["invasion"])
        app._is_schedule_voice_center_channel_active = lambda: False
        app._get_schedule_alarm_event_identity = lambda item: item["name"]
        app._build_schedule_alarm_event_group_identity = lambda group: "|".join(item["name"] for item in group)
        app._get_schedule_alarm_compact_group_context = lambda _group: (items[0], "니드호그 외 2개", 2, False)
        app._build_schedule_alarm_compact_group_audio_paths = lambda *_args, **_kwargs: ["boss.wav", "extra.wav", "count.wav", "gen.wav"]
        app._with_schedule_alarm_chime_paths = lambda paths, _category: ["chime.wav", *paths]
        app._get_schedule_alarm_second_precision_gen_lead_ms = lambda _paths: 1000
        app._build_schedule_alarm_due_key = lambda *_args: "group-key"
        app._should_fire_schedule_alarm_key = lambda *_args: True
        app._append_debug_log = lambda *_args: None
        app._write_schedule_alarm_voice_test_log = lambda *_args, **_kwargs: None
        app._get_schedule_alarm_group_display_names = lambda group: [item["name"] for item in group]
        app._get_schedule_invasion_voice_route = lambda **_kwargs: ("right", 0.8)
        app._submit_schedule_voice_request = mock.Mock()

        app._process_schedule_second_precision_gen_notice_tick(
            now,
            now,
            None,
            countdown_enabled=False,
        )

        app._submit_schedule_voice_request.assert_called_once()
        submitted = app._submit_schedule_voice_request.call_args.kwargs
        self.assertEqual(submitted["fallback_text"], "니드호그 외 2개 젠")
        self.assertEqual(submitted["lane"], "center")
        self.assertFalse(submitted["is_invasion"])

    def test_complex_voice_cases_enable_one_and_five_minute_alerts(self):
        app = object.__new__(BossTimerApp)
        now = datetime(2026, 9, 7, 18, 0, 0)
        app._get_schedule_reference_datetime = lambda: now
        app._get_schedule_alarm_voice_rule_rows = lambda: [(f"규칙{index}",) for index in range(24)]
        app._get_schedule_alarm_voice_test_general_boss_pool = lambda: []
        app._get_schedule_alarm_voice_test_fixed_boss_pool = lambda: []
        app._normalize_schedule_alarm_offsets = lambda values: sorted({int(value) for value in values}, reverse=True)
        app._normalize_schedule_event_items = lambda items: items

        minute_case = app._build_schedule_alarm_voice_test_cases(1, selected_rule_index=9)[0]
        second_case = app._build_schedule_alarm_voice_test_cases(1, selected_rule_index=10)[0]

        self.assertEqual(minute_case["normal_offsets"], [300, 60])
        self.assertEqual(second_case["normal_offsets"], [300, 60])

    def test_9_3_voice_case_uses_requested_second_precision_rows(self):
        app = object.__new__(BossTimerApp)
        now = datetime(2026, 9, 7, 18, 0, 0)
        app._get_schedule_reference_datetime = lambda: now
        app._get_schedule_alarm_voice_rule_rows = lambda: [(f"규칙{index}",) for index in range(25)]
        app._get_schedule_alarm_voice_test_general_boss_pool = lambda: []
        app._get_schedule_alarm_voice_test_fixed_boss_pool = lambda: []
        app._normalize_schedule_alarm_offsets = lambda values: sorted({int(value) for value in values}, reverse=True)
        app._normalize_schedule_event_items = lambda items: items

        case = app._build_schedule_alarm_voice_test_cases(1, selected_rule_index=11)[0]

        self.assertEqual(case["normal_offsets"], [60])
        self.assertEqual([event["boss_name"] for event in case["events"]], [
            "라이노르", "브륀힐드", "니드호그", "셀로비아", "라타토스크", "페티",
        ])
        self.assertTrue(all(event["precision"] == "second" for event in case["events"]))

    def test_9_4_voice_case_contains_nearby_second_precision_rows(self):
        app = object.__new__(BossTimerApp)
        now = datetime(2026, 9, 7, 18, 0, 0)
        app._get_schedule_reference_datetime = lambda: now
        app._get_schedule_alarm_voice_rule_rows = lambda: [(f"규칙{index}",) for index in range(26)]
        app._get_schedule_alarm_voice_test_general_boss_pool = lambda: []
        app._get_schedule_alarm_voice_test_fixed_boss_pool = lambda: []
        app._normalize_schedule_alarm_offsets = lambda values: sorted({int(value) for value in values}, reverse=True)
        app._normalize_schedule_event_items = lambda items: items

        case = app._build_schedule_alarm_voice_test_cases(1, selected_rule_index=12)[0]
        offsets = [
            int((event["scheduled_at"] - case["target_at"]).total_seconds())
            for event in case["events"]
        ]

        self.assertEqual(case["normal_offsets"], [60])
        self.assertEqual(offsets, [0, 2, 60, 60, 60, 61])
        self.assertTrue(all(event["precision"] == "second" for event in case["events"]))

    def test_second_precision_two_second_spawns_share_one_chime_sequence(self):
        app = object.__new__(BossTimerApp)
        now = datetime(2026, 9, 7, 18, 0, 0)
        events = [
            ({"boss_name": "라이노르"}, now + timedelta(seconds=6)),
            ({"boss_name": "브륀힐드"}, now + timedelta(seconds=8)),
        ]
        submitted = []
        app.schedule_alarm_second_precision_gen_pending_keys = set()
        app._iter_schedule_alarm_events_between = lambda *_args: events
        app._is_schedule_second_precision = lambda _item: True
        app._is_schedule_alarm_event_visible = lambda *_args: True
        app._is_schedule_invasion_item = lambda _item: False
        app._is_schedule_voice_center_channel_active = lambda: False
        app._has_active_main_schedule_countdown_window = lambda _current: False
        app._get_schedule_alarm_event_identity = lambda item: item["boss_name"]
        app._build_schedule_alarm_event_group_identity = lambda items: "+".join(item["boss_name"] for item in items)
        app._get_schedule_alarm_compact_group_context = lambda items: (
            items[0],
            " ".join(item["boss_name"] for item in items),
            0,
            False,
        )
        app._build_schedule_alarm_compact_group_audio_paths = lambda items, **_kwargs: [
            *(f"boss:{item['boss_name']}" for item in items), "info:젠"
        ]
        app._with_schedule_alarm_chime_paths = lambda paths, _category: ["chime", *paths]
        app._get_schedule_alarm_second_precision_gen_lead_ms = lambda _paths: 1000
        app._build_schedule_alarm_due_key = lambda *parts: ":".join(map(str, parts))
        app._should_fire_schedule_alarm_key = lambda *_args: True
        app._append_debug_log = lambda *_args, **_kwargs: None
        app._write_schedule_alarm_voice_test_log = lambda *_args, **_kwargs: None
        app._get_schedule_alarm_group_display_names = lambda items: [item["boss_name"] for item in items]
        app._submit_schedule_voice_request = lambda **kwargs: submitted.append(kwargs)

        app._process_schedule_second_precision_gen_notice_tick(
            now + timedelta(seconds=5, milliseconds=500),
            now + timedelta(seconds=5),
            None,
            countdown_enabled=False,
        )
        # 첫 보스가 조회 창에서 빠진 뒤에도 뒤 보스만 새 단일 알림으로
        # 재등록되면 안 된다.
        app._process_schedule_second_precision_gen_notice_tick(
            now + timedelta(seconds=10, milliseconds=500),
            now + timedelta(seconds=10),
            None,
            countdown_enabled=False,
        )

        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["phase"], "SPAWN_CONFIRMED_NEAR_SEQUENCE")
        self.assertEqual(submitted[0]["fallback_text"], "라이노르 젠, 브륀힐드 젠")
        self.assertEqual(submitted[0]["clip_paths"], [
            "chime", "boss:라이노르", "info:젠", "boss:브륀힐드", "info:젠",
        ])

    def test_second_precision_one_second_spawns_share_one_gen(self):
        app = object.__new__(BossTimerApp)
        now = datetime(2026, 9, 7, 18, 0, 0)
        events = [
            ({"boss_name": "라이노르"}, now + timedelta(seconds=6)),
            ({"boss_name": "브륀힐드"}, now + timedelta(seconds=7)),
        ]
        submitted = []
        app.schedule_alarm_second_precision_gen_pending_keys = set()
        app._iter_schedule_alarm_events_between = lambda *_args: events
        app._is_schedule_second_precision = lambda _item: True
        app._is_schedule_alarm_event_visible = lambda *_args: True
        app._is_schedule_invasion_item = lambda _item: False
        app._is_schedule_voice_center_channel_active = lambda: False
        app._has_active_main_schedule_countdown_window = lambda _current: False
        app._get_schedule_alarm_event_identity = lambda item: item["boss_name"]
        app._build_schedule_alarm_event_group_identity = lambda items: "+".join(item["boss_name"] for item in items)
        app._get_schedule_alarm_compact_group_context = lambda items: (
            items[0],
            " ".join(item["boss_name"] for item in items),
            0,
            False,
        )
        app._build_schedule_alarm_compact_group_audio_paths = lambda items, **_kwargs: [
            *(f"boss:{item['boss_name']}" for item in items), "info:젠"
        ]
        app._with_schedule_alarm_chime_paths = lambda paths, _category: ["chime", *paths]
        app._get_schedule_alarm_second_precision_gen_lead_ms = lambda _paths: 1000
        app._build_schedule_alarm_due_key = lambda *parts: ":".join(map(str, parts))
        app._should_fire_schedule_alarm_key = lambda *_args: True
        app._append_debug_log = lambda *_args, **_kwargs: None
        app._write_schedule_alarm_voice_test_log = lambda *_args, **_kwargs: None
        app._get_schedule_alarm_group_display_names = lambda items: [item["boss_name"] for item in items]
        app._submit_schedule_voice_request = lambda **kwargs: submitted.append(kwargs)

        app._process_schedule_second_precision_gen_notice_tick(
            now + timedelta(seconds=5, milliseconds=500),
            now + timedelta(seconds=5),
            None,
            countdown_enabled=False,
        )

        self.assertEqual(len(submitted), 1)
        self.assertEqual(submitted[0]["phase"], "SPAWN_CONFIRMED")
        self.assertEqual(submitted[0]["fallback_text"], "라이노르 브륀힐드 젠")
        self.assertEqual(submitted[0]["clip_paths"], [
            "chime", "boss:라이노르", "boss:브륀힐드", "info:젠",
        ])

    def test_countdown_followup_is_owned_by_primary_stream_through_ten_seconds(self):
        app = object.__new__(BossTimerApp)
        current_at = datetime(2026, 9, 7, 18, 0, 0)
        app._is_schedule_invasion_item = lambda _item: False
        countdown_items = [{"item": {}, "scheduled_at": current_at}]

        self.assertTrue(
            app._is_schedule_near_countdown_followup_target(
                current_at + timedelta(seconds=1),
                countdown_items,
            )
        )
        self.assertTrue(
            app._is_schedule_near_countdown_followup_target(
                current_at + timedelta(seconds=10),
                countdown_items,
            )
        )
        self.assertFalse(
            app._is_schedule_near_countdown_followup_target(
                current_at + timedelta(seconds=11),
                countdown_items,
            )
        )

    def test_countdown_followup_chain_uses_adjacent_ten_second_gap(self):
        app = object.__new__(BossTimerApp)
        primary_at = datetime(2026, 9, 8, 18, 0, 0)
        app._is_schedule_invasion_item = lambda _item: False
        app._is_schedule_alarm_ai_recording_preferred = lambda: True
        app._get_schedule_alarm_countdown_audio_paths = lambda second: [f"sec:{second}"]
        app._get_schedule_alarm_countdown_completion_audio_paths = lambda: ["info:젠"]
        app._get_schedule_alarm_boss_voice_path = lambda *, item=None, **_kwargs: f"boss:{item['boss_name']}"
        app._get_schedule_alarm_clip_sequence_duration_ms = lambda paths: len(paths) * 500
        app._get_schedule_alarm_voice_duration_ms = lambda _path: 500
        countdown_items = [
            {"item": {"boss_name": "니드호그"}, "boss_name": "니드호그", "scheduled_at": primary_at + timedelta(seconds=3)},
            {"item": {"boss_name": "셀로비아"}, "boss_name": "셀로비아", "scheduled_at": primary_at + timedelta(seconds=13)},
            {"item": {"boss_name": "라타토스크"}, "boss_name": "라타토스크", "scheduled_at": primary_at + timedelta(seconds=24)},
        ]

        timed, claimed = app._build_discord_near_countdown_followup_timed_clips(
            scheduled_at=primary_at,
            countdown_alarm_items=countdown_items,
        )

        self.assertTrue(any(path == "boss:니드호그" for _at, path in timed))
        self.assertTrue(any(path == "boss:셀로비아" for _at, path in timed))
        self.assertFalse(any(path == "boss:라타토스크" for _at, path in timed))
        self.assertEqual(len(claimed), 2)

    def test_countdown_start_notice_deduplicates_same_target_with_changed_group_identity(self):
        app = object.__new__(BossTimerApp)
        scheduled_at = datetime.now() + timedelta(seconds=20)
        notice_at = scheduled_at - timedelta(seconds=20)
        existing_key = (
            f"{notice_at.isoformat(timespec='seconds')}|"
            f"{scheduled_at.isoformat(timespec='seconds')}|라이노르+브륀힐드"
        )
        app.discord_countdown_start_notice_bridge_keys = {existing_key}

        emitted = app._append_discord_countdown_start_notice_bridge_request(
            scheduled_at=scheduled_at,
            countdown_start_notice_seconds=20,
            countdown_start_seconds=15,
            group_identity="라이노르",
            display_text="라이노르",
            clip_paths=["unused.wav"],
        )

        self.assertTrue(emitted)
        self.assertEqual(app.discord_countdown_start_notice_bridge_keys, {existing_key})

    def test_online_discord_countdown_skips_temporary_local_name_route(self):
        app = object.__new__(BossTimerApp)
        app._should_mute_local_schedule_audio_for_discord_bot = lambda _allow: True
        app._has_discord_countdown_sequence_bridge_for_target = lambda _target: True
        app._write_schedule_alarm_voice_test_log = mock.Mock()
        app._submit_schedule_voice_request = mock.Mock()

        app._schedule_temp_local_side_countdown_preannounces(
            scheduled_at=datetime(2026, 9, 7, 18, 0, 0),
            countdown_alarm_items=[],
        )

        app._submit_schedule_voice_request.assert_not_called()

    def test_near_confirmed_sequence_uses_discord_transition_timeline(self):
        app = object.__new__(BossTimerApp)
        durations = {"long.wav": 1000, "short.wav": 800, "gen.wav": 700}
        app._get_schedule_alarm_voice_duration_ms = lambda path: durations[path]

        lead_ms = app._get_schedule_alarm_timed_sequence_lead_ms(
            ["long.wav", "short.wav", "gen.wav"]
        )

        # Discord's timed builder uses 180ms trim for long clips and 40ms for
        # short clips; the final gen starts on the target time.
        self.assertEqual(lead_ms, (1000 - 180) + (800 - 40))

    def test_discord_near_spawn_timeline_has_one_early_chime_and_each_gen_at_target(self):
        app = object.__new__(BossTimerApp)
        durations = {"chime.wav": 1000, "first.wav": 540, "second.wav": 594, "gen.wav": 767}
        app._get_schedule_alarm_voice_duration_ms = lambda path: durations[path]
        first_at = datetime(2026, 9, 7, 18, 0, 0)
        timed = app._build_discord_near_confirmed_spawn_timed_clip_paths(
            [
                (first_at, ["first.wav", "gen.wav"]),
                (first_at + timedelta(seconds=2), ["second.wav", "gen.wav"]),
            ],
            chime_paths=["chime.wav"],
        )

        self.assertEqual(timed[0], (first_at - timedelta(seconds=3), "chime.wav"))
        self.assertIn((first_at, "gen.wav"), timed)
        self.assertIn((first_at + timedelta(seconds=2), "gen.wav"), timed)
        self.assertEqual(sum(1 for _play_at, path in timed if path == "chime.wav"), 1)

    def test_two_boss_group_keeps_both_names_without_extra_count(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_boss_display_name = lambda item, **_kwargs: item["name"]
        app._is_schedule_alarm_audio_invasion = lambda *, item=None, **_kwargs: bool(item.get("invasion"))
        app._get_schedule_alarm_random_voice_path_by_prefix = lambda _dir, token: f"info:{token}"
        app._get_schedule_alarm_info_audio_path = lambda token: f"info:{token}"
        app._get_schedule_alarm_boss_voice_path = lambda *, item=None, **_kwargs: f"boss:{item['name']}"
        items = [
            {"name": "라이노르", "invasion": False},
            {"name": "브륀힐드", "invasion": False},
        ]

        _primary, summary, additional_count, all_invasion = app._get_schedule_alarm_compact_group_context(items)
        paths = app._build_schedule_alarm_compact_group_audio_paths(items, suffix_info_tokens=["젠"])

        self.assertEqual(summary, "라이노르 브륀힐드")
        self.assertEqual(additional_count, 0)
        self.assertFalse(all_invasion)
        self.assertEqual(paths, ["boss:라이노르", "boss:브륀힐드", "info:젠"])

    def test_same_time_fixed_bosses_form_one_pre_alert_group(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_fixed_boss_alarm_entry = lambda _name: {
            "enabled": True,
            "offsets": [60],
        }
        app._normalize_schedule_alarm_offsets = lambda values: list(values)
        scheduled_at = datetime(2026, 9, 2, 19, 0, 0)
        rows = [
            (scheduled_at, "지옥성채 정예", False),
            (scheduled_at, "핏빛고블린", False),
        ]

        groups = app._build_schedule_fixed_pre_alert_groups(rows)

        self.assertEqual(
            groups[(scheduled_at, 60, False)],
            ["지옥성채 정예", "핏빛고블린"],
        )

    def test_same_time_fixed_group_message_has_one_followup(self):
        app = object.__new__(BossTimerApp)
        scheduled_at = datetime(2026, 9, 2, 19, 0, 0)
        app._get_next_schedule_alarm_target_after = lambda *_args: (
            scheduled_at + timedelta(minutes=20),
            "드라우그",
        )

        message = app._build_schedule_fixed_alarm_group_message(
            scheduled_at - timedelta(minutes=1),
            scheduled_at,
            ["지옥성채 정예", "핏빛고블린"],
            60,
        )

        self.assertEqual(
            message,
            "지옥성채 정예 외 1개 1분 전입니다. 다음 보스는 20분 후 드라우그입니다.",
        )
        self.assertEqual(message.count("다음 보스는"), 1)

    def test_mixed_due_time_group_uses_one_representative_and_one_invasion_prefix(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_boss_display_name = lambda item, **_kwargs: str(item["name"])
        app._is_schedule_alarm_audio_invasion = lambda *, item=None, **_kwargs: bool(item.get("invasion"))
        app._get_schedule_alarm_random_voice_path_by_prefix = lambda _dir, token: f"info:{token}"
        app._get_schedule_alarm_info_audio_path = lambda token: f"info:{token}"
        app._get_schedule_alarm_boss_voice_path = lambda *, item=None, **_kwargs: f"boss:{item['name']}"
        app._get_schedule_alarm_group_count_audio_path = lambda count: f"count:{count}"
        items = [
            {"name": "니드호그", "invasion": False},
            {"name": "셀로비아", "invasion": False},
            {"name": "침공 라타토스크", "invasion": True},
            {"name": "침공 비요른", "invasion": True},
            {"name": "침공 헤르모드", "invasion": True},
        ]

        _primary, summary, additional_count, all_invasion = app._get_schedule_alarm_compact_group_context(items)
        paths = app._build_schedule_alarm_due_time_group_audio_paths(items)

        self.assertEqual(summary, "니드호그 외 4개")
        self.assertEqual(additional_count, 4)
        self.assertFalse(all_invasion)
        self.assertEqual(
            paths,
            ["info:곧", "boss:니드호그", "info:외", "count:4", "info:타임입니다"],
        )

    def test_non_second_due_group_collects_forty_seconds_but_keeps_invasion_separate(self):
        app = object.__new__(BossTimerApp)
        app.schedule_alarm_fired_keys = {}
        started_at = datetime(2026, 9, 9, 12, 0, 0)
        candidates = [
            {"identity": "normal:a", "scheduled_at": started_at, "display_name": "라이노르", "is_invasion": False},
            {"identity": "normal:b", "scheduled_at": started_at + timedelta(seconds=25), "display_name": "브륀힐드", "is_invasion": False},
            {"identity": "normal:c", "scheduled_at": started_at + timedelta(seconds=40), "display_name": "니드호그", "is_invasion": False},
            {"identity": "normal:d", "scheduled_at": started_at + timedelta(seconds=41), "display_name": "셀로비아", "is_invasion": False},
            # 35초 뒤의 브륀힐드를 기준으로 보면 가깝지만, 대표 보스
            # 라이노르를 기준으로는 70초라서 절대 이 그룹에 들어오면 안 된다.
            {"identity": "normal:e", "scheduled_at": started_at + timedelta(seconds=70), "display_name": "라타토스크", "is_invasion": False},
            {"identity": "invasion:f", "scheduled_at": started_at + timedelta(seconds=10), "display_name": "침공 비요른", "is_invasion": True},
        ]

        grouped = app._get_schedule_alarm_non_second_due_group_entries(
            candidates[0],
            candidates,
            set(),
        )

        self.assertEqual(
            [entry["identity"] for entry in grouped],
            ["normal:a", "normal:b", "normal:c"],
        )

        # The group loop stores this per-member form after the first submit.
        # A later tick must not form a suffix group from the same entries.
        for entry in grouped:
            app.schedule_alarm_fired_keys[
                app._build_schedule_alarm_due_key(
                    "non_second_due_time_group",
                    entry["identity"],
                    entry["scheduled_at"],
                    0,
                )
            ] = started_at
        self.assertEqual(
            app._get_schedule_alarm_non_second_due_group_entries(
                candidates[0],
                candidates,
                set(),
            ),
            [],
        )

    def test_non_second_three_boss_group_uses_primary_plus_extra_count(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_boss_display_name = lambda item, **_kwargs: str(item["display_name"])
        app._is_schedule_alarm_audio_invasion = lambda *, item=None, **_kwargs: bool(item.get("is_invasion"))
        items = [
            {"display_name": "라이노르", "is_invasion": False},
            {"display_name": "브륀힐드", "is_invasion": False},
            {"display_name": "니드호그", "is_invasion": False},
        ]

        _primary, summary, additional_count, all_invasion = app._get_schedule_alarm_compact_group_context(items)

        self.assertEqual(summary, "라이노르 외 2개")
        self.assertEqual(additional_count, 2)
        self.assertFalse(all_invasion)

    def test_group_count_uses_many_clip_for_ten_or_more_additional_bosses(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_alarm_voice_file_by_stem = mock.Mock(return_value="count:many")

        result = app._get_schedule_alarm_group_count_audio_path(10)

        self.assertEqual(result, "count:many")
        app._get_schedule_alarm_voice_file_by_stem.assert_called_once_with("count", "다수")

    def test_valhalla_fixed_row_adds_end_notice_target_twenty_minutes_later(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_fixed_boss_alarm_entry = lambda _name: {
            "enabled": True,
            "offsets": [60],
        }
        app._normalize_schedule_alarm_offsets = lambda values: list(values)
        started_at = datetime(2026, 9, 2, 18, 0, 0)

        rows = app._build_schedule_fixed_pre_alert_rows(
            [(started_at, "발할라 대전", "고정", "-", "-", "고정보스", "#FFFFFF", "#09A2A6")]
        )

        self.assertEqual(
            rows,
            [
                (started_at, "발할라 대전", False),
                (started_at + timedelta(minutes=20), "발할라 대전", True),
            ],
        )

    def test_same_time_general_rows_sort_before_fixed_rows(self):
        app = object.__new__(BossTimerApp)
        scheduled_at = datetime(2026, 9, 2, 18, 0, 0)
        fixed_row = (scheduled_at, "발할라 대전", "", "", "", "", "", "", "fixed", {}, {})
        general_row = (scheduled_at, "우로보로스", "", "", "", "", "", "", "event", {}, {})

        sorted_rows = sorted([fixed_row, general_row], key=app._get_schedule_visible_row_sort_key)

        self.assertEqual([row[1] for row in sorted_rows], ["우로보로스", "발할라 대전"])

    def test_fixed_alert_waits_for_same_time_general_pre_alert(self):
        app = object.__new__(BossTimerApp)
        scheduled_at = datetime(2026, 9, 2, 18, 0, 0)
        runtime_events = [
            {
                "enabled": True,
                "scheduled_at": scheduled_at,
                "offsets": [300, 60],
            }
        ]

        block_until = app._get_schedule_fixed_alert_general_block_until(
            runtime_events,
            scheduled_at,
            60,
        )

        self.assertEqual(block_until, datetime(2026, 9, 2, 17, 59, 2))
        self.assertEqual(
            app._get_schedule_fixed_alert_general_block_until(
                runtime_events,
                scheduled_at,
                0,
            ),
            datetime(2026, 9, 2, 18, 0, 2),
        )
        self.assertIsNone(
            app._get_schedule_fixed_alert_general_block_until(
                runtime_events,
                scheduled_at + timedelta(minutes=2),
                60,
            )
        )

    def test_fixed_pre_alert_waits_for_near_non_second_due_group(self):
        app = object.__new__(BossTimerApp)
        fixed_scheduled_at = datetime(2026, 9, 9, 18, 1, 0)
        runtime_events = [
            {
                "enabled": True,
                "scheduled_at": datetime(2026, 9, 9, 18, 0, 8),
                "offsets": [300, 60],
                "second_precision": False,
            }
        ]

        block_until = app._get_schedule_fixed_alert_general_block_until(
            runtime_events,
            fixed_scheduled_at,
            60,
        )

        self.assertEqual(block_until, datetime(2026, 9, 9, 18, 0, 2))

    def test_valhalla_start_pre_alert_retries_canonical_recording_name(self):
        app = object.__new__(BossTimerApp)
        app._get_schedule_alarm_boss_audio_paths = mock.Mock(return_value=[])
        app._get_schedule_alarm_voice_file_by_stem = mock.Mock(return_value="voice/boss/발할라 대전.wav")
        app._get_schedule_alarm_offset_audio_path = mock.Mock(return_value="voice/min/1min01.wav")
        scheduled_at = datetime(2026, 9, 9, 18, 0, 0)

        paths = app._build_schedule_fixed_alarm_audio_paths(
            scheduled_at - timedelta(minutes=1),
            scheduled_at,
            "발할라대전",
            60,
        )

        self.assertEqual(paths, ["voice/boss/발할라 대전.wav", "voice/min/1min01.wav"])


class DiscordBotLifecycleTests(unittest.TestCase):
    def test_startup_sends_disconnect_only_session_even_without_status_runtime(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_startup_cleanup_failed = False
        app._query_discord_bot_status_port = mock.Mock(return_value={})
        app._stop_discord_bot_runtime_core = mock.Mock()
        app._run_discord_bot_disconnect_only = mock.Mock(return_value=True)
        app._append_debug_log = mock.Mock()

        cleaned = app._cleanup_stale_discord_bot_runtime_at_startup()

        self.assertTrue(cleaned)
        app._stop_discord_bot_runtime_core.assert_not_called()
        app._run_discord_bot_disconnect_only.assert_called_once_with()
        self.assertFalse(app.discord_bot_startup_cleanup_failed)

    def test_runtime_stop_requests_graceful_shutdown_before_force_cleanup(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_shutdown_in_progress = False
        app.discord_bot_process = None
        app.discord_bot_running = True
        app.discord_bot_last_status_payload = {}
        app.discord_bot_voice_bridge_online = True
        app._query_discord_bot_status_port = mock.Mock(return_value={"ok": True, "pid": 1234})
        app._request_discord_bot_shutdown = mock.Mock(return_value=True)
        app._wait_discord_bot_status_port_down = mock.Mock(return_value=True)
        app._terminate_discord_bot_status_process = mock.Mock()

        stopped = app._stop_discord_bot_runtime_core()

        self.assertTrue(stopped)
        app._request_discord_bot_shutdown.assert_called_once_with()
        app._terminate_discord_bot_status_process.assert_not_called()
        self.assertFalse(app.discord_bot_running)

    def test_failed_startup_cleanup_retries_and_restarts_after_three_seconds(self):
        app = object.__new__(BossTimerApp)
        app.discord_bot_startup_cleanup_failed = True
        app.discord_bot_last_status_payload = {"ok": True}
        app.schedule_status_var = mock.Mock()
        app.root = mock.Mock()
        app._is_discord_bot_toggle_locked = mock.Mock(return_value=False)
        app._refresh_discord_bot_status_ui = mock.Mock()
        app._is_discord_bot_process_alive = mock.Mock(return_value=False)
        app._set_discord_bot_toggle_locked = mock.Mock()
        app._stop_discord_bot_runtime_core = mock.Mock(return_value=True)
        app._start_discord_bot_runtime = mock.Mock(return_value=True)

        app._toggle_discord_bot_runtime()

        app._stop_discord_bot_runtime_core.assert_called_once()
        app.root.after.assert_called_once_with(3000, app._start_discord_bot_runtime)
        self.assertFalse(app.discord_bot_startup_cleanup_failed)


class DiscordVoiceCommandTests(unittest.TestCase):
    def test_media_lookup_prefers_wav_over_mp4_and_ignores_spacing(self):
        app = object.__new__(BossTimerApp)
        with tempfile.TemporaryDirectory() as temp_dir:
            command_dir = Path(temp_dir) / "user_voice"
            command_dir.mkdir()
            mp4_path = command_dir / "광역체크.MP4"
            wav_path = command_dir / "광역 체크.WAV"
            mp4_path.write_bytes(b"mp4")
            wav_path.write_bytes(b"wav")
            with mock.patch("boss_timer_gui.get_app_root", return_value=temp_dir):
                resolved = app._find_discord_voice_command_media("광역체크")

        self.assertEqual(resolved, str(wav_path.resolve()))

    def test_custom_voice_command_keeps_file_name_and_separate_tts_text(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.custom_voice_commands = {}
        with mock.patch("boss_timer_discord_bot.save_custom_discord_voice_commands"):
            added, _result, command_name, tts_text = bot._add_discord_voice_command(
                "축작업, 축 작업하세요"
            )

        self.assertTrue(added)
        self.assertEqual(command_name, "축작업")
        self.assertEqual(tts_text, "축 작업하세요")
        self.assertEqual(
            bot._resolve_discord_voice_command("축작업"),
            ("축작업", "축 작업하세요"),
        )

    def test_custom_voice_command_can_be_registered_as_file_only(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.custom_voice_commands = {}
        with mock.patch("boss_timer_discord_bot.save_custom_discord_voice_commands"):
            added, _result, command_name, tts_text = bot._add_discord_voice_command(
                "광역신호, ",
                allow_empty_tts=True,
            )

        self.assertTrue(added)
        self.assertEqual(command_name, "광역신호")
        self.assertEqual(tts_text, "")
        self.assertEqual(bot._resolve_discord_voice_command("광역신호"), ("광역신호", ""))

    def test_voice_command_menu_includes_builtin_and_server_custom_entries(self):
        bot = object.__new__(DiscordScheduleBot)
        bot.custom_voice_commands = {
            "파일전용": ("파일 전용", ""),
        }

        entries = bot._get_discord_voice_command_menu_entries()
        by_name = {name: tts_text for _key, name, tts_text in entries}

        self.assertIn("광역체크", by_name)
        self.assertEqual(by_name["파일 전용"], "")

    def test_cached_tts_voice_command_is_sent_through_discord_bridge(self):
        app = object.__new__(BossTimerApp)
        app.edge_tts_cache = mock.Mock(configured=True)
        app._find_discord_voice_command_media = mock.Mock(return_value="")
        app._get_edge_tts_cached_path = mock.Mock(return_value=r"C:\BossTimer\tts_캐쉬\command\x.mp3")
        app._append_discord_voice_bridge_request = mock.Mock(return_value=True)

        success, result = app._handle_discord_voice_command_request(
            {
                "voice_command": "집결지",
                "tts_text": "집결지 모여주세요.",
            },
            play=True,
        )

        self.assertTrue(success)
        self.assertIn("송출", result)
        app._append_discord_voice_bridge_request.assert_called_once_with(
            clip_paths=[r"C:\BossTimer\tts_캐쉬\command\x.mp3"],
            fallback_text="집결지 모여주세요.",
            phase="VOICE_COMMAND",
            category="command",
        )

    def test_new_voice_command_prefetches_tts_without_playing(self):
        app = object.__new__(BossTimerApp)
        app.edge_tts_cache = mock.Mock(configured=True)
        app._find_discord_voice_command_media = mock.Mock(return_value="")
        app._get_edge_tts_cached_path = mock.Mock(return_value=None)
        app._prefetch_edge_tts_text = mock.Mock(return_value=True)

        success, result = app._handle_discord_voice_command_request(
            {"voice_command": "바보", "tts_text": "바보"},
            play=False,
        )

        self.assertTrue(success)
        self.assertIn("생성을 시작", result)
        persistent_relpath = app._prefetch_edge_tts_text.call_args.kwargs["persistent_relpath"]
        self.assertTrue(persistent_relpath.startswith("command/"))
        self.assertTrue(persistent_relpath.endswith(".mp3"))

    def test_voice_command_does_not_echo_as_schedule_notice(self):
        bot = object.__new__(DiscordScheduleBot)
        bot._resolve_text_channel = mock.AsyncMock()
        job = VoiceBridgeJob(
            id="command-1",
            created_at=datetime.now(),
            phase="VOICE_COMMAND",
            category="command",
            lane="center",
            volume=1.0,
            clip_paths=("voice.wav",),
            fallback_text="광역 체크해주세요.",
        )

        asyncio.run(bot._send_voice_bridge_text_notice(job))

        bot._resolve_text_channel.assert_not_awaited()


class ServerProfileDiscordSettingsTests(unittest.TestCase):
    def test_discord_config_path_is_scoped_to_active_server_profile(self):
        app = BossTimerApp.__new__(BossTimerApp)
        app.schedule_server_profile_id = "odin9"
        app.current_season_no = "9"
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("boss_timer_gui.get_user_config_dir", return_value=temp_dir):
                config_path = app._get_discord_bot_config_storage_path()

        self.assertEqual(
            config_path,
            os.path.join(temp_dir, "server_profiles", "season_9", "odin9", "discord_bot.ini"),
        )

    def test_custom_voice_command_registry_is_scoped_to_active_server_profile(self):
        app = BossTimerApp.__new__(BossTimerApp)
        app.schedule_server_profile_id = "odin9"
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("boss_timer_gui.get_user_config_dir", return_value=temp_dir):
                registry_path = app._get_discord_voice_commands_storage_path()

        self.assertEqual(
            registry_path,
            os.path.join(temp_dir, "server_profiles", "season_unset", "odin9", "discord_voice_commands.json"),
        )

    def test_discord_settings_load_into_runtime_without_cross_profile_state(self):
        app = BossTimerApp.__new__(BossTimerApp)
        app._apply_discord_bot_settings_to_runtime(
            {
                "bot_token": "token-a",
                "application_id": "123456789012345678",
                "server_id": "111",
                "voice_channel_id": "222",
                "text_channel_id": "333",
                "invite_links": {"123456789012345678": "https://discord.com/oauth2/authorize?client_id=123456789012345678"},
                "mute_pc_audio_when_online": False,
            }
        )

        self.assertEqual(app.discord_bot_server_id, "111")
        self.assertEqual(app.discord_bot_voice_channel_id, "222")
        self.assertEqual(app.discord_bot_text_channel_id, "333")
        self.assertFalse(app.discord_bot_mute_pc_audio_when_online)


if __name__ == "__main__":
    unittest.main()
