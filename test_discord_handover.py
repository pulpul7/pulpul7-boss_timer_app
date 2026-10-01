"""Offline two-administrator handover, optimistic writes and completion barrier."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from discord_connection_policy import ConnectionPolicy
from discord_handover import DiscordHandover, HandoverError, HandoverRecord


class Repository:
    def __init__(self):
        self.files = {}
        self.lock = threading.Lock()
        self.revision = 0

    def get(self, path):
        with self.lock:
            data, sha = self.files.get(path, (None, None))
            return deepcopy(data), sha, ""

    def put(self, path, payload, *, sha=None, message=""):
        with self.lock:
            if self.files.get(path, (None, None))[1] != sha:
                return False, "409 conflict"
            self.revision += 1
            self.files[path] = (deepcopy(payload), str(self.revision))
            return True, ""


class Progress:
    def __init__(self, *args):
        self.window = NS()
        self.closed = False
        self.rows = []

    def update(self, row):
        self.rows.append(deepcopy(row))

    def close(self):
        self.closed = True


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.repo = Repository()
        self.scope = dict(guild="100", server="odin9", season="18")
        self.record = HandoverRecord(self.repo.get, self.repo.put, self.scope)

    def active_owner(self):
        row = self.record.request("first")
        self.record.change(row["request"], "first", phases={"joining"}, phase="active")

    def test_scope_mismatch_is_rejected_even_for_same_discord_guild(self):
        self.active_owner()
        other = HandoverRecord(self.repo.get, self.repo.put, dict(self.scope, season="19"))
        with self.assertRaises(HandoverError):
            other.request("second")

    def test_previous_scope_requires_same_owner_shutdown_and_unchanged_record(self):
        self.active_owner()
        other = HandoverRecord(self.repo.get, self.repo.put, dict(self.scope, season="19"))
        with self.assertRaises(HandoverError):
            other.request('first')  # No implicit bypass, even for the same ID.
        _, sha = other.read(recovery_client='first', inspect_own_active=True)
        with self.assertRaises(HandoverError):
            other.release_previous_scope('first', sha)
        with self.assertRaises(HandoverError):
            other.release_previous_scope('second', sha, stopped=True)
        self.record.request('second')  # Another transfer began after inspection.
        with self.assertRaises(HandoverError):
            other.release_previous_scope('first', sha, stopped=True)
        self.assertEqual(self.record.read()[0]['receiver'], 'second')

    def test_previous_scope_release_does_not_import_old_artifact(self):
        self.active_owner()
        old, sha = self.record.read()
        old['artifact'] = {'path': 'completed-old-season-schedule'}
        self.record.write(old, sha)
        other = HandoverRecord(self.repo.get, self.repo.put, dict(self.scope, server='odin8', season='19'))
        _, sha = other.read(recovery_client='first', inspect_own_active=True)
        other.release_previous_scope('first', sha, stopped=True)
        row = other.request('first')
        self.assertEqual(row['phase'], 'joining')
        self.assertEqual(row['scope'], other.scope)
        self.assertIsNone(row['artifact'])

    def test_concurrent_request_and_stale_completion_cannot_overwrite(self):
        self.active_owner()
        row = self.record.request("second")
        with self.assertRaises(HandoverError):
            self.record.request("third")
        with self.assertRaises(HandoverError):
            self.record.change("old-request", "first", phases={"requested"}, phase="released")
        self.assertEqual(self.record.read()[0]["request"], row["request"])

    def test_failed_after_logout_is_resumed_only_by_intended_receiver(self):
        self.active_owner()
        row = self.record.request("second")
        self.record.change(row["request"], "first", phases={"requested"}, phase="failed", released=True,
                           artifact={"path": "data/schedules/odin9.json", "sha": "exact"})
        with self.assertRaises(HandoverError):
            self.record.request("third")
        retry = self.record.request("second")
        self.assertTrue(retry["released"])
        self.assertEqual(retry["artifact"]["sha"], "exact")
        self.assertNotEqual(row["request"], retry["request"])

    def test_contents_sha_is_required_for_record_update(self):
        self.active_owner()
        data, sha = self.record.read()
        self.record.request("second")
        with self.assertRaises(HandoverError):
            self.record.write(data, sha)

    def test_clean_logout_allows_a_fresh_connection_without_waiting(self):
        self.active_owner()
        data, sha = self.record.read()
        data.update(owner="", phase="idle")
        self.record.write(data, sha)
        new = self.record.request("second")
        self.assertEqual(new["phase"], "joining")
        self.assertTrue(new["released"])

    def test_expired_request_cannot_publish_success(self):
        row = self.record.request("first", now=0)
        with self.assertRaises(HandoverError):
            self.record.change(row["request"], "first", phases={"joining"}, phase="active")

    def test_cleanly_released_guild_can_begin_next_season(self):
        self.active_owner()
        data, sha = self.record.read()
        data.update(owner="", phase="idle")
        self.record.write(data, sha)
        other = HandoverRecord(self.repo.get, self.repo.put, dict(self.scope, season="19"))
        row = other.request("second")
        self.assertEqual(row["scope"]["season"], "19")
        self.assertEqual(row["phase"], "joining")
        self.assertIsNone(row["artifact"])

    def test_legacy_idle_record_never_reuses_expired_previous_season_artifact(self):
        self.active_owner()
        data, sha = self.record.read()
        data.update(owner='', phase='idle', deadline=0,
                    artifact={'path': 'old-season'}, receiver='first', released=True)
        self.record.write(data, sha)
        other = HandoverRecord(self.repo.get, self.repo.put, dict(self.scope, season='19'))
        row = other.request('first')
        self.assertIsNone(row['artifact'])

    def test_failed_solo_start_can_retry_after_scope_correction(self):
        row = self.record.request('first')
        self.record.change(row['request'], 'first', phases={'joining'}, phase='failed')
        corrected = HandoverRecord(self.repo.get, self.repo.put, dict(self.scope, server='odin8'))
        with self.assertRaises(HandoverError):
            corrected.read()  # Normal polling remains strict.
        retry = corrected.request('first')
        self.assertEqual(retry['scope']['server'], 'odin8')
        self.assertIsNone(retry['artifact'])
        self.assertEqual(retry['phase'], 'joining')

    def test_expired_solo_start_can_recover_but_live_join_cannot(self):
        self.record.request('first')
        corrected = HandoverRecord(self.repo.get, self.repo.put, dict(self.scope, season='19'))
        with self.assertRaises(HandoverError):
            corrected.request('first')
        data, sha = self.record.read()
        data['deadline'] = 0
        self.record.write(data, sha)
        self.assertEqual(corrected.request('first')['scope']['season'], '19')

    def test_cross_scope_recovery_never_steals_other_owner_or_artifact(self):
        row = self.record.request('first')
        self.record.change(row['request'], 'first', phases={'joining'}, phase='failed')
        corrected = HandoverRecord(self.repo.get, self.repo.put, dict(self.scope, season='19'))
        with self.assertRaises(HandoverError):
            corrected.request('second')
        data, sha = self.record.read()
        data['artifact'] = {'path': 'protected-schedule'}
        self.record.write(data, sha)
        with self.assertRaises(HandoverError):
            corrected.request('first')
        self.assertEqual(self.record.read()[0]['artifact'], {'path': 'protected-schedule'})


class HandoverTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="boss-handover-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = Repository()
        self.gates = []
        self.workers = []
        self.patchers = [patch("discord_handover.HandoverProgress", Progress),
                         patch("discord_handover.POLL_SECONDS", .01),
                         patch("discord_handover.HANDOVER_SECONDS", 3)]
        for item in self.patchers:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.cleanup_workers)

    def cleanup_workers(self):
        for gate in self.gates:
            gate.set()
        for worker in self.workers:
            worker.join(5)

    def app(self, name):
        app = NS(root=NS(after=Mock()), schedule_window=None, current_season_no="18", discord_bot_server_id="100",
                 schedule_status_var=Mock(), discord_bot_expected_running=False,
                 _show_centered_messagebox=Mock(), _append_debug_log=Mock())
        config = self.root / (name + ".ini")
        app._get_discord_bot_config_storage_path = lambda: str(config)
        from administrator_identity import AdministratorIdentity
        identity_store = AdministratorIdentity(self.root / name)
        app._get_administrator_identity = lambda: identity_store.load_or_create(config)
        app._get_current_github_upload_server_entry = lambda: dict(id="odin9", name="오9", schedule="data/schedules/odin9.json")
        app._github_get_json_file, app._github_put_json_file = self.repo.get, self.repo.put
        app.raw = dict(kind="schedule", dataVersion="1.2.3", payload=dict(season_no="18", share_prefix="odin9",
                       server_name="오9", schedule_events=[{"at": "2026-09-20T18:00:00.123456", "boss": name}]))
        app._build_github_schedule_payload = lambda version: deepcopy(app.raw)
        app._get_github_schedule_content_hash = lambda data: hashlib.sha256(json.dumps(data["payload"], sort_keys=True).encode()).hexdigest() if isinstance(data, dict) else ""
        app._unwrap_github_schedule_payload = lambda data: deepcopy(data["payload"])
        app._create_schedule_full_state_snapshot = lambda: deepcopy(app.raw["payload"])
        app._restore_schedule_full_state_snapshot = Mock()
        app._save_schedule_state = Mock()
        app._update_github_import_meta = Mock()
        app._apply_loaded_schedule_shared_payload = Mock(return_value=True)
        app.connected = False
        def start(**kwargs):
            ConnectionPolicy(config).resume()
            app.connected = True
            return True
        app._start_discord_bot_runtime = Mock(side_effect=start)
        app._query_discord_bot_status_port = lambda **kwargs: dict(online=app.connected, voice_connected=app.connected, guild_id="100")
        def stop():
            app.connected = False
            return True
        app._stop_discord_bot_runtime_core = Mock(side_effect=stop)
        def upload(entry, progress_callback):
            path = entry["schedule"]
            _, sha, _ = self.repo.get(path)
            self.repo.put(path, app.raw, sha=sha)
            progress_callback("업로드 완료")
            return True, "ok", entry
        app._upload_current_schedule_to_github_data = Mock(side_effect=upload)
        return app

    def coordinator(self, app):
        manager = DiscordHandover(app)
        manager._ui = lambda callback, wait=True: callback()
        return manager

    def test_same_admin_can_recover_previous_season_after_confirmed_shutdown(self):
        app = self.app('first')
        old = self.coordinator(app)
        request = old.record.request(old.client)
        old.record.change(request['request'], old.client, phases={'joining'}, phase='active')
        app.current_season_no = '19'
        app._show_centered_messagebox.return_value = True
        new = self.coordinator(app)
        new._show()
        new._incoming()
        final, _ = new.record.read()
        self.assertEqual(final['phase'], 'active')
        self.assertEqual(final['scope']['season'], '19')
        self.assertFalse(new.policy.snapshot()['handover_hold'])
        app._stop_discord_bot_runtime_core.assert_called_once()
        app._apply_loaded_schedule_shared_payload.assert_not_called()
        self.assertEqual(app._show_centered_messagebox.call_args.args[1], '이전 시즌 담당 기록 정리')

    def test_declined_or_failed_shutdown_keeps_previous_owner_record(self):
        for confirm, stopped in ((False, True), (True, False)):
            with self.subTest(confirm=confirm, stopped=stopped):
                self.repo = Repository()
                app = self.app('first')
                old = self.coordinator(app)
                request = old.record.request(old.client)
                old.record.change(request['request'], old.client, phases={'joining'}, phase='active')
                original, original_sha = old.record.read()
                app.current_season_no = '19'
                app._show_centered_messagebox.return_value = confirm
                app._stop_discord_bot_runtime_core.side_effect = None
                app._stop_discord_bot_runtime_core.return_value = stopped
                new = self.coordinator(app)
                new._show()
                new._incoming()
                self.assertEqual(old.record.read(), (original, original_sha))
                app._start_discord_bot_runtime.assert_not_called()
                self.assertTrue(new.policy.snapshot()['handover_hold'])

    def wait_for(self, condition):
        deadline = time.monotonic() + 5
        while not condition():
            if time.monotonic() > deadline:
                self.fail("condition timed out")
            time.sleep(.005)

    def begin(self, *, sync_gate=None, connect_gate=None, upload_fail=False):
        first, second = self.app("first"), self.app("second")
        old, new = self.coordinator(first), self.coordinator(second)
        active = old.record.request(old.client)
        old.record.change(active["request"], old.client, phases={"joining"}, phase="active")
        if sync_gate:
            self.gates.append(sync_gate)
            second._apply_loaded_schedule_shared_payload.side_effect = lambda *a, **kw: sync_gate.wait(4)
        if connect_gate:
            self.gates.append(connect_gate)
            second._query_discord_bot_status_port = lambda **kwargs: dict(online=True, voice_connected=connect_gate.is_set(), guild_id="100")
        if upload_fail:
            first._upload_current_schedule_to_github_data.side_effect = None
            first._upload_current_schedule_to_github_data.return_value = (False, "upload failed", None)
        new._show()
        thread = threading.Thread(target=new._incoming)
        self.workers.append(thread)
        thread.start()
        self.wait_for(lambda: old.record.read()[0]["phase"] == "requested")
        old.current = old.record.read()[0]
        old._show(outgoing=True)
        thread = threading.Thread(target=old._outgoing)
        self.workers.append(thread)
        thread.start()
        return old, new

    def test_connected_first_keeps_both_progress_windows_and_output_locked(self):
        gate = threading.Event()
        old, new = self.begin(sync_gate=gate)
        self.wait_for(lambda: new.record.read()[0]["connection"].startswith("완료"))
        self.assertTrue(old.busy and new.busy)
        self.assertTrue(new.policy.snapshot()["handover_hold"])
        self.assertFalse(new.dialog.closed)
        gate.set()
        self.wait_for(lambda: not old.busy and not new.busy)
        self.assertFalse(new.policy.snapshot()["handover_hold"])
        self.assertTrue(old.policy.snapshot()["standby"])
        self.assertEqual(new.record.read()[0]["owner"], new.client)
        payload = new.app._apply_loaded_schedule_shared_payload.call_args.args[0]
        self.assertEqual(payload["schedule_events"][0]["at"], "2026-09-20T18:00:00.123456")

    def test_synced_first_also_keeps_output_locked_until_connection_finishes(self):
        gate = threading.Event()
        old, new = self.begin(connect_gate=gate)
        self.wait_for(lambda: new.record.read()[0]["sync"].startswith("완료"))
        self.assertTrue(new.busy)
        self.assertTrue(new.policy.snapshot()["handover_hold"])
        gate.set()
        self.wait_for(lambda: not new.busy)
        self.assertFalse(new.policy.snapshot()["handover_hold"])

    def test_upload_failure_never_logs_out_existing_administrator(self):
        old, new = self.begin(upload_fail=True)
        self.wait_for(lambda: not old.busy and not new.busy)
        old.app._stop_discord_bot_runtime_core.assert_not_called()
        self.assertFalse(old.policy.snapshot()["handover_hold"])
        new.app._start_discord_bot_runtime.assert_not_called()
        self.assertTrue(new.policy.snapshot()["handover_hold"])

    def test_changed_artifact_never_applies(self):
        app = self.app("receiver")
        manager = self.coordinator(app)
        manager.current = dict(artifact=dict(path="data/schedules/odin9.json", sha="wrong", version="1.2.3", hash="wrong"))
        manager._change = Mock()
        self.repo.put("data/schedules/odin9.json", app.raw)
        with self.assertRaises(HandoverError):
            manager._download_apply()
        app._apply_loaded_schedule_shared_payload.assert_not_called()

    def test_failed_disk_save_restores_memory_and_keeps_output_locked(self):
        app = self.app("receiver")
        manager = self.coordinator(app)
        manager._show()
        self.repo.put("data/schedules/odin9.json", app.raw)
        _, sha, _ = self.repo.get("data/schedules/odin9.json")
        manager.current = dict(deadline=time.time() + 3, artifact=dict(path="data/schedules/odin9.json", sha=sha,
                                  version="1.2.3", hash=app._get_github_schedule_content_hash(app.raw)))
        manager._change = Mock()
        app._save_schedule_state.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            manager._download_apply()
        app._restore_schedule_full_state_snapshot.assert_called_once()
        self.assertTrue(manager.policy.snapshot()["handover_hold"])

    def test_failed_old_logout_never_starts_new_bot(self):
        first = self.app("first")
        old = self.coordinator(first)
        active = old.record.request(old.client)
        old.record.change(active["request"], old.client, phases={"joining"}, phase="active")
        old.current = old.record.request("second")
        old._show(outgoing=True)
        first._stop_discord_bot_runtime_core.side_effect = None
        first._stop_discord_bot_runtime_core.return_value = False
        old._outgoing()
        record, _ = old.record.read()
        self.assertEqual(record["phase"], "failed")
        self.assertFalse(record["released"])
        self.assertTrue(old.policy.snapshot()["standby"])
        first._start_discord_bot_runtime.assert_not_called()

    def test_snapshot_failure_does_not_leave_ui_locked(self):
        app = self.app("snapshot-failure")
        app._create_schedule_restore_snapshot = Mock(side_effect=OSError("read failed"))
        app._get_schedule_restore_snapshot_signature = Mock()
        manager = self.coordinator(app)
        with self.assertRaises(OSError):
            manager._show()
        self.assertFalse(manager.busy)
        self.assertFalse(app.discord_handover_busy)

    def test_failure_cleanup_still_stops_bot_when_policy_write_fails(self):
        app = self.app("policy-failure")
        manager = self.coordinator(app)
        manager._show()
        with patch.object(manager.policy, "pause", side_effect=OSError("disk full")):
            manager._fail(HandoverError("connection failed"), outgoing=False)
        app._stop_discord_bot_runtime_core.assert_called_once()
        self.assertFalse(manager.busy)
        self.assertTrue(manager.policy.snapshot()["handover_hold"])
        self.assertIn("연결 상태 저장 실패", app.schedule_status_var.set.call_args.args[0])

    def test_scope_recovery_prompts_and_keeps_local_schedule(self):
        app = self.app('failed-solo')
        manager = self.coordinator(app)
        old = HandoverRecord(self.repo.get, self.repo.put, dict(manager.scope, season='17'))
        row = old.request(manager.client)
        old.change(row['request'], manager.client, phases={'joining'}, phase='failed')
        manager._show()
        manager._incoming()
        self.assertEqual(manager.record.read()[0]['phase'], 'active')
        self.assertEqual(manager.record.read()[0]['scope'], manager.scope)
        app._stop_discord_bot_runtime_core.assert_called_once()
        app._apply_loaded_schedule_shared_payload.assert_not_called()
        self.assertFalse(manager.busy)
        self.assertFalse(manager.policy.snapshot()['handover_hold'])

    def test_cancelled_scope_recovery_does_not_rewrite_record(self):
        app = self.app('cancel-recovery')
        app._show_centered_messagebox.return_value = False
        manager = self.coordinator(app)
        old = HandoverRecord(self.repo.get, self.repo.put, dict(manager.scope, season='17'))
        row = old.request(manager.client)
        old.change(row['request'], manager.client, phases={'joining'}, phase='failed')
        before = old.read()
        manager._show()
        manager._incoming()
        self.assertEqual(old.read(), before)
        app._start_discord_bot_runtime.assert_not_called()
        self.assertTrue(manager.policy.snapshot()['handover_hold'])

    def test_failed_local_shutdown_does_not_replace_old_record(self):
        app = self.app('cannot-stop')
        app._stop_discord_bot_runtime_core.side_effect = None
        app._stop_discord_bot_runtime_core.return_value = False
        manager = self.coordinator(app)
        old = HandoverRecord(self.repo.get, self.repo.put, dict(manager.scope, season='17'))
        row = old.request(manager.client)
        old.change(row['request'], manager.client, phases={'joining'}, phase='failed')
        before = old.read()
        manager._show()
        manager._incoming()
        self.assertEqual(old.read(), before)
        app._start_discord_bot_runtime.assert_not_called()
        self.assertTrue(manager.policy.snapshot()['handover_hold'])


if __name__ == "__main__":
    unittest.main()
