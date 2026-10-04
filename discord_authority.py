"""Guild authority and presence, independent of Gateway login and schedule sync."""
from copy import deepcopy
import hashlib
import json
import queue
import threading
import time
import unicodedata
import uuid
from pathlib import Path

from discord_connection_policy import ConnectionPolicy, CONTROL_PROTOCOL, AUTHORITY_SECONDS
from discord_query_routing import QueryLedger, QueryRoutingError, REQUEST_TTL

POLL_SECONDS = 1  # Local controller health; never a GitHub polling interval.
HANDOVER_SECONDS = 30
JOIN_SECONDS = 30
PRESENCE_TTL = 60


class AuthorityError(RuntimeError):
    pass


class LegacyAuthorityError(AuthorityError):
    def __init__(self, sha):
        super().__init__("구버전 담당 기록이 남아 있습니다. 구버전 프로그램과 봇을 종료한 뒤 프로그램의 디코 실행 버튼으로 기록을 전환하세요.")
        self.sha = sha


class AuthorityRecord:
    def __init__(self, get, put, scope):
        self.get, self.put, self.scope = get, put, dict(scope)
        key = hashlib.sha256(str(scope["guild"]).encode()).hexdigest()[:24]
        self.path = f"data/handover/{key}.json"

    def read(self):
        data, sha, error = self.get(self.path)
        if error:
            raise AuthorityError(error)
        if data is None:
            data = dict(schema=CONTROL_PROTOCOL, scope=self.scope, owner="", owner_runtime="",
                        generation=0, phase="idle", members={}, request="", receiver="")
        elif isinstance(data, dict) and data.get("schema") in {1, 2}:
            raise LegacyAuthorityError(sha)
        elif not isinstance(data, dict) or data.get("schema") != CONTROL_PROTOCOL:
            raise AuthorityError("관리자 기록 형식을 확인할 수 없습니다.")
        if str(data.get("scope", {}).get("guild")) != str(self.scope["guild"]):
            raise AuthorityError("Discord 서버의 권한 기록이 일치하지 않습니다.")
        if not isinstance(data.get("members"), dict):
            raise AuthorityError("관리자 접속 목록이 올바르지 않습니다.")
        return data, sha

    def migrate_legacy(self, expected_sha):
        data, sha, error = self.get(self.path)
        if error or sha != expected_sha or not isinstance(data, dict) or data.get("schema") not in {1, 2}:
            raise AuthorityError(error or "구버전 기록이 변경되었습니다. 다시 확인하세요.")
        replacement = dict(schema=CONTROL_PROTOCOL, scope=self.scope, owner="", owner_runtime="",
                           generation=0, phase="idle", members={}, request="", receiver="")
        ok, error = self.put(self.path, replacement, sha=sha, message="Migrate BossTimer authority protocol")
        if not ok:
            raise AuthorityError(error or "구버전 기록 전환에 실패했습니다.")

    def mutate(self, callback):
        # Re-read and merge on CAS conflicts. Never retry a superseded blob.
        for attempt in range(3):
            data, sha = self.read()
            previous = deepcopy(data)
            callback(data)
            if data == previous:
                return data  # A repeated release/reply/preference is not a commit.
            data["updated"] = time.time()
            ok, error = self.put(self.path, data, sha=sha, message="BossTimer administrator authority")
            if ok:
                return data
            if attempt == 2 or not any(code in str(error) for code in ("409", "422")):
                raise AuthorityError(error or "관리자 기록이 변경되었습니다. 다시 요청하세요.")

    @staticmethod
    def online_members(data, now=None):
        now = time.time() if now is None else now
        def fresh(member):
            try:
                return member.get("presence_mode") == "event" or -5 <= now - float(member.get("seen", 0)) < PRESENCE_TTL
            except (ValueError, TypeError, OverflowError):
                return False
        return {key: value for key, value in data.get("members", {}).items()
                if isinstance(value, dict) and value.get("online") and value.get("protocol") == CONTROL_PROTOCOL
                and fresh(value)}

    @staticmethod
    def query_members(data, now=None):
        """Active owner first, then newest Gateway connection; never heartbeat order."""
        members = AuthorityRecord.online_members(data, now)
        ordered = sorted(members, key=lambda client: (-float(members[client].get("connected_at", 0)), client))
        owner = data.get("owner")
        if (owner in members and data.get("phase") == "active"
                and data.get("owner_runtime") == members[owner].get("runtime")):
            ordered.remove(owner)
            ordered.insert(0, owner)
        result = []
        for client in ordered:
            length = 8
            while length < len(client) and any(other != client and other[:length].lower() == client[:length].lower() for other in members):
                length += 1
            member = members[client]
            result.append(dict(client_id=client, display_id=client[:length], runtime=member.get("runtime", ""),
                application_id=member.get("application_id", ""), bot_user_id=member.get("bot_user_id", ""),
                name=member.get("name", "담당자"), server=member.get("server", ""), season=member.get("season", ""),
                connected_at=member.get("connected_at", 0),
                sending=(client == owner and data.get("phase") == "active"
                         and data.get("owner_runtime") == member.get("runtime")),
                joining=(client == owner and data.get("phase") == "joining"
                         and data.get("owner_runtime") == member.get("runtime"))))
        return result

    @staticmethod
    def resolve_member(data, selector):
        normalize = lambda value: " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()
        selector = normalize(selector)
        members = AuthorityRecord.online_members(data)
        id_input = 8 <= len(selector) <= 32 and all(c in "0123456789abcdef" for c in selector)
        if id_input:
            matches = [key for key in members if key.lower().startswith(selector)]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise AuthorityError("관리자 ID가 중복됩니다. /보탐 관리자에서 더 긴 ID를 확인한 뒤 입력하세요.")
        matches = [key for key, member in members.items() if selector and normalize(member.get("name")) == selector]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise AuthorityError("같은 이름의 관리자가 여러 명입니다. /보탐 관리자에서 ID를 확인한 뒤 /보탐 ID로 호출하세요.")
        raise AuthorityError("접속한 관리자를 찾지 못했습니다. /보탐 관리자로 이름을 확인한 뒤 /보탐 나츠처럼 입력하세요.")

    def request(self, client, runtime, channel, *, call_id="", expected_owner=None, verified_previous=None,
                verified_kind="operator_confirmed", minimum_generation=0):
        def change(data):
            now = time.time()
            if expected_owner is not None and not (
                    data.get("owner") == client and data.get("owner_runtime") == expected_owner[0]
                    and data.get("generation") == expected_owner[1] and data.get("phase") == "active"):
                raise AuthorityError("관리자 권한이 변경되어 이전 연결 복구를 취소했습니다.")
            if data.get("phase") in {"requested", "joining"} and now < data.get("deadline", 0):
                raise AuthorityError("다른 관리자 승계가 진행 중입니다. 완료 후 다시 요청하세요.")
            if verified_previous is not None and self.owner_stamp(data) != verified_previous:
                raise AuthorityError("확인 중 담당자가 바뀌었습니다. 현재 담당자를 다시 확인하세요.")
            member = data["members"].get(client, {})
            if member.get("runtime") != runtime or client not in self.online_members(data):
                raise AuthorityError("대상 봇의 연결 상태가 변경되었습니다.")
            if call_id:
                call = member.get("call", {})
                if call.get("id") != call_id or now >= call.get("expires", 0):
                    raise AuthorityError("원격 호출의 응답시간이 초과되었습니다.")
                call.update(state="accepted")
            solo = (not data.get("owner") or (data.get("owner") == client and data.get("owner_runtime") == runtime)
                    or verified_previous is not None)
            previous_owner = self.owner_stamp(data)
            data.update(scope=self.scope, receiver=client, receiver_runtime=runtime,
                        generation=max(int(data.get("generation", 0)), int(minimum_generation)) + 1,
                        request=uuid.uuid4().hex, phase="joining" if solo else "requested",
                        deadline=now + (JOIN_SECONDS if solo else HANDOVER_SECONDS),
                        released=solo, channel=channel, upload="대기", previous_owner=previous_owner,
                        failure="", failure_request="",
                        release_proof={"kind": verified_kind} if verified_previous is not None else {})
            if solo:
                data.update(owner=client, owner_runtime=runtime, owner_application_id=member.get("application_id", ""),
                            owner_bot_user_id=member.get("bot_user_id") or member.get("application_id", ""))
        return self.mutate(change)

    @staticmethod
    def owner_stamp(data):
        return (data.get("owner"), data.get("owner_runtime"), int(data.get("generation", 0)), data.get("request"))

    def acknowledge_stop(self, request_id, client, runtime):
        def change(data):
            if (data.get("request") != request_id or data.get("phase") != "requested"
                    or data.get("owner") != client or data.get("owner_runtime") != runtime):
                raise AuthorityError("송출 종료 확인 전에 승계 요청이 변경됐습니다.")
            data.update(released=True, release_proof=dict(kind="voice_stopped", client=client,
                                                        runtime=runtime, request=request_id))
        return self.mutate(change)

    def accept_stopped(self, request_id, client, runtime):
        def change(data):
            proof = data.get("release_proof") or {}
            if (data.get("request") != request_id or data.get("phase") != "requested"
                    or data.get("receiver") != client or data.get("receiver_runtime") != runtime
                    or not data.get("released") or proof.get("kind") != "voice_stopped"
                    or proof.get("client") != data.get("owner") or proof.get("runtime") != data.get("owner_runtime")
                    or proof.get("request") != request_id or time.time() >= data.get("deadline", 0)):
                raise AuthorityError("기존 송출 종료 확인이 없거나 승계 시간이 만료됐습니다. 재검증 후 다시 요청하세요.")
            data.update(owner=client, owner_runtime=runtime, generation=int(data["generation"]) + 1,
                        owner_application_id=data["members"].get(client, {}).get("application_id", ""),
                        owner_bot_user_id=data["members"].get(client, {}).get("bot_user_id")
                            or data["members"].get(client, {}).get("application_id", ""),
                        phase="joining", deadline=time.time() + JOIN_SECONDS)
        return self.mutate(change)

    def change(self, request_id, actor, *, phases, **changes):
        def update(data):
            if (data.get("request") != request_id or data.get("phase") not in phases
                    or actor not in {data.get("owner"), data.get("receiver")}):
                raise AuthorityError("권한 요청이 변경되었거나 이미 종료되었습니다.")
            data.update(changes)
        return self.mutate(update)


