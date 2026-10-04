"""Memory-only GitHub regression checks: no network, settings writes or builds."""
from copy import deepcopy
import io
import json
import threading
from types import SimpleNamespace as NS
import unittest
import urllib.error
from unittest.mock import Mock, patch

import discord_authority as authority
from discord_authority import AuthorityRecord, DiscordAuthority
from discord_connection_policy import AUTHORITY_SECONDS, CONTROL_PROTOCOL
from discord_query_routing import QueryLedger
from github_request_policy import GithubRequestGate, is_rate_limit_response
from boss_timer_gui import BossTimerApp


class Repository:
    def __init__(self):
        self.data = dict(schema=CONTROL_PROTOCOL, scope=dict(guild="100"), owner="local",
                         owner_runtime="runtime", generation=1, phase="active", members={})
        self.writes = []
        self.reads = 0
        self.record = AuthorityRecord(self.get, self.put, dict(guild="100"))

    def get(self, path):
        self.reads += 1
        return deepcopy(self.data), str(len(self.writes)), ""

    def put(self, path, data, **kwargs):
        self.writes.append((path, deepcopy(data), kwargs))
        self.data = deepcopy(data)
        return True, ""


class AuthorityWriteTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository()

    def test_unchanged_nested_record_never_writes(self):
        original = deepcopy(self.repo.data)
        self.repo.record.mutate(lambda data: data.update(members=deepcopy(data["members"])))
        self.assertEqual(self.repo.writes, [])
        self.assertEqual(self.repo.data, original)

    def test_real_change_still_writes_with_sha(self):
        result = self.repo.record.mutate(lambda data: data.update(phase="idle"))
        self.assertEqual(result["phase"], "idle")
        self.assertEqual(len(self.repo.writes), 1)
        self.assertEqual(self.repo.writes[0][2]["sha"], "0")

    def test_conflict_rereads_and_keeps_peer_updates(self):
        original_put = self.repo.record.put
        calls = []
        def put(path, data, **kwargs):
            calls.append(kwargs["sha"])
            if len(calls) == 1:
                self.repo.data["members"]["peer"] = dict(runtime="other")
                return False, "GitHub API error 409"
            return original_put(path, data, **kwargs)
        self.repo.record.put = put
        result = self.repo.record.mutate(lambda data: data.update(phase="idle"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["members"]["peer"]["runtime"], "other")

    def test_conflict_already_applied_does_not_write_again(self):
        def put(path, data, **kwargs):
            self.repo.data["phase"] = "idle"
            return False, "409"
        self.repo.record.put = Mock(side_effect=put)
        result = self.repo.record.mutate(lambda data: data.update(phase="idle"))
        self.assertEqual(result["phase"], "idle")
        self.repo.record.put.assert_called_once()

    def test_rate_limit_does_not_retry_mutation(self):
        self.repo.record.put = Mock(return_value=(False, "GitHub API error 403: rate limit"))
        with self.assertRaises(authority.AuthorityError):
            self.repo.record.mutate(lambda data: data.update(phase="idle"))
        self.repo.record.put.assert_called_once()


class PresenceWriteTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository()
        self.coordinator = object.__new__(DiscordAuthority)
        self.coordinator.record = self.repo.record
        self.coordinator.client = "local"
        self.coordinator.lock = threading.RLock()
        self.coordinator.app = NS(discord_bot_authority_control_channel_id="555")
        self.coordinator._assert_profile = Mock()
        self.status = dict(online=True, runtime_id="runtime", connected_at=100)
        self.metadata = dict(name="local", server="9", season="18")
        self.now = 1000
        self.wall = patch.object(authority.time, "time", side_effect=lambda: 1700000000 + self.now)
        self.wall.start()
        self.addCleanup(self.wall.stop)

    def presence(self, status=None):
        return self.coordinator._presence(deepcopy(self.repo.data),
            self.status if status is None else status, self.metadata)

    def test_hour_of_unchanged_status_never_refreshes_registration(self):
        for self.now in range(1000, 4600):
            self.presence()
        self.assertEqual(len(self.repo.writes), 1)

    def test_missing_status_cannot_erase_live_runtime(self):
        self.presence()
        original = deepcopy(self.repo.data)
        for self.now in range(1005, 1400, 5):
            self.presence({})
        self.assertEqual(len(self.repo.writes), 1)
        self.assertEqual(self.repo.data, original)

    def test_confirmed_offline_is_written_once_without_heartbeat(self):
        self.presence()
        for self.now in range(1005, 1400, 5):
            self.presence(dict(self.status, online=False))
        self.assertEqual(len(self.repo.writes), 2)
        self.assertFalse(self.repo.data["members"]["local"]["online"])

    def test_restarted_runtime_is_published_immediately(self):
        self.presence()
        self.presence(dict(self.status, runtime_id="new-runtime"))
        self.assertEqual(len(self.repo.writes), 2)

    def test_metadata_change_is_published_immediately(self):
        self.presence()
        self.metadata["name"] = "renamed"
        self.presence()
        self.assertEqual(self.repo.data["members"]["local"]["name"], "renamed")

    def test_remote_call_is_preserved_on_metadata_change(self):
        self.presence()
        self.repo.data["members"]["local"]["call"] = dict(id="request", state="pending")
        self.metadata["name"] = "renamed"
        self.presence()
        self.assertEqual(self.repo.data["members"]["local"]["call"]["id"], "request")

    def test_old_protocol_cannot_be_treated_as_live(self):
        self.repo.data["members"]["old"] = dict(online=True, protocol=2, seen=1700001000)
        self.presence()
        self.assertNotIn("old", AuthorityRecord.online_members(self.repo.data))

    def test_event_registration_is_not_a_timed_authority_lease(self):
        self.presence()
        self.assertIn("local", AuthorityRecord.online_members(self.repo.data, now=1800000000))
        self.assertEqual(AUTHORITY_SECONDS, 10)

    def test_different_control_channels_are_rejected_without_write(self):
        self.repo.data["control_channel"] = "different"
        with self.assertRaises(authority.AuthorityError):
            self.presence()
        self.assertEqual(self.repo.writes, [])


class QueryWriteTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository()
        self.repo.data = dict(schema=1, guild="100", requests={}, responder={}, routing_generation=0)
        self.ledger = QueryLedger(self.repo.record)

    def test_unchanged_preference_clear_does_not_commit(self):
        result = self.ledger.clear_responder(dict(client_id="other", runtime="other"), 0)
        self.assertEqual(result, {})
        self.assertEqual(self.repo.writes, [])

    def test_repeated_open_returns_existing_request_without_commit(self):
        self.ledger.open("123", "help", [])
        self.ledger.open("123", "help", [])
        self.assertEqual(len(self.repo.writes), 1)

    def test_early_advance_does_not_commit(self):
        self.ledger.open("123", "help", [dict(client_id="local", runtime="runtime")])
        self.ledger.advance("123", 0)
        self.assertEqual(len(self.repo.writes), 1)

    def test_real_preference_clear_still_commits(self):
        self.repo.data["responder"] = dict(client_id="local", runtime="runtime")
        self.ledger.clear_responder(dict(client_id="local", runtime="runtime"), 0)
        self.assertEqual(len(self.repo.writes), 1)
        self.assertEqual(self.repo.data["routing_generation"], 1)


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.gate = GithubRequestGate()
        self.settings = dict(owner="owner", repo="repo", branch="main", token="test-token")
        self.now = 100
        self.clock = patch("github_request_policy.time.monotonic", side_effect=lambda: self.now)
        self.wall = patch("github_request_policy.time.time", return_value=1000)
        self.clock.start(); self.wall.start()
        self.addCleanup(self.clock.stop); self.addCleanup(self.wall.stop)

    def test_secondary_write_limit_also_pauses_reads(self):
        self.gate.limited(self.settings, "test-token", "PUT", {"retry-after": "120"})
        self.assertEqual(self.gate.remaining(self.settings, "test-token", "PUT"), 120)
        self.assertEqual(self.gate.remaining(self.settings, "test-token", "GET"), 120)

    def test_repeated_secondary_limit_increases_wait_and_resets_after_quiet_hour(self):
        for expected in [60, 120, 240, 480, 900, 900]:
            self.gate.limited(self.settings, "test-token", "PUT", {})
            self.assertEqual(self.gate.remaining(self.settings, "test-token", "GET"), expected)
            self.now += expected + 1
        self.now += 3600
        self.gate.limited(self.settings, "test-token", "PUT", {})
        self.assertEqual(self.gate.remaining(self.settings, "test-token", "GET"), 60)

    def test_primary_limit_respects_reset_and_blocks_reads(self):
        self.gate.limited(self.settings, "test-token", "GET",
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1200"})
        self.assertEqual(self.gate.remaining(self.settings, "test-token", "GET"), 201)
        self.assertEqual(self.gate.remaining(self.settings, "test-token", "DELETE"), 201)

    def test_cooldown_expires_and_other_token_remains_available(self):
        self.gate.limited(self.settings, "test-token", "PUT", {})
        self.assertEqual(self.gate.remaining(self.settings, "other-token", "PUT"), 0)
        self.now += 61
        self.assertEqual(self.gate.remaining(self.settings, "test-token", "PUT"), 0)

    def test_shared_token_limit_applies_across_repositories(self):
        self.gate.limited(self.settings, "test-token", "PUT", {})
        self.assertEqual(self.gate.remaining(dict(self.settings, repo="other"), "test-token", "PUT"), 60)

    def test_malformed_retry_after_has_bounded_default(self):
        self.gate.limited(self.settings, "test-token", "PUT", {"Retry-After": "invalid"})
        self.assertEqual(self.gate.remaining(self.settings, "test-token", "PUT"), 60)

    def test_permission_error_is_distinct_from_rate_limit(self):
        self.assertFalse(is_rate_limit_response(403, {}, "Resource not accessible"))
        self.assertTrue(is_rate_limit_response(403, {}, "API rate limit exceeded"))
        self.assertTrue(is_rate_limit_response(429, {}, ""))

    def app(self):
        app = object.__new__(BossTimerApp)
        app._get_github_data_settings = lambda: self.settings
        app._github_data_contents_url = lambda path: "https://api.github.invalid/contents/" + path
        return app

    def test_limited_get_never_falls_back_to_anonymous(self):
        error = urllib.error.HTTPError("https://api.github.invalid", 403, "Forbidden",
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1200"},
            io.BytesIO(b'{"message":"API rate limit exceeded"}'))
        with patch("boss_timer_gui.GITHUB_REQUEST_GATE", self.gate), \
             patch("boss_timer_gui.urlopen_verified", side_effect=error) as request:
            success, _, message = self.app()._github_data_request("GET", "file")
            self.assertFalse(success)
            self.assertIn("403", message)
            request.assert_called_once()
            self.app()._github_data_request("GET", "file")
            request.assert_called_once()

    def test_limited_put_does_not_send_next_write(self):
        error = urllib.error.HTTPError("https://api.github.invalid", 429, "Too many requests",
            {"Retry-After": "90"}, io.BytesIO(b'{"message":"secondary rate limit"}'))
        with patch("boss_timer_gui.GITHUB_REQUEST_GATE", self.gate), \
             patch("boss_timer_gui.urlopen_verified", side_effect=error) as request:
            self.app()._github_data_request("PUT", "file", payload={})
            self.app()._github_data_request("DELETE", "other", payload={})
            self.app()._github_data_request("GET", "other")
            request.assert_called_once()

    def test_permission_get_can_still_read_public_file(self):
        error = urllib.error.HTTPError("https://api.github.invalid", 403, "Forbidden", {},
            io.BytesIO(b'{"message":"Resource not accessible"}'))
        with patch("boss_timer_gui.GITHUB_REQUEST_GATE", self.gate), \
             patch("boss_timer_gui.urlopen_verified", side_effect=[error, io.BytesIO(b'{"ok":true}')]) as request:
            success, payload, _ = self.app()._github_data_request("GET", "file")
            self.assertTrue(success)
            self.assertEqual(payload, {"ok": True})
            self.assertEqual(request.call_count, 2)
            self.assertNotIn("Authorization", request.call_args.args[0].headers)


class UploadWriteTests(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(BossTimerApp)
        self.app.current_season_no = 18
        self.entry = dict(id="9", name="9", schedule="data/schedules/9.json", bosses="data/bosses/9.json",
            config="data/config/9.json", notices="data/notices/9.json",
            scheduleVersion="2026.10.04.001", bossConfigVersion="2026.10.04.001", dataVersion="2026.10.04.001")
        self.schedule = dict(kind="schedule", dataVersion="2026.10.04.001",
            payload=dict(share_prefix="9", server_name="9", season_no=18, schedule_events=[]))
        self.bosses = dict(kind="boss_config", dataVersion="2026.10.04.001", payload=dict(boss_definitions=[]))
        self.entry["scheduleHash"] = self.app._get_github_schedule_content_hash(self.schedule)
        self.entry["bossConfigHash"] = self.app._get_github_boss_config_content_hash(self.bosses)
        self.index = dict(dataVersion="2026.10.04.001", servers=[deepcopy(self.entry)])
        self.files = {"data/server_index.json": self.index, self.entry["schedule"]: self.schedule,
                      self.entry["bosses"]: self.bosses}
        self.app._get_github_cached_item_for_entry = lambda entry: dict(scheduleDirty="1", bossConfigDirty="1",
            scheduleVersion="2026.10.04.002", bossConfigVersion="2026.10.04.002")
        self.app._load_github_local_schedule_payload = lambda entry: (None, "")
        self.app._get_github_import_meta_versions_for_entry = lambda entry: ("", "")
        self.app._build_github_schedule_payload = lambda version: deepcopy(self.schedule)
        self.app._build_github_boss_config_payload = lambda version: deepcopy(self.bosses)
        self.app._github_get_json_file = Mock(side_effect=lambda path: (deepcopy(self.files.get(path)), "sha-" + path, ""))
        self.app._github_put_json_file = Mock(return_value=(True, ""))
        self.app._set_github_cached_versions = Mock()
        self.app._update_github_import_meta = Mock()
        self.app._save_current_schedule_to_github_local_cache = Mock()
        self.app._save_schedule_state = Mock()
        self.app._get_app_version_for_data = lambda: "v5.5.1"

    def test_newer_local_versions_with_identical_content_do_not_write_any_file(self):
        success, _, _ = self.app._upload_current_schedule_to_github_data(self.entry)
        self.assertTrue(success)
        self.app._github_put_json_file.assert_not_called()
        self.app._set_github_cached_versions.assert_called_once()

    def test_actual_schedule_change_writes_only_schedule_and_index(self):
        self.app._build_github_schedule_payload = lambda version: dict(deepcopy(self.schedule),
            payload=dict(self.schedule["payload"], schedule_events=[dict(boss_name="boss", scheduled_at="2026-10-04T20:00:00")]))
        success, _, _ = self.app._upload_current_schedule_to_github_data(self.entry)
        self.assertTrue(success)
        self.assertEqual([c.args[0] for c in self.app._github_put_json_file.call_args_list],
                         [self.entry["schedule"], "data/server_index.json"])
        index = self.app._github_put_json_file.call_args_list[-1].args[1]
        self.assertEqual(index["servers"][0]["bossConfigVersion"], "2026.10.04.001")
        self.assertEqual(sum(c.args[0] == self.entry["schedule"] for c in self.app._github_get_json_file.call_args_list), 1)

    def test_unversioned_legacy_schedule_still_receives_version_and_index(self):
        self.index["servers"][0]["scheduleVersion"] = ""
        self.index["servers"][0]["dataVersion"] = ""
        self.schedule["dataVersion"] = ""
        success, _, _ = self.app._upload_current_schedule_to_github_data(self.entry)
        self.assertTrue(success)
        self.assertEqual([c.args[0] for c in self.app._github_put_json_file.call_args_list],
                         [self.entry["schedule"], "data/server_index.json"])
        self.assertEqual(self.app._github_put_json_file.call_args_list[0].args[1]["dataVersion"],
                         "2026.10.04.002")

    def test_actual_boss_change_writes_only_bosses_and_index(self):
        self.app._build_github_boss_config_payload = lambda version: dict(deepcopy(self.bosses),
            payload=dict(boss_definitions=[dict(name="boss")]))
        success, _, _ = self.app._upload_current_schedule_to_github_data(self.entry)
        self.assertTrue(success)
        self.assertEqual([c.args[0] for c in self.app._github_put_json_file.call_args_list],
                         [self.entry["bosses"], "data/server_index.json"])

    def test_remote_read_failure_cannot_commit_upload(self):
        self.app._github_get_json_file = Mock(side_effect=lambda path:
            (deepcopy(self.index), "sha", "") if path == "data/server_index.json" else (None, None, "read failed"))
        success, _, _ = self.app._upload_current_schedule_to_github_data(self.entry)
        self.assertFalse(success)
        self.app._github_put_json_file.assert_not_called()

    def test_remote_file_index_version_mismatch_cannot_be_overwritten(self):
        self.schedule["dataVersion"] = "2026.10.04.003"
        success, _, _ = self.app._upload_current_schedule_to_github_data(self.entry)
        self.assertFalse(success)
        self.app._github_put_json_file.assert_not_called()

    def test_partial_upload_retry_commits_only_index_for_already_saved_files(self):
        self.schedule["dataVersion"] = "2026.10.04.002"
        self.bosses["dataVersion"] = "2026.10.04.002"
        success, _, _ = self.app._upload_current_schedule_to_github_data(self.entry)
        self.assertTrue(success)
        self.assertEqual([c.args[0] for c in self.app._github_put_json_file.call_args_list],
                         ["data/server_index.json"])
        saved = self.app._github_put_json_file.call_args.args[1]["servers"][0]
        self.assertEqual(saved["scheduleVersion"], "2026.10.04.002")
        self.assertEqual(saved["bossConfigVersion"], "2026.10.04.002")

    def test_same_version_partial_file_with_different_content_is_not_overwritten(self):
        self.schedule["dataVersion"] = "2026.10.04.002"
        self.app._build_github_schedule_payload = lambda version: dict(deepcopy(self.schedule),
            payload=dict(self.schedule["payload"], schedule_events=[dict(boss_name="different")]))
        success, _, _ = self.app._upload_current_schedule_to_github_data(self.entry)
        self.assertFalse(success)
        self.app._github_put_json_file.assert_not_called()

    def test_equal_handover_snapshot_does_not_write_any_file(self):
        success, _, _ = self.app._upload_current_schedule_to_github_data(
            self.entry, handover_schedule_payload=deepcopy(self.schedule))
        self.assertTrue(success)
        self.app._github_put_json_file.assert_not_called()

    def test_expired_handover_cannot_commit_snapshot(self):
        snapshot = deepcopy(self.schedule)
        snapshot["payload"]["schedule_events"] = [dict(boss_name="boss")]
        success, _, _ = self.app._upload_current_schedule_to_github_data(
            self.entry, handover_schedule_payload=snapshot, handover_guard=lambda: False)
        self.assertFalse(success)
        self.app._github_put_json_file.assert_not_called()

    def test_remote_newer_version_cannot_be_overwritten(self):
        self.index["servers"][0]["scheduleVersion"] = "2026.10.04.003"
        success, message, _ = self.app._upload_current_schedule_to_github_data(self.entry)
        self.assertTrue(success)
        self.app._github_put_json_file.assert_not_called()

    def test_unchanged_server_management_does_not_write_index(self):
        self.app.schedule_github_server_entries = [self.entry]
        self.app._get_current_github_upload_server_entry = lambda: self.entry
        self.app._cache_github_server_entries_from_index = Mock()
        success, _, entries = self.app._apply_github_server_management_changes({"9": deepcopy(self.entry)})
        self.assertTrue(success)
        self.assertEqual(entries, [self.entry])
        self.app._github_put_json_file.assert_not_called()


if __name__ == "__main__":
    unittest.main()
