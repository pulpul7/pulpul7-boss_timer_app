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

POLL_SECONDS = 5
HANDOVER_SECONDS = 10
JOIN_SECONDS = 30
PRESENCE_SECONDS = 20
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
        if data is None or (isinstance(data, dict) and data.get("schema") == 1 and not data.get("owner")):
            data = dict(schema=CONTROL_PROTOCOL, scope=self.scope, owner="", owner_runtime="",
                        generation=0, phase="idle", members={}, request="", receiver="")
        elif isinstance(data, dict) and data.get("schema") == 1:
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
        if error or sha != expected_sha or not isinstance(data, dict) or data.get("schema") != 1:
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
            callback(data)
            data["updated"] = time.time()
            ok, error = self.put(self.path, data, sha=sha, message="BossTimer administrator authority")
            if ok:
                return data
            if attempt == 2 or not any(code in str(error) for code in ("409", "422")):
                raise AuthorityError(error or "관리자 기록이 변경되었습니다. 다시 요청하세요.")

    @staticmethod
    def online_members(data, now=None):
        now = time.time() if now is None else now
        return {key: value for key, value in data.get("members", {}).items()
                if isinstance(value, dict) and value.get("online") and value.get("protocol") == CONTROL_PROTOCOL
                and -5 <= now - float(value.get("seen", 0)) < PRESENCE_TTL}

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

    def request(self, client, runtime, channel, *, call_id="", expected_owner=None):
        def change(data):
            now = time.time()
            if expected_owner is not None and not (
                    data.get("owner") == client and data.get("owner_runtime") == expected_owner[0]
                    and data.get("generation") == expected_owner[1] and data.get("phase") == "active"):
                raise AuthorityError("관리자 권한이 변경되어 이전 연결 복구를 취소했습니다.")
            if data.get("phase") in {"requested", "joining"} and now < data.get("deadline", 0):
                raise AuthorityError("다른 관리자 승계가 진행 중입니다. 완료 후 다시 요청하세요.")
            member = data["members"].get(client, {})
            if member.get("runtime") != runtime or client not in self.online_members(data):
                raise AuthorityError("대상 봇의 연결 상태가 변경되었습니다.")
            if call_id:
                call = member.get("call", {})
                if call.get("id") != call_id or now >= call.get("expires", 0):
                    raise AuthorityError("원격 호출의 응답시간이 초과되었습니다.")
                call.update(state="accepted")
            solo = not data.get("owner") or data.get("owner") == client
            data.update(scope=self.scope, receiver=client, receiver_runtime=runtime,
                        generation=int(data.get("generation", 0)) + 1,
                        request=uuid.uuid4().hex, phase="joining" if solo else "requested",
                        deadline=now + (JOIN_SECONDS if solo else HANDOVER_SECONDS),
                        released=solo, channel=channel, upload="대기")
            if solo:
                data.update(owner=client, owner_runtime=runtime)
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
        self.alive = True
        self.releasing = False
        self.poll_inflight = False
        self.last_presence = 0
        self.runtime = ""
        self.current = None
        self.outgoing_requests = set()
        self.calls = set()
        self.lock = threading.RLock()
        app.root.after(100, self._drain)
        app.root.after(500, self._poll)

    def _assert_profile(self):
        if (not self.alive or self.app._get_discord_bot_config_storage_path() != self.profile
                or str(self.app.discord_bot_server_id) != self.scope["guild"]):
            raise AuthorityError("Discord 설정이 변경되어 요청을 중단했습니다.")

    def _ui(self, callback, wait=True):
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
        if not done.wait(HANDOVER_SECONDS):
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

    def _observe(self, data, status, checked_at=None):
        self._assert_profile()
        if not status.get("runtime_id"):
            # A single status-port timeout must not revoke a valid lease.
            # Stop renewing it; the bot still stops within its original 10s.
            return
        self.runtime = str(status["runtime_id"])
        local = self.policy.snapshot()
        owns = (data.get("owner") == self.client and data.get("owner_runtime") == self.runtime
                and data.get("phase") in {"joining", "active"}
                and not (local.get("blocked_request") and local["blocked_request"] == data.get("request")))
        # Lease begins before the read: delayed old responses cannot extend it.
        values = dict(protocol=CONTROL_PROTOCOL, client_id=self.client, runtime_id=self.runtime,
                      standby=not owns, handover_hold=False, handover_role="",
                      authority_generation=int(data.get("generation", 0)),
                      authority_request=str(data.get("request") or ""),
                      authority_active=owns and data.get("phase") == "active",
                      authority_until=(checked_at or time.monotonic()) + AUTHORITY_SECONDS if owns else 0,
                      voice_requested=owns and not self.releasing)
        if owns:
            values["voice_channel_id"] = str(data.get("channel") or "")
        online = self.record.online_members(data)
        responder = data.get("owner") if data.get("owner") in online else min(online, default="")
        values["command_sync_allowed"] = responder == self.client
        query_generation = int(data.get("query_routing_generation", -1))
        preferred = data.get("query_responder") or {}
        if local.get("query_routing_generation", -1) > query_generation:
            query_generation = local["query_routing_generation"]
            preferred = local.get("query_responder") or {}
        values["query_members"] = QueryLedger.order_candidates(self.record.query_members(data), preferred)
        values["query_responder"] = preferred
        values["query_routing_generation"] = query_generation
        values["query_until"] = (checked_at or time.monotonic()) + AUTHORITY_SECONDS
        values["query_protocol"] = 1
        with self.lock:
            local = self.policy.snapshot()
            if (local.get("runtime_id") == self.runtime
                    and local.get("authority_generation", 0) > values["authority_generation"]):
                return
            self.policy.update(**values)

    def _presence(self, data, status, metadata):
        now = time.monotonic()
        member = data["members"].get(self.client, {})
        online = bool(status.get("online"))
        runtime = str(status.get("runtime_id") or "")
        connected_at = float(status.get("connected_at") or 0)
        if (now - self.last_presence < PRESENCE_SECONDS and member.get("online") == online
                and member.get("runtime") == runtime and member.get("connected_at", 0) == connected_at):
            return data
        def update(record):
            previous = record["members"].get(self.client, {})
            record["members"][self.client] = dict(previous, **metadata, online=online,
                runtime=runtime, connected_at=connected_at, seen=time.time(), protocol=CONTROL_PROTOCOL)
        with self.lock:
            data = self.record.mutate(update)
        self.last_presence = now
        return data

    def _poll(self):
        if not self.alive:
            return
        try:
            self._assert_profile()
        except AuthorityError:
            self.alive = False
            return
        if self.poll_inflight:
            # A slow presence update must not postpone the next authority
            # check by a whole additional five-second interval.
            self.app.root.after(500, self._poll)
            return
        identity = self.app._get_administrator_identity()
        entry = dict(self.app._get_current_github_upload_server_entry())
        metadata = dict(name=identity.get("name") or "담당자 미등록",
                        server=str(entry.get("name") or entry["id"]), season=str(self.app.current_season_no))
        self.scope.update(server=str(entry["id"]), season=metadata["season"])
        self.record.scope = dict(self.scope)
        if not self.poll_inflight:
            self.poll_inflight = True
            def worker():
                try:
                    with self.lock:
                        checked_at = time.monotonic()
                        data, _ = self.record.read()
                        status = self._status()
                        local = self.policy.snapshot()
                        if (status.get("runtime_id") and local.get("voice_error")
                                and data.get("phase") == "active" and data.get("owner") == self.client
                                and data.get("owner_runtime") == status["runtime_id"]
                                and data.get("request") == local.get("authority_request")):
                            self._release(request_id=data["request"], runtime=status["runtime_id"])
                            checked_at = time.monotonic()
                            data, _ = self.record.read()
                        self._observe(data, status, checked_at)
                        previous = data
                        data = self._presence(data, status, metadata)
                        if data is not previous:
                            self._observe(data, status, checked_at)
                    if "query_responder" not in data:
                        self._warm_query_preference()
                    else:
                        preferred = data.get("query_responder") or {}
                        member = self.record.online_members(data).get(preferred.get("client_id"), {})
                        if preferred and member.get("runtime") != preferred.get("runtime"):
                            self._warm_query_preference(clear=preferred, generation=int(data.get("query_routing_generation", -1)))
                    if (data.get("owner") == self.client and data.get("owner_runtime") == self.runtime
                            and data.get("phase") == "requested" and data["request"] not in self.outgoing_requests):
                        self.outgoing_requests.add(data["request"])
                        threading.Thread(target=self._outgoing, args=(deepcopy(data), entry), daemon=True).start()
                    call = data["members"].get(self.client, {}).get("call", {})
                    if (status.get("online") and call.get("state") == "pending"
                            and call.get("runtime") == self.runtime and time.time() < call.get("expires", 0)
                            and call.get("id") not in self.calls):
                        self.calls.add(call["id"])
                        self._ui(lambda: self.start(remote=True, call=call), wait=False)
                except Exception as exc:
                    self.app._append_debug_log(f"authority_poll_failed type={type(exc).__name__} error={exc!r}")
                finally:
                    self.poll_inflight = False
            threading.Thread(target=worker, daemon=True, name="discord-authority-poll").start()
        self.app.root.after(POLL_SECONDS * 1000, self._poll)

    def start(self, *, remote=False, call=None, channel="", done=None, expected_owner=None):
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
        threading.Thread(target=self._incoming, args=(remote, call, channel, done, expected_owner), daemon=True).start()

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

    def _incoming(self, remote, call, channel, done, expected_owner):
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
            try:
                with self.lock:
                    existing, _ = self.record.read()
            except LegacyAuthorityError as exc:
                if remote:
                    raise
                if not self._ui(lambda: self.app._show_centered_messagebox("askyesno", "구버전 연결 종료 확인",
                    "구버전의 담당 기록이 남아 있습니다.\n모든 PC의 구버전 프로그램과 봇을 종료했나요?\n"
                    "확인하면 권한 기록만 새 구조로 전환합니다. 설정과 스케줄은 보존합니다.",
                    parent=self.app.schedule_window or self.app.root)):
                    message = "봇 연결됨 · 권한 없음 (승계 취소)"
                    return
                with self.lock:
                    self.record.migrate_legacy(exc.sha)
                    existing, _ = self.record.read()
            owner = self.record.online_members(existing).get(existing.get("owner"), {})
            if (owner.get("runtime") == existing.get("owner_runtime") and existing.get("owner")
                    and existing.get("owner") != self.client and not remote):
                if not self._ui(lambda: self.app._show_centered_messagebox("askyesno", "관리자 승계",
                    "다른 관리자가 운영 중입니다. 권한을 승계하고 음성채널에 입장할까요?\n"
                    "아니오를 선택하면 봇 연결만 유지합니다. 스케줄은 자동 동기화하지 않습니다.",
                    parent=self.app.schedule_window or self.app.root)):
                    message = "봇 연결됨 · 권한 없음 (승계 취소)"
                    return
            channel = str((call or {}).get("channel") or channel or self.app.discord_bot_voice_channel_id)
            if not channel.isdigit():
                raise AuthorityError("입장할 음성채널을 설정하세요.")
            with self.lock:
                member = existing["members"].get(self.client, {})
                if member.get("runtime") != runtime or self.client not in self.record.online_members(existing):
                    identity = self._ui(self.app._get_administrator_identity)
                    self.last_presence = 0
                    existing = self._presence(existing, status, dict(name=identity.get("name") or "담당자 미등록",
                        server=self.scope["server"], season=self.scope["season"]))
                grant_checked_at = time.monotonic()
                self.current = self.record.request(self.client, runtime, channel,
                    call_id=(call or {}).get("id", ""), expected_owner=expected_owner)
            request_id = self.current["request"]
            wait_until = time.monotonic() + HANDOVER_SECONDS
            while self.current["phase"] == "requested":
                if self.current.get("released") or time.monotonic() >= wait_until:
                    with self.lock:
                        grant_checked_at = time.monotonic()
                        latest, _ = self.record.read()
                        if latest.get("request") != request_id:
                            raise AuthorityError("다른 승계 요청으로 변경되었습니다.")
                        self.current = self.record.change(request_id, self.client, phases={"requested"},
                            owner=self.client, owner_runtime=runtime, generation=int(latest["generation"]) + 1,
                            phase="joining", released=True, deadline=time.time() + JOIN_SECONDS)
                    break
                time.sleep(.5)
                with self.lock:
                    self.current, _ = self.record.read()
                if self.current.get("request") != request_id:
                    raise AuthorityError("다른 승계 요청으로 변경되었습니다.")
            with self.lock:
                self.policy.resume()
                self._observe(self.current, self._status(), grant_checked_at)
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
                    result, message = True, "관리자 · 송출 중 (로컬 스케줄 유지 · 동기화는 수동)"
                    return
                with self.lock:
                    checked_at = time.monotonic()
                    data, _ = self.record.read()
                    if data.get("request") != request_id or data.get("owner_runtime") != runtime:
                        raise AuthorityError("권한이 다른 실행 세션으로 변경되었습니다.")
                    self._observe(data, status, checked_at)
                time.sleep(1)
            raise AuthorityError("음성채널 입장에 실패했습니다. 봇 연결은 유지하고 권한을 반납합니다.")
        except Exception as exc:
            message = str(exc)
            if request_id:
                self._release(request_id=request_id, runtime=runtime)
        finally:
            if call:
                self._answer_remote_call(call, result, message)
            self._ui(lambda: self._finish(message, done, result), wait=False)

    def _outgoing(self, data, entry):
        # _observe already revoked this lease; never pause a later local owner.
        completed = threading.Event()
        outcome = ["업로드 제한시간 초과"]
        deadline = time.monotonic() + max(0, min(HANDOVER_SECONDS, data.get("deadline", time.time()) - time.time()))
        def may_upload():
            if time.monotonic() >= deadline:
                return False
            with self.lock:
                current, _ = self.record.read()
            return (current.get("request") == data["request"] and current.get("phase") == "requested"
                    and current.get("owner_runtime") == data.get("owner_runtime")
                    and time.monotonic() < deadline)
        def upload():
            try:
                expected = self._ui(lambda: deepcopy(self.app._build_github_schedule_payload("0.0.0")))
                expected["payload"].update(share_prefix=str(entry["id"]), server_name=entry["name"])
                ok, message, _ = self.app._upload_current_schedule_to_github_data(entry,
                    handover_schedule_payload=expected, handover_guard=may_upload)
                outcome[0] = "성공" if ok else "실패: " + message
            except Exception as exc:
                outcome[0] = "실패: " + str(exc)
            finally:
                completed.set()
        threading.Thread(target=upload, daemon=True, name="discord-handover-upload").start()
        completed.wait(max(0, min(HANDOVER_SECONDS, data.get("deadline", time.time()) - time.time())))
        try:
            with self.lock:
                self.record.change(data["request"], self.client, phases={"requested"}, released=True, upload=outcome[0])
        except AuthorityError:
            pass  # A late upload cannot update a new authority generation.
        self._ui(lambda: self.app.schedule_status_var.set("봇 연결됨 · 권한 없음 · 인계 업로드 " + outcome[0]), wait=False)

    def _release(self, *, request_id="", runtime=None):
        runtime = self.runtime if runtime is None else runtime
        with self.lock:
            self.policy.block_request(request_id, runtime)
            def update(data):
                if (data.get("owner") == self.client and data.get("owner_runtime") == runtime
                        and (not request_id or data.get("request") == request_id)):
                    if self.policy.snapshot().get("runtime_id") == runtime:
                        self.policy.pause()
                    if data.get("phase") == "requested":
                        data["released"] = True
                    else:
                        data.update(owner="", owner_runtime="", phase="idle", receiver="", generation=int(data["generation"]) + 1)
            try:
                self.record.mutate(update)
            except Exception as exc:
                self.app._append_debug_log(f"authority_release_failed type={type(exc).__name__} error={exc!r}")

    def release_after_stop(self):
        self.release()

    def release(self):
        if self.releasing:
            return
        local = self.policy.snapshot()
        release_request, release_runtime = local["authority_request"], local["runtime_id"]
        self.releasing = True
        self.policy.update(voice_requested=False)
        def worker():
            try:
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline and self._status().get("voice_connected"):
                    time.sleep(.1)
                self._release(request_id=release_request, runtime=release_runtime)
            finally:
                self.releasing = False
                self._ui(lambda: self.app.schedule_status_var.set("봇 연결됨 · 권한 없음"), wait=False)
        threading.Thread(target=worker, daemon=True).start()

    def _finish(self, message, done, result):
        self.busy = False
        self.app.discord_handover_busy = False
        self.app.schedule_status_var.set(message)
        if done:
            done(result, message)
        elif message and not result and "승계 취소" not in message:
            self.app._show_centered_messagebox("showerror", "관리자 접속", message, parent=self.app.schedule_window or self.app.root)

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
                    entry = self.query_ledger.open(query_id, payload.get("query"), self.record.query_members(data))
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
                    done(True, self.format_members(data))
                    return
                if payload["operation"] == "administrator_release":
                    self.release()
                    done(True, "권한을 반납합니다. 봇 연결은 유지합니다.")
                    return
                target = self.record.resolve_member(data,
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