class DiscordAuthority:
    def __init__(self, app):
        self.app = app
        self.profile = app._get_discord_bot_config_storage_path()
        self.policy = ConnectionPolicy(self.profile)
        self.client = app._get_administrator_identity()["client_id"]
        self.scope = dict(guild=str(app.discord_bot_server_id),
                          server=str(app._get_current_github_upload_server_entry()["id"]),
                          season=str(app.current_season_no))
        self.record = AuthorityRecord(
            lambda path: app._github_get_json_file(path, timeout=3),
            lambda path, data, **kw: app._github_put_json_file(path, data, timeout=3, **kw), self.scope)
        self.query_ledger = QueryLedger(self.record)
        self.query_preference_warming = False
        try:
            self.policy.update(query_protocol=1)
        except (OSError, ValueError, TypeError) as exc:
            app._append_debug_log(f"query_protocol_setup_failed error={exc!r}")
        self.events = queue.Queue()
        self.busy = False
        app.discord_handover_busy = False
        self.alive = True
        self.releasing = False
        self.runtime = ""
        self.control_channel_id = ""
        self.current = None
        self.outgoing_requests = set()
        self.calls = set()
        self.failure_notices = set()
        self.incoming_progress_token = ""
        self.outgoing_progress_token = ""
        self.outgoing_progress_done = False
        self.outgoing_progress_result = ""
        self.outgoing_progress_runtime = ""
        self.last_progress = {}
        self.lock = threading.RLock()
        self.event_lock = threading.Lock()
        self.session_lock = threading.Lock()
        app.root.after(100, self._drain)
        threading.Thread(target=self._controller_watch, daemon=True, name="discord-local-controller").start()

    def _assert_profile(self):
        if (not self.alive or self.app._get_discord_bot_config_storage_path() != self.profile
                or str(self.app.discord_bot_server_id) != self.scope["guild"]):
            raise AuthorityError("Discord 설정이 변경되어 요청을 중단했습니다.")

    def _ui(self, callback, wait=True, timeout=HANDOVER_SECONDS):
        done, cancelled, result = threading.Event(), threading.Event(), []
        def run():
            if cancelled.is_set():
                return
            try:
                self._assert_profile()
                result.append((True, callback()))
            except Exception as exc:
                result.append((False, exc))
            finally:
                done.set()
        self.events.put(run)
        if not wait:
            return
        if not done.wait(timeout):
            cancelled.set()
            raise AuthorityError("프로그램의 요청 응답시간을 초과했습니다.")
        ok, value = result[0]
        if not ok:
            raise value
        return value

    def _drain(self):
        if not self.alive:
            return
        while True:
            try:
                self.events.get_nowait()()
            except queue.Empty:
                break
        self.app.root.after(100, self._drain)

    def _status(self):
        status = self.app._query_discord_bot_status_port(timeout=.3)
        return status if self.app._discord_bot_status_matches(status) else {}

    def _progress(self, role, token, step, detail, *, peer="", create=False):
        """Post display-only updates; never wait for Tk or fetch remote data."""
        value = (token, step, detail, peer)
        if not create and self.last_progress.get(role) == value:
            return
        self.last_progress[role] = value
        def show():
            try:
                if getattr(self, role + "_progress_token") != token:
                    return  # Ignore an older operation's late display updates.
                from discord_connection_progress import show_connection_progress
                popup = (show_connection_progress(self.app, role=role, token=token) if create
                         else getattr(self.app, "discord_connection_progress", None))
                if popup is not None and popup.token == token and popup.role == role:
                    popup.update(step, detail, peer)
            except Exception as exc:
                self.app._append_debug_log(f"authority_progress_display_failed type={type(exc).__name__}")
        self._ui(show, wait=False)

    def _finish_progress(self, role, token, detail, *, success=True, close=False):
        try:
            popup = getattr(self.app, "discord_connection_progress", None)
            if popup is not None and popup.token == token and popup.role == role:
                if close:
                    popup.close()
                else:
                    popup.finish(detail, success=success)
        except Exception as exc:
            self.app._append_debug_log(f"authority_progress_finish_failed type={type(exc).__name__}")

    def _observe_outgoing_progress(self, status):
        # Reuse the existing authority poll's status. No extra query or timer.
        if (not self.outgoing_progress_done or not status.get("runtime_id")
                or status.get("runtime_id") != self.outgoing_progress_runtime):
            return
        if not status.get("voice_connected"):
            token, result = self.outgoing_progress_token, self.outgoing_progress_result
            online = bool(status.get("online"))
            self.outgoing_progress_done = False
            detail = result + ("\n음성 연결 종료 · 봇 연결 유지 · 권한 없음" if online
                               else "\n음성 연결 종료 · 봇 연결 상태를 확인하세요.")
            self._ui(lambda: self._finish_progress("outgoing", token, detail, success=online), wait=False)

    def _observe(self, data, status, checked_at=None):
        self._assert_profile()
        if not status.get("runtime_id") or not status.get("online"):
            # A single status-port timeout must not revoke a valid lease.
            # The independent local controller loop decides local liveness.
            return
        self.runtime = str(status["runtime_id"])
        local = self.policy.snapshot()
        owns = (data.get("owner") == self.client and data.get("owner_runtime") == self.runtime
                and data.get("phase") in {"joining", "active"}
                and not (local.get("blocked_request") and local["blocked_request"] == data.get("request")))
        # Ownership is verified on events; the lease tracks local controller health.
        values = dict(protocol=CONTROL_PROTOCOL, client_id=self.client, runtime_id=self.runtime,
                      standby=not owns, handover_hold=False, handover_role="",
                      authority_generation=int(data.get("generation", 0)),
                      authority_request=str(data.get("request") or ""),
                      authority_active=owns and data.get("phase") == "active",
                      authority_until=time.monotonic() + AUTHORITY_SECONDS if owns else 0,
                      authority_needs_sync=False,
                      release_voice_generation=(local.get("release_voice_generation", -1)
                          if local.get("standby") else local.get("authority_generation", -1))
                          if data.get("phase") == "requested" and data.get("owner") == self.client
                          and data.get("owner_runtime") == self.runtime else -1,
                      voice_requested=owns and not self.releasing)
        if owns:
            values["voice_channel_id"] = str(data.get("channel") or "")
        online = self.record.online_members(data)
        application_members = {client: member for client, member in online.items()
                               if str(member.get("application_id") or "") == str(status.get("application_id") or "")}
        responder = data.get("owner") if data.get("owner") in application_members else min(application_members, default=self.client)
        values["command_sync_allowed"] = responder == self.client
        query_generation = int(data.get("query_routing_generation", -1))
        preferred = data.get("query_responder") or {}
        if local.get("query_routing_generation", -1) > query_generation:
            query_generation = local["query_routing_generation"]
            preferred = local.get("query_responder") or {}
        values["query_members"] = QueryLedger.order_candidates(self.record.query_members(data), preferred)
        values["query_responder"] = preferred
        values["query_routing_generation"] = query_generation
        values["query_until"] = time.monotonic() + AUTHORITY_SECONDS
        values["query_protocol"] = 1
        with self.lock:
            local = self.policy.snapshot()
            if (local.get("runtime_id") == self.runtime
                    and local.get("authority_generation", 0) > values["authority_generation"]):
                return
            self.policy.update(**values)

    def _control(self, action, **values):
        from discord_authority_events import request_control
        from boss_timer_gui import DISCORD_BOT_STATUS_PORT
        status = self._status()
        if not status.get("online"):
            raise AuthorityError("봇 연결이 끊겼습니다. 연결 상태를 확인한 뒤 다시 요청하세요.")
        read_settings = getattr(self.app, "_load_discord_bot_settings", None)
        settings = read_settings() if callable(read_settings) else {}
        channel = settings.get("text_channel_id", getattr(self.app, "discord_bot_text_channel_id", ""))
        values["text_channel_id"] = str(channel or "").strip()
        result = request_control(DISCORD_BOT_STATUS_PORT, self.app.notice_output_secret,
                                 status, action, **values)
        self.control_channel_id = str(result.get("channel_id") or "")
        return result

    def _metadata(self, status):
        identity = self.app._get_administrator_identity()
        entry = dict(self.app._get_current_github_upload_server_entry())
        metadata = dict(name=identity.get("name") or "담당자 미등록",
                    server=str(entry.get("name") or entry["id"]), season=str(self.app.current_season_no),
                    application_id=str(status.get("application_id") or ""))
        metadata["bot_user_id"] = str(status.get("bot_user_id") or status.get("application_id") or "")
        return metadata

    def _presence(self, data, status, metadata):
        # Event registration only; no periodic seen/timestamp refresh.
        if not status.get("runtime_id"):
            return data
        self._assert_profile()
        values = dict(metadata, online=bool(status.get("online")), runtime=str(status["runtime_id"]),
                      connected_at=float(status.get("connected_at") or 0), protocol=CONTROL_PROTOCOL,
                      presence_mode="event")
        member = data["members"].get(self.client, {})
        channel = str(getattr(self, "control_channel_id", "") or getattr(self.app, "discord_bot_text_channel_id", "") or "")
        if (data.get("control_channel") and data["control_channel"] != channel
                and (data.get("owner") or data.get("phase", "idle") != "idle")):
            raise AuthorityError("다른 관리자와 안내채팅 채널이 다릅니다. 모든 PC에 같은 안내채팅 ID를 설정하세요.")
        if all(member.get(key) == value for key, value in values.items()) and data.get("control_channel") == channel:
            return data
        def update(record):
            self._assert_profile()
            if (record.get("control_channel") and record["control_channel"] != channel
                    and (record.get("owner") or record.get("phase", "idle") != "idle")):
                raise AuthorityError("안내채팅 채널 설정이 변경되었습니다.")
            previous = record["members"].get(self.client, {})
            record["members"][self.client] = dict(previous, **values, seen=time.time())
            record["control_channel"] = channel
        with self.lock:
            return self.record.mutate(update)

    def _controller_watch(self):
        while self.alive:
            try:
                self._poll()
            except AuthorityError:
                self.alive = False
            except Exception as exc:
                self.app._append_debug_log(f"local_controller_failed type={type(exc).__name__} error={exc!r}")
            time.sleep(POLL_SECONDS)

    def _poll(self):
        # This loop must never acquire self.lock: GitHub operations can hold it.
        self._assert_profile()
        status = self._status()
        local = self.policy.snapshot()
        if (status.get("online") and status.get("runtime_id") == local.get("runtime_id")
                and not local.get("authority_needs_sync")):
            self.policy.renew_controller(status["runtime_id"])
            self.policy.update(query_until=time.monotonic() + AUTHORITY_SECONDS)
            self._observe_outgoing_progress(status)
        if self.policy.expire_controller():
            self.app._append_debug_log("authority_controller_expired source=localhost")
            self._ui(lambda: self.app._show_centered_messagebox("showerror", "송출 연결 확인",
                "이 PC의 GUI·봇 연결 확인이 끊겨 송출을 중단했습니다.\n"
                "GitHub 확인 실패와는 별개입니다. 디코 실행 버튼으로 현재 담당자를 검증하고 다시 연결하세요.",
                parent=self.app.schedule_window or self.app.root), wait=False)

    def _notify(self, data):
        self._control("publish", request=data.get("request", ""), generation=data.get("generation", 0))

    def _process_record(self, data, status):
        channel = str(getattr(self, "control_channel_id", "") or getattr(self.app, "discord_bot_text_channel_id", "") or "")
        if data.get("control_channel") != channel:
            raise AuthorityError("안내채팅 채널이 현재 담당자와 다릅니다. 모든 PC의 안내채팅 ID를 확인하세요.")
        self._observe(data, status)
        failed_request = data.get("failure_request")
        notice = (failed_request, data.get("generation"))
        if (data.get("failure") and failed_request == getattr(self, "outgoing_progress_token", "")
                and notice not in self.failure_notices):
            self.failure_notices.add(notice)
            token = self.outgoing_progress_token
            self.outgoing_progress_token = ""
            def show_failure():
                detail = str(data["failure"])
                if data.get("owner") == self.client:
                    self.app._show_centered_messagebox("showinfo", "승계 취소",
                        detail + "\n현재 담당자를 다시 확인하고 기존 송출을 재개합니다.",
                        parent=self.app.schedule_window or self.app.root)
                else:
                    from operation_retry import offer_verified_retry
                    offer_verified_retry(self.app, "승계 연결 실패", detail + "\n현재 송출 담당자가 없습니다.",
                                         lambda: self.start())
                self._finish_progress("outgoing", token, detail, success=False)
            self._ui(show_failure, wait=False)
        if "query_responder" not in data:
            self._warm_query_preference()
        if (data.get("owner") == self.client and data.get("owner_runtime") == status.get("runtime_id")
                and data.get("phase") == "requested" and data["request"] not in self.outgoing_requests):
            self.outgoing_requests.add(data["request"])
            entry = dict(self.app._get_current_github_upload_server_entry())
            threading.Thread(target=self._outgoing, args=(deepcopy(data), entry), daemon=True).start()
        call = data["members"].get(self.client, {}).get("call", {})
        if (call.get("state") == "pending" and call.get("runtime") == status.get("runtime_id")
                and time.time() < call.get("expires", 0) and call.get("id") not in self.calls):
            self.calls.add(call["id"])
            self._ui(lambda: self.start(remote=True, call=call), wait=False)

    def session_connected(self):
        def worker():
            with self.session_lock:
                try:
                    self._assert_profile()
                    status = self._status()
                    if not status.get("online"):
                        return
                    self._control("check")
                    with self.lock:
                        data, _ = self.record.read()
                        previous = data
                        data = self._presence(data, status, self._metadata(status))
                    self._process_record(data, status)
                    if data is not previous:
                        self._notify(data)
                except Exception as exc:
                    self.app._append_debug_log(f"authority_session_verify_failed error={exc!r}")
                    self._ui(lambda message=str(exc): self.app.schedule_status_var.set(
                        "봇 연결됨 · 담당자 확인 필요: " + message), wait=False)
        threading.Thread(target=worker, daemon=True, name="discord-session-verify").start()

    def changed(self, event, attempt=0):
        def worker():
            with self.event_lock:
                try:
                    self._assert_profile()
                    # Discord carries only a hint; durable ownership is verified.
                    with self.lock:
                        data, _ = self.record.read()
                    status = self._status()
                    if status.get("online"):
                        self._process_record(data, status)
                except Exception as exc:
                    # Failure must never revoke or stop a healthy existing owner.
                    self.app._append_debug_log(f"authority_event_verify_failed error={exc!r}")
                    self._ui(lambda message=str(exc): self.app.schedule_status_var.set(
                        "승계 변경 확인 실패 · 기존 송출 유지: " + message), wait=False)
                    from operation_retry import offer_verified_retry
                    self._ui(lambda message=str(exc): offer_verified_retry(self.app,
                        "승계 변경 확인 실패", message,
                        lambda: self.changed(event, attempt + 1), attempt=attempt), wait=False)
        threading.Thread(target=worker, daemon=True, name="discord-authority-event").start()

    def _live_data(self, data):
        result = self._control("probe")
        live = {member.get("client_id"): member for member in result["members"] if member.get("online")}
        for client, member in live.items():
            if ((member.get("sending") or member.get("joining"))
                    and (client != data.get("owner") or member.get("runtime") != data.get("owner_runtime"))):
                raise AuthorityError("실제 송출 관리자와 공유 담당 기록이 다릅니다. 기존 송출·제어 채널·GitHub 저장소 설정을 확인하고 다시 요청하세요.")
        checked = deepcopy(data)
        checked["members"] = {key: value for key, value in data["members"].items()
            if key in live and live[key].get("runtime") == value.get("runtime")
            and live[key].get("application_id") == value.get("application_id")
            and (live[key].get("bot_user_id") or live[key].get("application_id"))
                == (value.get("bot_user_id") or value.get("application_id"))}
        return checked

    def start(self, *, remote=False, call=None, channel="", done=None, expected_owner=None, attempt=0):
        self._assert_profile()
        if self.busy or self.releasing:
            if call:
                threading.Thread(target=self._answer_remote_call,
                    args=(call, False, "다른 관리자 승계 또는 권한 반납이 진행 중입니다."), daemon=True).start()
            if done:
                done(False, "다른 관리자 승계가 진행 중입니다.")
            return
        self.busy = True
        self.app.discord_handover_busy = True
        self.current = None
        self.incoming_progress_token = uuid.uuid4().hex
        self._progress("incoming", self.incoming_progress_token, "bot", "봇 연결 상태를 확인하고 있습니다.",
                       peer="Discord에서 요청한 관리자 접속" if remote else "디코 실행 · 관리자 접속", create=True)
        threading.Thread(target=self._incoming, args=(remote, call, channel, done, expected_owner, attempt), daemon=True).start()

    def _answer_remote_call(self, call, result, message):
        try:
            def finish_call(data):
                target = data["members"].get(self.client, {}).get("call", {})
                if target.get("id") == call["id"] and target.get("runtime") == call.get("runtime"):
                    target.update(state="done", ok=result, message=message)
            with self.lock:
                self.record.mutate(finish_call)
        except Exception as exc:
            self.app._append_debug_log(f"administrator_call_reply_failed type={type(exc).__name__} error={exc!r}")

    def _incoming(self, remote, call, channel, done, expected_owner, attempt=0):
        request_id = ""
        result, message = False, ""
        try:
            deadline = time.monotonic() + JOIN_SECONDS
            while time.monotonic() < deadline:
                status = self._status()
                if status.get("configuration_error"):
                    raise AuthorityError(status["configuration_error"])
                if status.get("online"):
                    break
                time.sleep(.25)
            else:
                raise AuthorityError("봇 연결이 완료되지 않았습니다. 봇 연결은 유지하며 다시 시도할 수 있습니다.")
            runtime = str(status["runtime_id"])
            self._control("check")
            self._progress("incoming", self.incoming_progress_token, "owner",
                           "봇 연결 확인 완료 · 현재 담당 관리자를 확인하고 있습니다.")
            try:
                with self.lock:
                    existing, _ = self.record.read()
            except LegacyAuthorityError as exc:
                if remote:
                    raise
                if not self._ui(lambda: self.app._show_centered_messagebox("askyesno", "구버전 연결 종료 확인",
                    "구버전의 담당 기록이 남아 있습니다.\n모든 PC의 구버전 프로그램과 봇을 종료했나요?\n"
                    "확인하면 권한 기록만 새 구조로 전환합니다. 설정과 스케줄은 보존합니다.",
                    parent=self.app.schedule_window or self.app.root), timeout=None):
                    message = "봇 연결됨 · 권한 없음 (승계 취소)"
                    return
                with self.lock:
                    self.record.migrate_legacy(exc.sha)
                    existing, _ = self.record.read()
            with self.lock:
                existing = self._presence(existing, status, self._metadata(status))
            local = self.policy.snapshot()
            requested_channel = str((call or {}).get("channel") or channel or self.app.discord_bot_voice_channel_id)
            if (existing.get("owner") == self.client and existing.get("owner_runtime") == runtime
                    and existing.get("phase") == "active" and ConnectionPolicy.has_authority(local, runtime)
                    and status.get("voice_connected") and str(status.get("voice_channel_id")) == requested_channel):
                result, message = True, "관리자 · 송출 중 (현재 연결 확인 완료)"
                return
            live = self._live_data(existing)
            owner = live["members"].get(existing.get("owner"), {})
            if owner.get("runtime") != existing.get("owner_runtime"):
                owner = {}
            verified_previous = None
            verified_kind = "operator_confirmed"
            if (existing.get("owner") and (existing.get("owner") != self.client
                    or existing.get("owner_runtime") != runtime) and not owner):
                bot_user_id = existing.get("owner_bot_user_id") or existing.get("owner_application_id")
                local_stopped = (existing.get("owner") == self.client
                    and expected_owner == (existing.get("owner_runtime"), existing.get("generation"))
                    and getattr(self.app, "discord_bot_verified_stopped_runtime", "") == existing.get("owner_runtime"))
                if local_stopped:
                    verified_kind = "local_process_stopped"
                else:
                    absent = self._control("verify_absent", bot_user_id=bot_user_id).get("absent")
                    if remote or not absent:
                        raise AuthorityError("이전 담당자의 종료를 확인하지 못해 승계하지 않았습니다. GUI에서 종료 확인 후 다시 요청하세요.")
                    if not self._ui(lambda: self.app._show_centered_messagebox("askyesno", "이전 담당자 종료 확인",
                            "이전 담당자의 응답이 없고 해당 봇의 음성 입장이 확인되지 않습니다.\n"
                            "이전 PC의 프로그램과 봇을 완전히 종료했는지 직접 확인했나요?\n"
                            "확인하면 현재 담당자 기록을 다시 검증한 새 요청으로 연결합니다.",
                            parent=self.app.schedule_window or self.app.root), timeout=None):
                        message = "봇 연결됨 · 권한 없음 (승계 취소 · 이전 담당자 종료 확인 필요)"
                        return
                verified_previous = self.record.owner_stamp(existing)
            if owner:
                self._progress("incoming", self.incoming_progress_token, "owner",
                               "현재 담당자 정보를 확인했습니다. 승계 여부를 확인하고 있습니다.",
                               peer=f"현재 담당자: {owner.get('name') or '담당자'} · "
                                    f"{owner.get('server') or ''} · {owner.get('season') or ''}차")
            if (owner.get("runtime") == existing.get("owner_runtime") and existing.get("owner")
                    and existing.get("owner") != self.client and not remote):
                if not self._ui(lambda: self.app._show_centered_messagebox("askyesno", "관리자 승계",
                    "다른 관리자가 운영 중입니다. 권한을 승계하고 음성채널에 입장할까요?\n"
                    "아니오를 선택하면 봇 연결만 유지합니다. 스케줄은 자동 동기화하지 않습니다.",
                    parent=self.app.schedule_window or self.app.root), timeout=None):
                    message = "봇 연결됨 · 권한 없음 (승계 취소)"
                    return
            channel = str((call or {}).get("channel") or channel or self.app.discord_bot_voice_channel_id)
            if not channel.isdigit():
                raise AuthorityError("입장할 음성채널을 설정하세요.")
            with self.lock:
                grant_checked_at = time.monotonic()
                self.current = self.record.request(self.client, runtime, channel,
                    call_id=(call or {}).get("id", ""), expected_owner=expected_owner,
                    verified_previous=verified_previous, verified_kind=verified_kind,
                    minimum_generation=self.policy.snapshot().get("authority_generation", 0))
            request_id = self.current["request"]
            self._notify(self.current)
            if self.current["phase"] == "requested":
                self._progress("incoming", self.incoming_progress_token, "handover",
                               "기존 관리자의 실제 음성 종료 확인을 기다립니다.\n"
                               "응답 시간이 끝나도 확인 없이 권한을 가져오지 않습니다.")
            else:
                self._progress("incoming", self.incoming_progress_token, "authority",
                               "기존 담당자가 없어 승계 대기를 생략하고 관리자 권한을 확인합니다.")
            wait_until = time.monotonic() + HANDOVER_SECONDS
            while self.current["phase"] == "requested":
                if self.current.get("released"):
                    self._progress("incoming", self.incoming_progress_token, "authority",
                                   ("기존 관리자 처리: " + str(self.current.get("upload") or "완료")
                                    if self.current.get("released") else "기존 관리자 종료 확인을 기다립니다.")
                                   + "\n새 관리자 권한을 획득하고 있습니다.")
                    with self.lock:
                        grant_checked_at = time.monotonic()
                        latest, _ = self.record.read()
                        if latest.get("request") != request_id:
                            raise AuthorityError("다른 승계 요청으로 변경되었습니다.")
                        self.current = self.record.accept_stopped(request_id, self.client, runtime)
                    self._notify(self.current)
                    break
                if time.monotonic() >= wait_until:
                    raise AuthorityError("승계 응답 시간이 끝났습니다. 기존 송출 종료 확인이 없어 새 음성 입장을 중단했습니다.")
                time.sleep(.5)
                with self.lock:
                    self.current, _ = self.record.read()
                if self.current.get("request") != request_id:
                    raise AuthorityError("다른 승계 요청으로 변경되었습니다.")
            with self.lock:
                self.policy.resume()
                self._observe(self.current, self._status(), grant_checked_at)
            self._progress("incoming", self.incoming_progress_token, "voice",
                           "관리자 권한 확인 완료 · 음성채널에 입장하고 있습니다.\n"
                           "현재 PC의 스케줄을 그대로 사용합니다.")
            deadline = time.monotonic() + JOIN_SECONDS
            while time.monotonic() < deadline:
                status = self._status()
                state = self.policy.snapshot()
                if state.get("voice_error"):
                    raise AuthorityError(state["voice_error"])
                if (status.get("online") and status.get("voice_connected")
                        and status.get("runtime_id") == runtime and str(status.get("voice_channel_id")) == channel
                        and status.get("voice_authority_generation") == self.current["generation"]):
                    with self.lock:
                        grant_checked_at = time.monotonic()
                        self.current = self.record.change(request_id, self.client, phases={"joining"}, phase="active")
                        self._observe(self.current, status, grant_checked_at)
                    self._notify(self.current)
                    result, message = True, "관리자 · 송출 중 (로컬 스케줄 유지 · 동기화는 수동)"
                    return
                if (state.get("runtime_id") != runtime or state.get("authority_request") != request_id
                        or state.get("authority_needs_sync") or state.get("standby")):
                    raise AuthorityError("로컬 연결 또는 담당자 상태가 변경되었습니다. 재검증 후 다시 요청하세요.")
                time.sleep(1)
            raise AuthorityError("음성채널 입장에 실패했습니다. 봇 연결은 유지하고 권한을 반납합니다.")
        except Exception as exc:
            message = str(exc)
            if request_id:
                self._cancel_incoming(request_id, runtime, reason=message)
        finally:
            if call:
                self._answer_remote_call(call, result, message)
            self._ui(lambda: self._finish(message, done, result,
                retry=(lambda: self.start(remote=remote, channel=channel, done=done,
                    expected_owner=expected_owner, attempt=attempt + 1))
                if not remote and not result and "승계 취소" not in message else None,
                attempt=attempt), wait=False)

    def _wait_local_stop(self, runtime, request_id):
        local = self.policy.snapshot()
        if local.get("runtime_id") != runtime:
            raise AuthorityError("종료할 봇의 실행 세션이 변경됐습니다.")
        self.policy.request_stop(runtime, request_id)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            status = self._status()
            if (status.get("runtime_id") == runtime and status.get("online")
                    and not status.get("voice_connected") and status.get("voice_disconnect_confirmed")):
                return
            time.sleep(.1)
        raise AuthorityError("음성 연결의 실제 종료를 확인하지 못했습니다. 기존 봇·음성채널 상태를 확인한 뒤 재시도하세요.")

    def _outgoing(self, data, entry):
        token = self.outgoing_progress_token = data["request"]
        runtime = self.outgoing_progress_runtime = data["owner_runtime"]
        self.outgoing_progress_done = False
        self._progress("outgoing", token, "stop", "기존 음성 송출을 종료하고 Discord 종료 응답을 확인합니다.", create=True)
        try:
            self._wait_local_stop(runtime, token)
            with self.lock:
                stopped = self.record.acknowledge_stop(token, self.client, runtime)
            self._notify(stopped)
        except Exception as exc:
            self.app._append_debug_log(f"handover_stop_verify_failed request={token} error={exc!r}")
            self._ui(lambda message=str(exc): self.app._show_centered_messagebox(
                "showerror", "승계 종료 확인 실패", message + "\n현재 담당자·음성채널 상태를 확인하고 새 요청으로 재시도하세요.",
                parent=self.app.schedule_window or self.app.root), wait=False)
            return
        # Upload is a separate, bounded operation; it cannot delay or revoke the
        # acknowledged ownership transfer and never renews voice authority.
        self.outgoing_progress_done = True
        self.outgoing_progress_result = "음성 종료 확인 완료 · 새 담당자에게 승계"
        snapshot = None
        try:
            snapshot = self._ui(lambda: deepcopy(self.app._build_github_schedule_payload("0.0.0")))
            snapshot["payload"].update(share_prefix=str(entry["id"]), server_name=entry["name"])
            deadline = time.monotonic() + 45
            def may_upload():
                self._assert_profile()
                if time.monotonic() >= deadline:
                    return False
                with self.lock:
                    current, _ = self.record.read()
                return (current.get("request") == token and current.get("phase") in {"requested", "joining", "active"}
                        and current.get("owner") in {data["owner"], data["receiver"]}
                        and int(current.get("generation", 0)) in {data["generation"], data["generation"] + 1}
                        and time.monotonic() < deadline)
            ok, message, _ = self.app._upload_current_schedule_to_github_data(entry,
                handover_schedule_payload=snapshot, handover_guard=may_upload,
                progress_callback=lambda message: self._progress("outgoing", token, "upload", message))
        except Exception as exc:
            ok, message = False, str(exc)
        self.app._append_debug_log(f"authority_handover_upload_result request={token} success={int(ok)} detail={message}")
        self._progress("outgoing", token, "voice", "음성 종료 확인 완료 · 업로드 결과: " + str(message))
        self._ui(lambda: self.app.schedule_status_var.set("승계 종료 확인 완료 · 업로드: " + str(message))
                 if self.outgoing_progress_token == token else None, wait=False)
        if not ok and snapshot is not None:
            self._ui(lambda: self._offer_upload_retry(entry, snapshot, str(message)), wait=False)

    def _offer_upload_retry(self, entry, snapshot, error, attempt=0):
        from operation_retry import offer_verified_retry
        offer_verified_retry(self.app, "인계 자료 업로드 실패", error,
            lambda: threading.Thread(target=self._retry_upload,
                args=(deepcopy(entry), deepcopy(snapshot), attempt + 1), daemon=True).start(), attempt=attempt)

    def _retry_upload(self, entry, snapshot, attempt):
        try:
            self._assert_profile()
            with self.lock:
                current, _ = self.record.read()
            stamp = self.record.owner_stamp(current)
            deadline = time.monotonic() + 45
            def still_current():
                self._assert_profile()
                if time.monotonic() >= deadline:
                    return False
                with self.lock:
                    latest, _ = self.record.read()
                return self.record.owner_stamp(latest) == stamp and time.monotonic() < deadline
            ok, message, _ = self.app._upload_current_schedule_to_github_data(entry,
                handover_schedule_payload=deepcopy(snapshot), handover_guard=still_current)
        except Exception as exc:
            ok, message = False, str(exc)
        self._ui(lambda: self.app.schedule_status_var.set(str(message)), wait=False)
        if not ok:
            self._ui(lambda: self._offer_upload_retry(entry, snapshot, str(message), attempt), wait=False)

    def _cancel_incoming(self, request_id, runtime, reason="승계 연결 실패"):
        try:
            # A failed join must close its local voice session even when GitHub
            # is down. Do not touch a newer request or another bot runtime.
            local = self.policy.snapshot()
            if (local.get("runtime_id") == runtime and local.get("authority_request") == request_id
                    and local.get("voice_requested")):
                try:
                    self._wait_local_stop(runtime, request_id)
                finally:
                    self.policy.block_request(request_id, runtime)
            with self.lock:
                current, _ = self.record.read()
            if (current.get("request") == request_id and current.get("owner") == self.client
                    and current.get("owner_runtime") == runtime):
                self._release(request_id=request_id, runtime=runtime, failure=reason)
                return
            def cancel(data):
                if (data.get("request") == request_id and data.get("phase") == "requested"
                        and data.get("receiver") == self.client and data.get("receiver_runtime") == runtime):
                    data.update(phase="active", receiver="", receiver_runtime="", released=False, release_proof={},
                                generation=int(data["generation"]) + 1, request=uuid.uuid4().hex,
                                failure=reason, failure_request=request_id)
            with self.lock:
                restored = self.record.mutate(cancel)
            self._notify(restored)
        except Exception as exc:
            self.app._append_debug_log(f"handover_cancel_verify_failed request={request_id} error={exc!r}")

    def _release(self, *, request_id="", runtime=None, stopped=False, failure=""):
        runtime = self.runtime if runtime is None else runtime
        if not stopped:
            self._wait_local_stop(runtime, request_id or self.policy.snapshot()["authority_request"])
        self.policy.block_request(request_id, runtime)
        def update(data):
            member = data["members"].get(self.client, {})
            if stopped and member.get("runtime") == runtime:
                member["online"] = False
            if (data.get("owner") == self.client and data.get("owner_runtime") == runtime
                    and (not request_id or data.get("request") == request_id)):
                if data.get("phase") == "requested":
                    data.update(released=True, release_proof=dict(kind="voice_stopped", client=self.client,
                                runtime=runtime, request=data["request"]))
                else:
                    data.update(owner="", owner_runtime="", owner_application_id="", owner_bot_user_id="", phase="idle", receiver="",
                                generation=int(data["generation"]) + 1,
                                failure=failure, failure_request=request_id if failure else "")
        with self.lock:
            released = self.record.mutate(update)
        if not stopped:
            self._notify(released)

    def release_after_stop(self):
        if getattr(self.app, "discord_bot_running", True):
            raise AuthorityError("봇 프로세스 종료를 확인하지 못했습니다.")
        try:
            self._release(stopped=True)
        except Exception as exc:
            self.app._append_debug_log(f"stopped_authority_release_failed error={exc!r}")
            self._ui(lambda message=str(exc): self.app._show_centered_messagebox("showerror", "종료 기록 확인",
                "봇은 종료했지만 담당자 기록 반납에 실패했습니다.\n" + message
                + "\n다음 연결 시 이전 봇의 종료를 확인하고 현재 기록을 재검증하세요.",
                parent=self.app.schedule_window or self.app.root), wait=False)

    def release(self):
        if self.releasing:
            return
        local = self.policy.snapshot()
        self.releasing = True
        def worker():
            message = "봇 연결됨 · 권한 없음"
            try:
                self._release(request_id=local["authority_request"], runtime=local["runtime_id"])
            except Exception as exc:
                message = "권한 반납 확인 실패: " + str(exc)
                self._ui(lambda text=message: self.app._show_centered_messagebox("showerror", "권한 반납",
                    text + "\n연결·서버 상태를 확인하고 다시 반납하세요.",
                    parent=self.app.schedule_window or self.app.root), wait=False)
            finally:
                self.releasing = False
                self._ui(lambda: self.app.schedule_status_var.set(message), wait=False)
        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, message, done, result, retry=None, attempt=0):
        self._finish_progress("incoming", self.incoming_progress_token, message, success=result, close=not result)
        self.busy = False
        self.app.discord_handover_busy = False
        self.app.schedule_status_var.set(message)
        if done:
            done(result, message)
        elif message and not result and "승계 취소" not in message:
            from operation_retry import offer_verified_retry
            offer_verified_retry(self.app, "관리자 접속 실패", message, retry, attempt=attempt)

    @staticmethod
    def format_members(data):
        members = AuthorityRecord.online_members(data)
        if not members:
            return "접속한 관리자가 없습니다."
        rows = ["접속한 관리자 (관리자 ID는 프로그램 설치별 ID입니다.)",
                "접속 요청: /보탐 담당자이름 (예: /보탐 나츠) · 이름 중복 시 /보탐 ID"]
        for client, member in sorted(members.items()):
            length = 8
            while length < 32 and any(other != client and other[:length].lower() == client[:length].lower() for other in members):
                length += 1
            owns = data.get("owner") == client and data.get("owner_runtime") == member.get("runtime")
            state = "입장 중" if owns and data.get("phase") == "joining" else "송출 중" if owns and data.get("phase") == "active" else "권한 없음"
            rows.append(f"{member.get('name', '담당자')} · {client[:length]} · {member.get('server', '')} · {member.get('season', '')}차 · {state}")
        return "\n".join(rows)

    def _publish_query_preference(self):
        """Write only when failover changes; normal queries never write GitHub."""
        query_data, _ = self.query_ledger.read()
        generation = int(query_data.get("routing_generation", 0))
        preferred = deepcopy(query_data.get("responder", {}))
        def change(record):
            if int(record.get("query_routing_generation", -1)) <= generation:
                record.update(query_responder=preferred, query_routing_generation=generation)
        with self.lock:
            self._assert_profile()
            checked_at = time.monotonic()
            data = self.record.mutate(change)
            self._observe(data, self._status(), checked_at)
        self._notify(data)

    def _warm_query_preference(self, *, clear=None, generation=-1):
        if self.query_preference_warming:
            return
        self.query_preference_warming = True
        def worker():
            try:
                if clear:
                    self.query_ledger.clear_responder(clear, generation)
                self._publish_query_preference()
            except Exception as exc:
                self.app._append_debug_log(f"query_preference_warm_failed error={exc!r}")
            finally:
                self.query_preference_warming = False
        threading.Thread(target=worker, daemon=True, name="discord-query-preference").start()

    def _query_command(self, payload, done):
        """Every receiving bot participates; only the Discord ack winner opens a request."""
        def worker():
            try:
                query_id = str(payload.get("query_id") or "")
                if not query_id.isdigit() or len(query_id) > 24:
                    raise QueryRoutingError("조회 요청 번호가 올바르지 않습니다.")
                coordinator = payload["operation"] in {"readonly_query_route", "administrator_query_route"}
                if coordinator:
                    with self.lock:
                        data, _ = self.record.read()
                    candidates = self.record.query_members(data)
                    application_id = str(payload.get("application_id") or self._status().get("application_id") or "")
                    candidates = [member for member in candidates if str(member.get("application_id") or "") == application_id]
                    entry = self.query_ledger.open(query_id, payload.get("query"), candidates)
                else:
                    status = self._status()
                    if not status.get("online") or status.get("runtime_id") != payload.get("runtime_id"):
                        done(True, dict(state="finished"))
                        return
                    entry = self.query_ledger.offer(query_id, payload.get("query"), self.client, status["runtime_id"])
                finish = time.monotonic() + REQUEST_TTL - 5
                opening_deadline = time.monotonic() + 12
                reported = -1
                published = -1
                while self.alive and time.monotonic() < finish:
                    self._assert_profile()
                    if (not entry or entry.get("state") == "opening") and time.monotonic() >= opening_deadline:
                        done(True, dict(state="finished"))
                        return
                    if entry:
                        if coordinator:
                            if entry.get("retries") and published != len(entry["retries"]):
                                try:
                                    self._publish_query_preference()
                                except Exception as exc:
                                    self.app._append_debug_log(f"query_preference_publish_failed error={exc!r}")
                                    self._warm_query_preference()
                                published = len(entry["retries"])
                            if reported != len(entry.get("retries", [])):
                                reported = len(entry.get("retries", []))
                                done(True, dict(state="progress", retries=entry.get("retries", [])))
                            if entry["state"] != "pending":
                                done(True, dict(state=entry["state"], retries=entry.get("retries", [])))
                                return
                            if self.query_ledger.offered(entry):
                                entry = self.query_ledger.choose(query_id, entry["index"])
                                continue
                            if time.time() >= entry["deadline"]:
                                entry = self.query_ledger.advance(query_id, entry["index"])
                                continue
                        else:
                            if entry["state"] == "ready":
                                source = entry["source"]
                                selected = (source.get("client_id") == self.client
                                            and source.get("runtime") == payload.get("runtime_id"))
                                done(True, dict(state="selected" if selected else "finished", source=source,
                                                retries=entry.get("retries", []),
                                                routing_generation=entry.get("routing_generation", -1)))
                                return
                            if entry["state"] not in {"pending", "opening"}:
                                done(True, dict(state="finished"))
                                return
                    time.sleep(.25)
                    entry = self.query_ledger.entry(query_id)
                done(False, "조회 응답을 받지 못했습니다. 관리자를 변경하고 다시 시도하세요.")
            except Exception as exc:
                done(False, str(exc))
        threading.Thread(target=worker, daemon=True, name="discord-query-routing").start()

    def command(self, payload, done):
        if payload.get("operation") in {"readonly_query_route", "readonly_query_wait", "administrator_query_route", "administrator_query_wait"}:
            self._query_command(payload, done)
            return
        if payload.get("operation") not in {"administrator_list", "administrator_connect", "administrator_release"}:
            done(False, "지원하지 않는 관리자 요청입니다.")
            return
        def worker():
            try:
                with self.lock:
                    data, _ = self.record.read()
                if payload["operation"] == "administrator_list":
                    done(True, self.format_members(self._live_data(data)))
                    return
                if payload["operation"] == "administrator_release":
                    self.release()
                    done(True, "권한을 반납합니다. 봇 연결은 유지합니다.")
                    return
                target = self.record.resolve_member(self._live_data(data),
                    payload.get("target_administrator") or payload.get("target_client_id") or self.client)
                call_id = str(payload["request_id"])
                def publish(record):
                    now = time.time()
                    if target == self.client and (self.busy or self.releasing):
                        raise AuthorityError("대상 관리자가 다른 승계 또는 권한 반납을 처리 중입니다.")
                    if record.get("phase") in {"joining", "requested"} and now < record.get("deadline", 0):
                        raise AuthorityError("다른 관리자 승계가 진행 중입니다.")
                    member = self.record.online_members(record).get(target)
                    if not member:
                        raise AuthorityError("대상 관리자 연결이 끊겼습니다.")
                    previous = member.get("call", {})
                    if previous.get("state") in {"pending", "accepted"} and now < previous.get("expires", 0):
                        raise AuthorityError("대상 관리자가 다른 요청을 처리 중입니다.")
                    member["call"] = dict(id=call_id, runtime=member["runtime"], channel=str(payload.get("voice_channel_id") or ""),
                                          state="pending", expires=now + HANDOVER_SECONDS)
                with self.lock:
                    published = self.record.mutate(publish)
                    if target == self.client:
                        call = deepcopy(published["members"][target]["call"])
                        self.calls.add(call_id)
                        self._ui(lambda call=call: self.start(remote=True, call=call), wait=False)
                self._notify(published)
                acknowledgement = time.monotonic() + HANDOVER_SECONDS
                finish = time.monotonic() + HANDOVER_SECONDS + JOIN_SECONDS + 10
                while time.monotonic() < finish:
                    time.sleep(.5)
                    with self.lock:
                        latest, _ = self.record.read()
                    call = latest["members"].get(target, {}).get("call", {})
                    if call.get("id") != call_id:
                        raise AuthorityError("원격 호출이 변경되었습니다.")
                    if call.get("state") == "done":
                        done(bool(call.get("ok")), str(call.get("message") or "요청 완료"))
                        return
                    if call.get("state") == "pending" and time.monotonic() >= acknowledgement:
                        raise AuthorityError("대상 관리자가 응답하지 않았습니다. 기존 담당자의 권한은 유지합니다.")
                raise AuthorityError("입장 완료 응답을 받지 못했습니다. 관리자 목록에서 상태를 확인하세요.")
            except Exception as exc:
                done(False, str(exc))
        threading.Thread(target=worker, daemon=True, name="discord-administrator-command").start()


def write_command_result(request_dir, request_id, ok, message):
    """Results are separate from the existing GUI request inbox."""
    if not request_id or any(c not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-" for c in request_id):
        return
    folder = Path(request_dir) / "results"
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / (request_id + ".json")
    temporary = folder / (request_id + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(dict(ok=ok, message=message), ensure_ascii=False), encoding="utf-8")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
