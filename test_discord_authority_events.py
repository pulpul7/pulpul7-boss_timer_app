"""Offline event authority, local liveness and approved retry regressions."""
import asyncio
from contextlib import nullcontext
from copy import deepcopy
import json
import threading
import queue
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

import discord_authority as authority
import discord_authority_events as events
import discord_connection_policy as policy_module
from discord_authority import AuthorityRecord, DiscordAuthority, AuthorityError, LegacyAuthorityError
from discord_authority_events import AuthorityEvents, MARKER
from discord_connection_policy import ConnectionPolicy, CONTROL_PROTOCOL
from github_request_policy import GithubRequestGate
from operation_retry import offer_verified_retry


class MemoryPolicy(ConnectionPolicy):
    def __init__(self):
        self.data = dict(self._empty(), protocol=CONTROL_PROTOCOL, client_id="local", runtime_id="runtime",
            authority_generation=1, authority_request="owned", authority_active=True,
            authority_needs_sync=False, authority_until=1010, standby=False, voice_requested=True)
    def _locked(self):
        return nullcontext()
    def snapshot(self, *, strict=False):
        return deepcopy(self.data)
    def _save(self, data):
        self.data = deepcopy(data)


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.clock = patch.object(authority, "time", NS(time=lambda: self.now, monotonic=lambda: self.now))
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.data = dict(schema=CONTROL_PROTOCOL, scope=dict(guild="100"), owner="old", owner_runtime="old-runtime",
            owner_application_id="11", generation=1, request="owned", phase="active", members={
            "old": dict(online=True, runtime="old-runtime", application_id="11", protocol=CONTROL_PROTOCOL, presence_mode="event"),
            "new": dict(online=True, runtime="new-runtime", application_id="22", protocol=CONTROL_PROTOCOL, presence_mode="event")})
        self.writes = []
        self.record = AuthorityRecord(self.get, self.put, dict(guild="100"))
    def get(self, _path):
        return deepcopy(self.data), str(len(self.writes)), ""
    def put(self, _path, data, **kwargs):
        self.assertEqual(kwargs["sha"], str(len(self.writes)))
        self.data = deepcopy(data); self.writes.append(deepcopy(data))
        return True, ""
    def requested(self):
        return self.record.request("new", "new-runtime", "777")["request"]
    def test_timeout_never_grants_without_stop_confirmation(self):
        identity = self.requested(); self.now += 31
        with self.assertRaises(AuthorityError):
            self.record.accept_stopped(identity, "new", "new-runtime")
        self.assertEqual(self.data["owner"], "old")
        self.assertEqual(len(self.writes), 1)
    def test_released_boolean_alone_is_not_stop_proof(self):
        identity = self.requested(); self.data["released"] = True
        with self.assertRaises(AuthorityError):
            self.record.accept_stopped(identity, "new", "new-runtime")
        self.assertEqual(self.data["owner"], "old")
    def test_matching_stop_confirmation_allows_one_transfer(self):
        identity = self.requested()
        self.record.acknowledge_stop(identity, "old", "old-runtime")
        result = self.record.accept_stopped(identity, "new", "new-runtime")
        self.assertEqual((result["owner"], result["owner_runtime"], result["phase"]), ("new", "new-runtime", "joining"))
        self.assertEqual(result["owner_application_id"], "22")
    def test_wrong_runtime_cannot_acknowledge_stop(self):
        identity = self.requested()
        with self.assertRaises(AuthorityError):
            self.record.acknowledge_stop(identity, "old", "another-runtime")
        self.assertFalse(self.data["released"])
    def test_expired_confirmation_cannot_grant(self):
        identity = self.requested()
        self.record.acknowledge_stop(identity, "old", "old-runtime")
        self.now += 31
        with self.assertRaises(AuthorityError):
            self.record.accept_stopped(identity, "new", "new-runtime")
    def test_old_ack_cannot_change_a_new_request(self):
        identity = self.requested()
        self.data["request"] = "newer"
        with self.assertRaises(AuthorityError):
            self.record.acknowledge_stop(identity, "old", "old-runtime")
    def test_manual_offline_confirmation_is_bound_to_current_owner(self):
        stamp = self.record.owner_stamp(self.data)
        self.data["generation"] += 1
        with self.assertRaises(AuthorityError):
            self.record.request("new", "new-runtime", "777", verified_previous=stamp)
        self.assertEqual(self.writes, [])
    def test_verified_offline_recovery_uses_new_generation(self):
        result = self.record.request("new", "new-runtime", "777", verified_previous=self.record.owner_stamp(self.data))
        self.assertEqual(result["phase"], "joining")
        self.assertEqual(result["release_proof"]["kind"], "operator_confirmed")
    def test_previous_protocol_requires_explicit_migration(self):
        for schema in [1, 2]:
            self.data["schema"] = schema
            with self.assertRaises(LegacyAuthorityError):
                self.record.read()
        self.assertEqual(self.writes, [])

    def test_local_process_recovery_requires_exact_prior_owner(self):
        self.data["members"]["old"]["runtime"] = "restarted"
        result = self.record.request("old", "restarted", "777", expected_owner=("old-runtime", 1),
            verified_previous=self.record.owner_stamp(self.data), verified_kind="local_process_stopped")
        self.assertEqual(result["phase"], "joining")
        self.assertEqual(result["release_proof"]["kind"], "local_process_stopped")

    def test_new_claim_cannot_move_local_generation_backwards(self):
        self.data.update(owner="", owner_runtime="", generation=0)
        result = self.record.request("new", "new-runtime", "777", minimum_generation=50)
        self.assertEqual(result["generation"], 51)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        clock = NS(monotonic=lambda: self.now, time=lambda: self.now, sleep=lambda _: None)
        for module in [authority, policy_module]:
            mocked = patch.object(module, "time", clock); mocked.start(); self.addCleanup(mocked.stop)
        self.policy = MemoryPolicy()
        self.coordinator = object.__new__(DiscordAuthority)
        self.coordinator.policy = self.policy
        self.coordinator._assert_profile = Mock()
        self.coordinator._status = Mock(return_value=dict(online=True, runtime_id="runtime"))
        self.coordinator._observe_outgoing_progress = Mock()
        self.coordinator._ui = Mock()
        self.coordinator.record = NS(read=Mock(side_effect=RuntimeError("GitHub is unavailable")),
                                     put=Mock(side_effect=RuntimeError("GitHub is unavailable")))
        self.coordinator.app = NS(_append_debug_log=Mock())
    def test_full_hour_healthy_voice_has_zero_github_requests(self):
        for self.now in range(1000, 4600):
            self.coordinator._poll()
            self.assertTrue(ConnectionPolicy.has_authority(self.policy.snapshot(), "runtime"))
        self.coordinator.record.read.assert_not_called()
        self.coordinator.record.put.assert_not_called()
    def test_github_lock_cannot_block_local_health(self):
        self.coordinator.lock = threading.Lock()
        with self.coordinator.lock:
            self.now = 1005; self.coordinator._poll()
        self.assertEqual(self.policy.data["authority_until"], 1015)
    def test_new_runtime_cannot_renew_old_ownership(self):
        self.assertFalse(self.policy.renew_controller("another-runtime"))
        self.assertEqual(self.policy.data["authority_until"], 1010)
    def test_reconnect_requires_verification_before_local_renewal(self):
        self.policy.update(authority_needs_sync=True, authority_until=0)
        self.assertFalse(self.policy.renew_controller("runtime"))
        self.assertFalse(ConnectionPolicy.has_authority(self.policy.snapshot(), "runtime"))
    def test_local_controller_timeout_is_distinct_and_needs_verification(self):
        self.now = 1011
        self.coordinator._status.return_value = {}
        self.coordinator._poll()
        self.assertTrue(self.policy.data["authority_needs_sync"])
        self.assertFalse(ConnectionPolicy.has_authority(self.policy.snapshot(), "runtime"))
        self.coordinator._ui.assert_called_once()
    def test_late_stop_request_cannot_stop_new_owner_generation(self):
        self.policy.update(authority_request="newer")
        with self.assertRaises(RuntimeError):
            self.policy.request_stop("runtime", "owned")
        self.assertTrue(self.policy.data["voice_requested"])
    def test_actual_stop_request_fences_audio_immediately(self):
        self.policy.request_stop("runtime", "owned")
        self.assertFalse(self.policy.data["voice_requested"])
        self.assertEqual(self.policy.data["release_voice_generation"], 1)
    def test_stop_preserves_prior_voice_generation_during_handover(self):
        self.policy.update(authority_generation=2, release_voice_generation=1)
        self.policy.request_stop("runtime", "owned")
        self.assertEqual(self.policy.data["release_voice_generation"], 1)
    def test_live_probe_excludes_stale_registered_runtime(self):
        self.coordinator._control = Mock(return_value=dict(members=[dict(client_id="old", runtime="new-session", application_id="11", online=True)]))
        checked = self.coordinator._live_data(dict(members=dict(old=dict(runtime="old-session", application_id="11"))))
        self.assertEqual(checked["members"], {})


class Embed:
    def __init__(self, **values):
        self.footer = NS(text="")
        self.values = values
        self.fields = []
    def set_footer(self, *, text): self.footer.text = text
    def add_field(self, **values): self.fields.append(values)


class EventTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.status = NS(pid=123, runtime_id="runtime", client_id="local", application_id="11", online=True, connected_at=1)
        self.guild = NS(id=100, me=NS(), get_member=Mock(return_value=NS(voice=None)))
        self.thread = NS(id=556, parent_id=555, name=events.CONTROL_THREAD_NAME, type="public", guild=self.guild,
            locked=False, send=AsyncMock(),
            permissions_for=Mock(return_value=NS(view_channel=True, send_messages_in_threads=True, embed_links=True)))
        self.guild.get_thread = Mock(return_value=self.thread)
        self.channel = NS(id=555, guild=self.guild, send=AsyncMock(),
            threads=[self.thread],
            permissions_for=Mock(return_value=NS(view_channel=True, send_messages=True, embed_links=True)))
        self.bot = NS(config=dict(text_channel_id="555", authority_control_channel_id="555"), message_content_enabled=True,
            client=NS(get_channel=Mock(return_value=self.channel)), connection_policy=MemoryPolicy(),
            _resolve_text_channel=AsyncMock(return_value=self.channel),
            discord=NS(Embed=Embed, AllowedMentions=NS(none=lambda: None), ChannelType=NS(public_thread="public"),
                       NotFound=KeyError, utils=NS(escape_markdown=lambda value: value)),
            _is_configured_guild=lambda guild: guild is self.guild,
            _get_configured_server_id=lambda: "100", _queue_local_schedule_request=AsyncMock())
        self.transport = AuthorityEvents(self.bot, self.status)
    def command(self, action):
        return dict(id="command", pid=123, runtime_id="runtime", sent_at=time.time(), action=action)
    def message(self, kind="changed", **values):
        payload = dict(kind=kind, id="message", at=time.time(), guild="100", client="peer", runtime="peer-runtime", protocol=CONTROL_PROTOCOL)
        payload.update(values)
        embed = Embed(); embed.set_footer(text=MARKER + json.dumps(payload))
        return NS(channel=self.thread, guild=self.guild, author=NS(bot=True, id=22), embeds=[embed])
    async def test_verified_change_hint_reaches_standby_gui(self):
        self.assertTrue(await self.transport.receive(self.message()))
        queued = self.bot._queue_local_schedule_request.call_args.args[0]
        self.assertEqual(queued["operation"], "administrator_event")
        self.assertEqual(queued["runtime_id"], "runtime")
    async def test_duplicate_hint_does_not_repeat_gui_work(self):
        message = self.message()
        await self.transport.receive(message); await self.transport.receive(message)
        self.bot._queue_local_schedule_request.assert_awaited_once()
    async def test_old_protocol_and_expired_hints_are_ignored(self):
        await self.transport.receive(self.message(protocol=2))
        await self.transport.receive(self.message(at=time.time()-21))
        self.bot._queue_local_schedule_request.assert_not_called()
    async def test_foreign_channel_cannot_signal_authority(self):
        message = self.message(); message.channel = NS(id=999)
        self.assertFalse(await self.transport.receive(message))
        self.bot._queue_local_schedule_request.assert_not_called()
    async def test_echo_is_not_a_second_change(self):
        await self.transport.receive(self.message(client="local", runtime="runtime"))
        self.bot._queue_local_schedule_request.assert_not_called()
    async def test_probe_replies_even_without_voice_authority(self):
        self.bot.connection_policy.update(standby=True)
        await self.transport.receive(self.message("probe", probe="peer-probe"))
        self.channel.send.assert_not_called()
        self.thread.send.assert_awaited_once()
        footer = self.thread.send.call_args.kwargs["embed"].footer.text
        payload = json.loads(footer[len(MARKER):])
        self.assertEqual(payload["kind"], "reply")
        self.assertEqual(payload["probe"], "peer-probe")
    async def test_reply_requires_bot_identity_and_current_runtime(self):
        self.transport.probes["query"] = {}
        await self.transport.receive(self.message("reply", probe="query",
            member=dict(client_id="peer", runtime="peer-runtime", application_id="wrong")))
        self.assertEqual(self.transport.probes["query"], {})
    async def test_valid_probe_reply_is_collected(self):
        self.transport.probes["query"] = {}
        await self.transport.receive(self.message("reply", probe="query",
            member=dict(client_id="peer", runtime="peer-runtime", application_id="22", online=True)))
        self.assertIn("peer", self.transport.probes["query"])
    async def test_missing_channel_and_permissions_fail_before_send(self):
        self.bot.config["text_channel_id"] = "999"
        self.bot._resolve_text_channel.return_value = None
        with self.assertRaises(RuntimeError): await self.transport.command(self.command("check"))
        self.bot.config["text_channel_id"] = "555"
        self.bot._resolve_text_channel.return_value = self.channel
        self.channel.permissions_for.return_value.send_messages = False
        with self.assertRaises(RuntimeError): await self.transport.command(self.command("publish"))
        self.channel.send.assert_not_called()
    async def test_wrong_process_and_expired_local_command_are_rejected(self):
        with self.assertRaises(ValueError):
            await self.transport.command(dict(self.command("publish"), runtime_id="old"))
        with self.assertRaises(ValueError):
            await self.transport.command(dict(self.command("publish"), sent_at=time.time()-6))
        self.channel.send.assert_not_called()
    async def test_publish_is_one_message_and_never_a_heartbeat(self):
        result = await self.transport.command(dict(self.command("publish"), request="owned", generation=1))
        self.assertTrue(result["ok"])
        self.channel.send.assert_not_called()
        self.thread.send.assert_awaited_once()
        self.assertEqual(self.thread.send.call_args.kwargs["delete_after"], 60)
        self.assertTrue(self.thread.send.call_args.kwargs["silent"])

    async def test_legacy_main_channel_probe_does_not_generate_another_notice(self):
        message = self.message("probe", probe="legacy")
        message.channel = self.channel
        self.assertTrue(await self.transport.receive(message))
        self.channel.send.assert_not_called()
        self.thread.send.assert_not_called()

    async def test_connection_start_is_announced_once_per_request(self):
        command = dict(self.command("publish"), request="owned", generation=1, notice="started",
                       notice_member=dict(name="나츠", server="오9", season="22"), notice_voice_channel="777")
        await self.transport.command(command)
        await self.transport.command(command)
        await self.transport.notice_worker
        self.channel.send.assert_awaited_once()
        self.assertEqual(self.channel.send.call_args.kwargs["embed"].values["title"], "관리자 접속 시작")
        self.assertEqual(self.channel.send.call_args.kwargs["delete_after"], 60)

    async def test_verified_completion_remains_and_has_no_internal_payload(self):
        self.status.voice_connected = True
        self.status.voice_channel_id = "777"
        self.status.voice_authority_generation = 1
        self.bot.connection_policy.update(authority_until=time.monotonic()+10)
        command = dict(self.command("publish"), request="owned", generation=1, notice="completed",
                       notice_member=dict(name="나츠", server="오9", season="22"), notice_voice_channel="777")
        await self.transport.command(command)
        await self.transport.notice_worker
        self.channel.send.assert_awaited_once()
        options = self.channel.send.call_args.kwargs
        self.assertNotIn("delete_after", options)
        self.assertEqual(options["embed"].values["title"], "관리자 접속 완료")
        self.assertEqual(options["embed"].footer.text, events.COMPLETED_NOTICE_FOOTER)
        self.assertTrue(events.is_completed_authority_notice(NS(embeds=[options["embed"]])))

    async def test_thread_send_permission_failure_never_falls_back_to_main_chat(self):
        self.thread.permissions_for.return_value.send_messages_in_threads = False
        with self.assertRaises(RuntimeError):
            await self.transport.command(self.command("check"))
        self.channel.send.assert_not_called()
        self.thread.send.assert_not_called()
    async def test_absence_verification_checks_actual_discord_voice_state(self):
        command = dict(self.command("verify_absent"), bot_user_id="22")
        self.assertTrue((await self.transport.command(command))["absent"])
        self.guild.get_member.return_value.voice = NS(channel=NS(id=777))
        self.assertFalse((await self.transport.command(command))["absent"])

    async def test_bot_user_identity_is_separate_from_application_identity(self):
        self.transport.probes["query"] = {}
        await self.transport.receive(self.message("reply", probe="query",
            member=dict(client_id="peer", runtime="peer-runtime", application_id="33", bot_user_id="22", online=True)))
        self.assertIn("peer", self.transport.probes["query"])


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.app = NS(root=NS(after=Mock()), schedule_window=None,
            _get_github_data_settings=lambda: dict(token="test"), _show_centered_messagebox=Mock(return_value=True))
        self.gate = GithubRequestGate()
        self.patch = patch("operation_retry.GITHUB_REQUEST_GATE", self.gate)
        self.patch.start(); self.addCleanup(self.patch.stop)
    def test_retry_waits_for_approval_and_ui_callback(self):
        retry = Mock()
        offer_verified_retry(self.app, "failure", "failed", retry)
        retry.assert_not_called()
        self.app.root.after.call_args.args[1]()
        retry.assert_called_once()
    def test_declined_retry_never_schedules_operation(self):
        self.app._show_centered_messagebox.return_value = False
        offer_verified_retry(self.app, "failure", "failed", Mock())
        self.app.root.after.assert_not_called()
    def test_rate_limit_shows_wait_instead_of_immediate_retry(self):
        self.gate.limited({}, "test", "PUT", {"Retry-After": "90"})
        offer_verified_retry(self.app, "failure", "failed", Mock())
        self.assertEqual(self.app._show_centered_messagebox.call_args.args[0], "showerror")
        self.app.root.after.assert_not_called()
    def test_retry_sequence_is_bounded(self):
        offer_verified_retry(self.app, "failure", "failed", Mock(), attempt=2)
        self.assertEqual(self.app._show_centered_messagebox.call_args.args[0], "showerror")
        self.app.root.after.assert_not_called()


class HandoverIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.lock = threading.Lock()
        self.data = dict(schema=CONTROL_PROTOCOL, scope=dict(guild="100"), control_channel="555",
            owner="old", owner_runtime="old-runtime", owner_application_id="11", generation=1,
            request="owned", phase="active", query_responder={}, query_routing_generation=0,
            members={key: dict(online=True, runtime=key+"-runtime", application_id=app_id,
                              protocol=CONTROL_PROTOCOL, presence_mode="event", name=key, server="9", season="18")
                     for key, app_id in [("old", "11"), ("new", "22")]})
        self.writes = []; self.completed = threading.Event(); self.upload_started = threading.Event()
        self.finish_upload = threading.Event(); self.fail_github = False
        self.nodes = {key: self.node(key, app_id) for key, app_id in [("old", "11"), ("new", "22")]}
        self.addCleanup(self.finish_upload.set)
    def get(self, _path):
        if self.fail_github: return None, None, "GitHub unavailable"
        with self.lock: return deepcopy(self.data), str(len(self.writes)), ""
    def put(self, _path, data, **values):
        if self.fail_github: return False, "GitHub unavailable"
        with self.lock:
            if values["sha"] != str(len(self.writes)): return False, "409"
            self.data = deepcopy(data); self.writes.append(deepcopy(data))
        return True, ""
    def upload(self, *args, **values):
        self.upload_started.set(); self.finish_upload.wait(5)
        return False, "upload permission expired", None
    def node(self, key, app_id):
        node = object.__new__(DiscordAuthority)
        node.client = key; node.runtime = key+"-runtime"; node.lock = threading.RLock()
        node.event_lock = threading.Lock(); node.alive = True; node.releasing = False
        node.busy = False; node.current = None; node.outgoing_requests = set(); node.calls = set()
        node.failure_notices = set()
        node.outgoing_progress_done = False; node.incoming_progress_token = ""
        node.policy = MemoryPolicy()
        node.policy.update(client_id=key, runtime_id=node.runtime, standby=key!="old",
            authority_active=key=="old", voice_requested=key=="old", authority_until=time.monotonic()+10)
        node.record = AuthorityRecord(self.get, self.put, dict(guild="100"))
        node._assert_profile = Mock(); node._progress = Mock(); node._finish_progress = Mock()
        node._warm_query_preference = Mock()
        node._ui = lambda callback, **_values: callback()
        node.app = NS(root=NS(after=Mock()), schedule_window=None, discord_handover_busy=False,
            discord_bot_voice_channel_id="777", discord_bot_text_channel_id="555",
            _append_debug_log=Mock(), schedule_status_var=NS(set=Mock()), _show_centered_messagebox=Mock(return_value=False),
            _get_github_data_settings=lambda: dict(token="test"),
            _get_current_github_upload_server_entry=lambda: dict(id="9", name="9"),
            _get_administrator_identity=lambda: dict(name=key), current_season_no=18,
            _build_github_schedule_payload=lambda version: dict(kind="schedule", payload=dict(share_prefix="9", season_no=18)),
            _upload_current_schedule_to_github_data=self.upload)
        def status():
            state = node.policy.snapshot()
            joining = ConnectionPolicy.has_authority(state, node.runtime, allow_joining=True) and state["voice_requested"]
            return dict(online=True, runtime_id=node.runtime, application_id=app_id, connected_at=1,
                voice_connected=joining, voice_disconnect_confirmed=not joining,
                voice_channel_id="777", voice_authority_generation=state["authority_generation"])
        node._status = status
        def control(action, **values):
            if action == "probe":
                return dict(members=[dict(member, client_id=client,
                    sending=ConnectionPolicy.has_authority(self.nodes[client].policy.snapshot(), client+"-runtime"))
                    for client, member in self.data["members"].items()])
            if action == "publish":
                for client, peer in self.nodes.items():
                    if client != key: peer.changed({})
            return dict(ok=True, channel_id="555", absent=True)
        node._control = control
        return node
    def connect(self):
        self.results = []
        def done(ok, message): self.results.append((ok, message)); self.completed.set()
        self.thread = threading.Thread(target=self.nodes["new"]._incoming,
            args=(True, None, "777", done, None), daemon=True)
        self.thread.start()
    def test_upload_can_stall_and_fail_without_delaying_or_revoking_transfer(self):
        self.connect()
        self.assertTrue(self.completed.wait(4), "transfer waited for upload")
        self.assertTrue(self.results[0][0], self.results)
        self.assertTrue(self.upload_started.wait(1))
        self.assertFalse(self.finish_upload.is_set())
        self.assertEqual(self.data["owner"], "new")
        proof = self.data["release_proof"]
        self.assertEqual((proof["kind"], proof["runtime"]), ("voice_stopped", "old-runtime"))
        self.fail_github = True
        self.nodes["new"]._poll()
        self.assertTrue(ConnectionPolicy.has_authority(self.nodes["new"].policy.snapshot(), "new-runtime"))
        self.finish_upload.set(); self.thread.join(1)
    def test_no_stop_confirmation_means_no_new_join(self):
        self.nodes["old"]._wait_local_stop = Mock(side_effect=AuthorityError("stop not confirmed"))
        with patch.object(authority, "HANDOVER_SECONDS", .1):
            self.connect(); self.assertTrue(self.completed.wait(3))
        self.assertFalse(self.results[0][0])
        self.assertFalse(any(row["owner"] == "new" for row in self.writes))
        self.assertFalse(ConnectionPolicy.has_authority(self.nodes["new"].policy.snapshot(), "new-runtime"))
        self.assertFalse(self.upload_started.is_set())
    def test_initial_github_failure_does_not_touch_existing_sender(self):
        self.fail_github = True; self.connect()
        self.assertTrue(self.completed.wait(2))
        self.assertFalse(self.results[0][0])
        self.nodes["old"]._poll()
        self.assertTrue(ConnectionPolicy.has_authority(self.nodes["old"].policy.snapshot(), "old-runtime"))
        self.assertEqual(self.writes, [])

    def test_missing_owner_record_cannot_create_two_senders(self):
        self.data.update(owner="", owner_runtime="", generation=0, phase="idle")
        self.connect(); self.assertTrue(self.completed.wait(2))
        self.assertFalse(self.results[0][0])
        self.assertFalse(any(row["owner"] == "new" for row in self.writes))
        self.nodes["old"]._poll()
        self.assertTrue(ConnectionPolicy.has_authority(self.nodes["old"].policy.snapshot(), "old-runtime"))

    def test_failed_new_voice_join_releases_grant_and_notifies_previous_owner(self):
        node = self.nodes["new"]
        original_status = node._status
        def failed_join_status():
            status = original_status()
            if node.policy.snapshot()["voice_requested"]:
                node.policy.update(voice_error="new voice join failed")
                status["voice_connected"] = False
            return status
        node._status = failed_join_status
        self.connect()
        self.assertTrue(self.completed.wait(4))
        self.assertFalse(self.results[0][0])
        self.assertEqual(self.data["owner"], "")
        self.assertEqual(self.data["failure"], "new voice join failed")
        self.assertFalse(node.policy.snapshot()["voice_requested"])
        self.assertFalse(ConnectionPolicy.has_authority(self.nodes["old"].policy.snapshot(), "old-runtime"))
        deadline = time.monotonic() + 1
        while not self.nodes["old"].failure_notices and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(self.nodes["old"].failure_notices)

    def test_cancelled_unacknowledged_transfer_restores_previous_sender(self):
        self.nodes["old"]._wait_local_stop = Mock(side_effect=AuthorityError("stop not confirmed"))
        with patch.object(authority, "HANDOVER_SECONDS", .1):
            self.connect()
            self.assertTrue(self.completed.wait(3))
        old = self.nodes["old"]
        old._process_record(deepcopy(self.data), old._status())
        self.assertEqual(self.data["owner"], "old")
        self.assertEqual(self.data["phase"], "active")
        self.assertTrue(ConnectionPolicy.has_authority(old.policy.snapshot(), "old-runtime"))
        self.assertTrue(old.failure_notices)

    def test_github_failure_after_voice_join_still_stops_failed_incoming_session(self):
        node = self.nodes["new"]
        original_put = self.put
        def fail_active_commit(path, data, **values):
            if data.get("owner") == "new" and data.get("phase") == "active":
                self.fail_github = True
            return original_put(path, data, **values)
        node.record.put = fail_active_commit
        self.connect()
        self.assertTrue(self.completed.wait(4))
        self.assertFalse(self.results[0][0])
        local = node.policy.snapshot()
        self.assertFalse(local["voice_requested"])
        self.assertEqual(local["blocked_request"], local["authority_request"])
        self.assertGreaterEqual(local["release_voice_generation"], 0)
        self.assertFalse(ConnectionPolicy.has_authority(local, "new-runtime"))


class GatewayContinuityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import boss_timer_discord_bot as bot_module
        self.now = 1000
        clock = NS(monotonic=lambda: self.now, time=lambda: 2000, sleep=lambda _: None)
        self.status = NS(online=True, voice_connected=True, connected_at=1234, runtime_id="runtime",
                         shutdown_requested=threading.Event(),
                         update=lambda **values: vars(self.status).update(values))
        self.events = {}
        self.bot = object.__new__(bot_module.DiscordScheduleBot)
        self.bot.client = NS(event=lambda handler: self.events.setdefault(handler.__name__, handler),
            ws=NS(_close_code=None, socket=NS(close_code=1001)), latency=.05)
        self.bot.voice_client = NS(is_connected=lambda: True)
        self.bot.gateway_disconnected_at = None
        self.bot.gateway_reconnect_signal = ""
        self.bot.gateway_last_heartbeat_sent_at = None
        self.bot.gateway_last_heartbeat_ack_at = None
        self.bot.gateway_last_heartbeat_latency = None
        self.bot.connection_policy = MemoryPolicy()
        self.bot._queue_authority_session_event = AsyncMock()
        for mocked in (patch.object(bot_module, "STATUS", self.status),
                       patch.object(bot_module, "time", clock), patch.object(policy_module, "time", clock),
                       patch.object(bot_module, "log")):
            mocked.start(); self.addCleanup(mocked.stop)
        self.bot._bind_events()

    async def test_discord_reconnect_signal_is_recorded_before_socket_cleanup(self):
        import boss_timer_discord_bot as bot_module
        with patch.object(bot_module, "log") as write_log:
            self.bot._observe_gateway_packet('{"op":7,"d":null}')
            await self.events["on_disconnect"]()
        self.assertEqual(self.bot.gateway_reconnect_signal, "discord_reconnect_request")
        self.assertIn("reason=discord_reconnect_request", write_log.call_args.args[0])

    async def test_last_ack_latency_remains_valid_after_sdk_latency_becomes_infinite(self):
        import boss_timer_discord_bot as bot_module
        self.bot._observe_gateway_packet('{"op":1,"d":42}', sent=True)
        self.now = 1000.05
        self.bot._observe_gateway_packet('{"op":11,"d":null}')
        self.now = 1001
        self.bot.client.latency = float('inf')
        with patch.object(bot_module, "log") as write_log:
            await self.events["on_disconnect"]()
        self.assertIn("last_heartbeat_latency_sec=0.050", write_log.call_args.args[0])
        self.assertIn("last_ack_age_sec=0.950", write_log.call_args.args[0])

    async def test_packet_diagnostics_never_log_chat_or_authentication_payloads(self):
        import boss_timer_discord_bot as bot_module
        with patch.object(bot_module, "log") as write_log:
            self.bot._observe_gateway_packet('{"op":0,"d":{"content":"private-chat"}}')
            self.bot._observe_gateway_packet('{"op":2,"d":{"token":"test-only"}}', sent=True)
            self.bot._observe_gateway_packet('{"op":0,"d":"' + 'x'*1000 + '"}')
        write_log.assert_not_called()

    async def test_one_second_resume_keeps_voice_and_last_verified_lease(self):
        await self.events["on_disconnect"]()
        self.assertFalse(self.status.online)
        self.assertTrue(self.status.voice_connected)
        self.assertEqual(self.bot.connection_policy.snapshot()["authority_until"], 1010)
        self.now = 1001
        await self.events["on_resumed"]()
        self.assertTrue(self.status.online)
        self.assertTrue(self.status.voice_connected)
        self.assertEqual(self.status.connected_at, 1234)
        self.assertTrue(self.bot.connection_policy.renew_controller("runtime"))
        self.bot._queue_authority_session_event.assert_awaited_once()

    async def test_ten_second_expiry_is_not_revived_by_resume(self):
        await self.events["on_disconnect"]()
        self.now = 1011
        self.assertTrue(self.bot.connection_policy.expire_controller())
        await self.events["on_resumed"]()
        self.assertFalse(self.status.voice_connected)
        self.assertFalse(self.bot.connection_policy.renew_controller("runtime"))
        self.assertTrue(self.bot.connection_policy.snapshot()["authority_needs_sync"])

    async def test_explicit_release_is_not_undone_by_short_resume(self):
        await self.events["on_disconnect"]()
        self.bot.connection_policy.pause()
        self.now = 1001
        await self.events["on_resumed"]()
        self.assertFalse(self.status.voice_connected)
        self.assertFalse(self.bot.connection_policy.renew_controller("runtime"))
        self.assertFalse(self.bot.connection_policy.snapshot()["voice_requested"])


class VoiceReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_revoked_sender_can_leave_only_its_acknowledged_generation(self):
        from boss_timer_discord_bot import DiscordScheduleBot
        policy = MemoryPolicy()
        policy.update(standby=True, release_voice_generation=1, authority_generation=2)
        leave = AsyncMock()
        connection = NS(_voice_connect=AsyncMock(), _voice_disconnect=leave)
        class BaseVoice:
            def create_connection_state(self): return connection
            def cleanup(self): pass
            def stop(self): pass
        bot = object.__new__(DiscordScheduleBot)
        bot.discord = NS(VoiceClient=BaseVoice)
        bot.connection_policy = policy
        bot.authority_disconnect_generation = -1
        voice = bot._authority_voice_client_type(1)()
        wrapped = voice.create_connection_state()
        await wrapped._voice_disconnect()
        leave.assert_not_called()
        bot.authority_disconnect_generation = 1
        await wrapped._voice_disconnect()
        leave.assert_awaited_once()
        bot.authority_disconnect_generation = 2
        await wrapped._voice_disconnect()
        leave.assert_awaited_once()

    async def test_disconnect_confirmation_requires_server_voice_leave(self):
        import boss_timer_discord_bot as bot_module
        from boss_timer_discord_bot import DiscordScheduleBot
        status = NS(online=True, update=lambda **values: vars(status).update(values))
        guild = NS(me=NS(voice=NS(channel=NS(id=777))))
        bot = object.__new__(DiscordScheduleBot)
        bot.connection_policy = MemoryPolicy()
        bot.connection_policy.update(release_voice_generation=1)
        bot.voice_client = NS(authority_generation=1)
        bot.timed_bridge_tasks = {}; bot.play_queue = queue.Queue()
        bot.voice_transition_lock = asyncio.Lock(); bot.voice_connect_lock = asyncio.Lock()
        bot.notice_output = None
        bot._stop_current_voice_playback_and_wait = AsyncMock()
        bot.client = NS(get_guild=lambda _guild: guild)
        bot._get_configured_server_id = lambda: "100"
        async def close(_voice, **_values):
            self.assertEqual(bot.authority_disconnect_generation, 1)
            guild.me.voice = None
            bot.voice_client = None
        bot._close_voice_client = close
        with patch.object(bot_module, "STATUS", status):
            await bot._disconnect_authority_voice()
        self.assertTrue(status.voice_disconnect_confirmed)
        self.assertEqual(bot.authority_disconnect_generation, -1)


if __name__ == "__main__": unittest.main()
