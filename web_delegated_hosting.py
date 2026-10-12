"""Split hosting. Verified authority events drive downloads, never polling.

The voice owner edits; the designated host only publishes detached web snapshots.
No Tk prompts and no network calls on Tk or on the local lease-renewal path.
"""
from copy import deepcopy
from collections import deque
import hashlib
import json
import re
import threading
import time
import uuid

from discord_connection_policy import ConnectionPolicy
from web_service_common import timestamp
from web_service_common import read_json
from web_schedule_snapshot import validate_snapshot

SYNC_DELAY = 90
MAX_ATTEMPTS = 3
RETRY_DELAY = 15
ACK_TIMEOUT = 15
WEB_RELAY_PROTOCOL = 1


def valid_token(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def content_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def edit_hash(payload):
    # Captures/heartbeat and continuously moving window edges are not edits.
    values = {key: value for key, value in payload.items() if key not in
              {"captured_at", "updated_at", "revision", "window_start", "window_end"}}
    values["window_days"] = [payload.get(key, "")[:10] for key in ("window_start", "window_end")]
    return content_hash(values)


def delegation_info(data):
    item = data.get("web_delegate") if isinstance(data, dict) else None
    if not isinstance(item, dict) or item.get("protocol") != WEB_RELAY_PROTOCOL:
        return None
    if (not all(valid_token(item.get(key)) for key in ("id", "host", "host_runtime", "source", "source_runtime"))
            or item["host"] == item["source"] or item.get("generation") != data.get("generation")
            or item.get("request") != data.get("request")
            or item.get("state") not in {"active", "releasing", "stopped"}):
        return None
    phase = data.get("phase")
    if phase in {"requested", "joining"}:
        if data.get("receiver") != item["source"] or data.get("receiver_runtime") != item["source_runtime"]:
            return None
    elif phase == "active":
        if data.get("owner") != item["source"] or data.get("owner_runtime") != item["source_runtime"]:
            return None
    elif phase != "idle" or data.get("owner"):
        return None
    members = data.get("members", {})
    if not isinstance(members, dict) or any(not isinstance(members.get(item[key]), dict) for key in ("host", "source")):
        return None
    if any(members.get(item[key], {}).get("runtime") != item[key + "_runtime"] for key in ("host", "source")):
        return None
    if any(not isinstance(item.get(key), str) or not item[key] for key in ("guild", "server", "season")):
        return None
    if str(data.get("scope", {}).get("guild")) != item["guild"]:
        return None
    return deepcopy(item)


def build_delegation(data, client, runtime, scope, generation, request):
    """Called inside the voice authority CAS, before replacing the old owner."""
    previous = delegation_info(data)
    host = previous["host"] if previous else data.get("owner")
    host_runtime = previous["host_runtime"] if previous else data.get("owner_runtime")
    if not host and data.get("phase", "idle") == "idle":
        return None  # Releasing voice leaves no previous owner to preserve.
    if host == client:
        return None  # Taking voice back on the existing web host reunites roles.
    if data.get("members", {}).get(client, {}).get("web_delegate_protocol") != WEB_RELAY_PROTOCOL:
        raise ValueError("이 PC의 GUI와 봇을 함께 새 버전으로 교체한 뒤 다시 요청하세요.")
    member = data.get("members", {}).get(host, {})
    old_scope = previous or data.get("scope", {})
    if (data.get("phase") not in ({"active", "idle"} if previous else {"active"})
            or (previous and previous["state"] != "active") or not valid_token(host) or not valid_token(host_runtime)
            or not member.get("online") or member.get("runtime") != host_runtime
            or member.get("web_delegate_protocol") != WEB_RELAY_PROTOCOL):
        raise ValueError("기존 웹 담당자의 실행 연결을 확인하지 못했습니다. GUI와 봇을 함께 업데이트하고 다시 요청하세요.")
    if str(old_scope.get("server")) != str(scope["server"]) or str(old_scope.get("season")) != str(scope["season"]):
        raise ValueError("웹을 유지하는 분리 운영은 같은 시즌·게임 서버에서만 사용할 수 있습니다.")
    return dict(protocol=WEB_RELAY_PROTOCOL, id=uuid.uuid4().hex, host=host, host_runtime=host_runtime,
                source=client, source_runtime=runtime, generation=generation, request=request,
                guild=str(scope["guild"]), server=str(scope["server"]), season=str(scope["season"]), state="active")


def build_automatic_delegation(data, client, runtime, scope, generation, request):
    """A web-OFF receiver leaves an eligible existing host in place."""
    member = data.get("members", {}).get(data.get("owner"), {})
    if member.get("web_public_available") is False:
        return None  # Both PCs have web disabled: do not start a schedule relay.
    try:
        # Older split-capable GUIs do not advertise availability. Preserve their
        # possible live web service instead of revoking it without a replacement.
        return build_delegation(data, client, runtime, scope, generation, request)
    except ValueError:
        # First connection, offline/legacy peer or different season/server must
        # not block voice authority just because automatic web preservation is unavailable.
        return None


def artifact_path(delegate, transfer):
    if not valid_token(delegate) or not valid_token(transfer):
        raise ValueError("invalid web transfer")
    return f"data/web/delegated/{delegate}/{transfer}.json"


class DelegatedHosting:
    def __init__(self, bridge):
        self.bridge, self.app, self.manager = bridge, bridge.app, bridge.manager
        self.lock = threading.RLock()
        self.closed, self.wake = threading.Event(), threading.Event()
        self.info = self.coordinator = None
        self.record = {}
        self.role = ""
        self.split_for_next = False  # Explicit and one-shot; never inferred from network state.
        self.local_payload = self.remote_payload = None
        self.last_edit = 0.0
        self.latest_hash = self.sent_hash = ""
        self.sequence = self.applied_sequence = 0
        self.pending = None
        self.applied_transfer = None
        self.host_job = None
        self.notice = None
        self.return_settings = None
        self.requests = deque(maxlen=32)
        self.force = False
        self.release_done = False
        self.auto_return_started = False
        self.prepared_return = None
        self.auto_return_check = None
        self.restore_started = False
        self.peer_ready = None
        self.last_ready_sent = None
        self.last_applied_at = ""
        self.message = "일반 운영 · Discord와 웹을 함께 승계합니다."
        self.next_attempt = 0.0
        self.failures = 0
        self.worker_retry_at = 0.0
        self.worker_retries = 0
        self.public_retry_at = 0.0
        self.public_retries = 0
        self.thread = threading.Thread(target=self._loop, name="web-delegated-hosting", daemon=True)
        self.thread.start()

    def status(self):
        with self.lock:
            info, members = self.info or {}, self.record.get("members", {})
            def label(client):
                return (members.get(client, {}).get("name") or client[:8]) if client else "—"
            return dict(role=self.role, split_for_next=self.split_for_next, message=self.message,
                        host=label(info.get("host", "")), source=label(info.get("source", "")),
                         keep_host=bool(info.get("keep_host")), return_started=self.auto_return_started,
                         return_stage=getattr(getattr(self.bridge, "recovery", None), "stage", ""),
                        last_applied_at=self.last_applied_at or "아직 전달되지 않음", peer_ready=self.peer_ready)

    def choose_next(self, enabled):
        with self.lock:
            self.split_for_next = bool(enabled)

    def observe(self, data, coordinator):
        manual = getattr(self.bridge, "standalone", None)
        previous_manual = None
        if manual is not None:
            from web_standalone_hosting import standalone_info
            previous_manual = standalone_info(manual.record)
            manual.observe(data, coordinator)
        info = delegation_info(data) if not (manual and manual.suppresses_normal()) else None
        role = ("host" if info["host"] == coordinator.client else "source" if info["source"] == coordinator.client else "") if info else ""
        with self.lock:
            old_role = self.role
            old_info = self.info
            changed = (self.info or {}).get("id") != (info or {}).get("id") or role != self.role
            self.record, self.coordinator = deepcopy(data), coordinator
            self.info, self.role = info, role
            if changed:
                self.pending = None
                self.applied_transfer = self.host_job = self.notice = None
                self.return_settings = None
                self.latest_hash = self.sent_hash = ""
                self.sequence = self.applied_sequence = 0
                self.release_done = False
                self.auto_return_started = self.restore_started = False
                self.prepared_return = None
                self.peer_ready = self.last_ready_sent = None
                self.last_applied_at = ""
                self.failures, self.next_attempt = 0, 0
                self.worker_retry_at, self.worker_retries = 0, 0
                self.public_retry_at, self.public_retries = 0, 0
                self.force = False
                if role == "source" and (info or {}).get("restore_after_failure"):
                    self.force = True  # Send one fresh post-recovery snapshot without the edit debounce.
                self.requests.clear()
                self.local_payload = None
                with self.manager.lock:
                    self.remote_payload = deepcopy(self.manager.pending_snapshot) if role == "host" else None
                self.message = ("웹 담당 유지 · 스케줄 전달 대기" if role == "host" else
                                "Discord 송출 담당 · 기존 PC의 웹 준비 응답 대기" if role == "source" else
                                "일반 운영 · Discord와 웹을 함께 승계합니다.")
            with self.manager.lock:
                self.manager.delegated_snapshot_only = (False if manual and manual.suppresses_normal() else
                    (role == "host" or old_role == "host" or getattr(self.bridge, "retained_host", None) is not None))
            resumed_self = bool(previous_manual and previous_manual["state"] == "active"
                and previous_manual["owner"] == coordinator.client and previous_manual["runtime"] == coordinator.runtime
                and not (manual and manual.suppresses_normal()) and role != "host")
            if (old_role == "host" and role != "host") or resumed_self:
                try:
                    retain = getattr(self.bridge, "retain_web_host", None)
                    retained_info = (dict(previous_manual, host=previous_manual["owner"],
                                          host_runtime=previous_manual["runtime"]) if resumed_self else old_info)
                    manual_owns = bool(manual and manual.status()["local"] and manual.status()["state"] == "active")
                    if not manual_owns and not (callable(retain) and not (manual and manual.suppresses_normal())
                            and retain(data, retained_info, coordinator)):
                        with self.manager.lock:
                            self.manager.delegated_snapshot_only = role == "host"
                        self.manager.set_authority((), message="기존 웹 담당 종료 · 현재 담당 상태 확인 중")
                except OSError as exc:
                    self.manager._log("web_delegated_stop_lease_failed", error_type=type(exc).__name__)
        if changed:
            self.manager._log("web_delegation_role_changed", role=role, delegate=(info or {}).get("id", ""))
        recovery = getattr(self.bridge, "recovery", None)
        if recovery is not None:
            try:
                recovery.observe(data)
            except Exception as exc:
                self.manager._log("web_return_observe_failed", error_type=type(exc).__name__)
        observer = getattr(self.bridge, "observe_transfer", None)
        if callable(observer):
            observer(data, coordinator)
        self.wake.set()
        self.bridge.wake.set()

    def unattended(self, data=None):
        info = delegation_info(data) if data is not None else self.info
        coordinator = self.coordinator or getattr(self.app, "discord_handover", None)
        return bool(info and coordinator and info["host"] == coordinator.client)

    def needs_local_snapshot(self):
        with self.lock:
            return self.role == "source"

    def offer_local(self, payload):
        with self.lock:
            if self.role == "source":
                self.local_payload = payload
                self.wake.set()

    def host_snapshot(self):
        with self.lock:
            if self.role != "host" or not self.remote_payload:
                retained = getattr(self.bridge, "retained_host", None)
                return dict(retained[3], captured_at=timestamp()) if retained and retained[3] else None
            return dict(self.remote_payload, captured_at=timestamp())

    def tick(self):
        """Local health only; safe during remote uploads/downloads."""
        with self.lock:
            info, role, coordinator = self.info, self.role, self.coordinator
        if not info or role != "host" or info["state"] != "active" or not coordinator or not coordinator.alive:
            return
        state = coordinator.policy.snapshot()
        if (state.get("runtime_id") == info["host_runtime"] and state.get("query_until", 0) > time.monotonic()
                and str(self.app.current_season_no) == info["season"]
                and coordinator.scope["server"] == info["server"] and not self.manager.public_paused):
            key = (info["guild"], info["host"], info["host_runtime"], "web:" + info["id"])
            with self.lock:
                self._guard(info, "host")
                self.manager.set_authority(key, ready=self.manager.snapshot_version > 0,
                                           message="분리 운영 · Discord 권한과 별도로 기존 웹 담당 유지")

    def receive(self, event, data):
        """The caller already verified the durable record on this incoming event."""
        if event.get("kind") != "web":
            return
        with self.lock:
            info = self.info
            if (not info or event.get("delegate") != info["id"] or event.get("target") != self.coordinator.client
                    or event.get("target_runtime") != self.coordinator.runtime):
                return
            peer = info["source"] if self.role == "host" else info["host"]
            member = data.get("members", {}).get(peer, {})
            if (event.get("client") != peer or event.get("runtime") != member.get("runtime")
                    or str(event.get("sender_bot_id")) != str(member.get("bot_user_id") or member.get("application_id"))):
                return
            if self.role == "host" and event.get("operation") == "schedule":
                if (type(event.get("sequence")) is int and event["sequence"] >= self.applied_sequence
                        and valid_token(event.get("transfer")) and isinstance(event.get("hash"), str)
                        and re.fullmatch(r"[0-9a-f]{64}", event["hash"])):
                    self.requests.append(dict(event))
                    self.failures, self.next_attempt = 0, 0
            elif self.role == "source" and event.get("operation") == "ack":
                pending = self.pending
                if pending and event.get("transfer") == pending["transfer"] and event.get("sequence") == pending["sequence"]:
                    if event.get("ok") is True:
                        self.sent_hash = pending["edit_hash"]
                        self.pending = None
                        self.failures, self.next_attempt = 0, 0
                        self.last_applied_at = str(event.get("applied_at") or timestamp())[:64]
                        self.message = "기존 웹 담당자에 최신 스케줄 반영 완료"
                    else:
                        self.message = "웹 반영 응답 대기 · 자동 재시도 중"
            elif self.role == "source" and event.get("operation") == "ready":
                self.peer_ready = event.get("ready") is True
                self.message = "기존 웹 연결 확인 · 스케줄 변경 후 1분 30초 뒤 전달" if self.peer_ready else "기존 웹 연결 준비/재시도 중 · 분리 운영 유지"
            elif self.role == "host" and event.get("operation") == "preflight_failed" and info["state"] == "active":
                reason = str(event.get("reason") or "진단 로그를 확인해주세요.")[:300]
                phase = str(event.get("phase_name") or "prepare")[:40]
                self.message = "새 PC의 웹 승계 준비 실패 · 이 PC의 웹 유지 · " + reason
                self.manager._log("web_peer_preflight_failed", phase_name=phase, reason=reason)
        self.wake.set()

    def _guard(self, info, role, *, allow_joining=False):
        with self.lock:
            current, coordinator = self.info, self.coordinator
            if (self.closed.is_set() or self.manager.closed.is_set() or not current
                    or current != info or self.role != role or not coordinator or not coordinator.alive):
                raise ValueError("웹 담당자 또는 실행 세션이 변경되었습니다.")
        coordinator._assert_profile()
        if (str(self.app.current_season_no) != info["season"] or coordinator.scope["server"] != info["server"]
                or coordinator.scope["guild"] != info["guild"] or coordinator.runtime != info[role + "_runtime"]):
            raise ValueError("웹 분리 운영의 시즌·서버가 변경되었습니다.")
        if role == "source":
            state = coordinator.policy.snapshot()
            if (not ConnectionPolicy.has_authority(state, info["source_runtime"], allow_joining=allow_joining)
                    or state.get("authority_generation") != info["generation"]
                    or state.get("authority_request") != info["request"]):
                raise ValueError("스케줄 제공자의 Discord 송출권한이 변경되었습니다.")
        return coordinator

    def _send(self, info, operation, **values):
        with self.lock:
            role = self.role
        coordinator = self._guard(info, role, allow_joining=operation == "preflight_failed")
        peer = "source" if role == "host" else "host"
        result = coordinator._control("web", operation=operation, delegate=info["id"],
                                      target=info[peer], target_runtime=info[peer + "_runtime"], **values)
        if result.get("web_protocol") != WEB_RELAY_PROTOCOL:
            raise ValueError("웹 분리 운영은 GUI와 봇을 함께 새 버전으로 교체해야 합니다.")

    def sync_now(self):
        with self.lock:
            if self.role != "source":
                raise ValueError("Discord 송출·스케줄 제공 담당자 PC에서 눌러주세요.")
            self.force = True
            self.failures, self.next_attempt = 0, 0
            self.message = "수동 웹 갱신 요청 · 최신 스케줄을 준비합니다."
        self.wake.set()

    def return_web_here(self):
        with self.lock:
            if self.role != "source" or not self.info:
                raise ValueError("현재 스케줄 제공자 PC에서 요청해주세요.")
            if self.info["state"] != "active" or any(item.get("operation") == "return" for item in self.requests) or (self.host_job or {}).get("operation") == "return":
                return
            self.auto_return_started = True
            self.prepared_return = None
            self.requests.append(dict(operation="return"))
            recovery = getattr(self.bridge, "recovery", None)
            if recovery is not None:
                recovery.stage, recovery.message = "queued", "기존 웹 유지 · 새 PC의 웹 준비를 확인합니다."
        self.wake.set()

    def take_over_when_configured(self, available):
        with self.lock:
            info = self.info
            if not info or self.role != "source":
                return
            reason = ("web_settings_off" if not available else
                      "host_transition_pending" if info["state"] != "active" else
                      "explicit_keep_host" if info.get("keep_host") else
                      "already_started" if self.auto_return_started else
                      "voice_authority_pending" if not self.coordinator or not
                      ConnectionPolicy.has_authority(self.coordinator.policy.snapshot(), info["source_runtime"],
                                                     allow_joining=True) else "ready")
            signature = (info["id"], reason)
            if signature != self.auto_return_check:
                self.auto_return_check = signature
                self.manager._log("web_auto_return_check", delegate=info["id"], reason=reason)
            if reason != "ready":
                return
        # Exactly one attempt for this assignment; preparing/retrying is not a failure.
        self.return_web_here()
        self.manager._log("web_auto_return_queued", delegate=info["id"])

    def configuration_saved(self):
        with self.lock:
            if self.role == "source" and self.info and not self.info.get("keep_host"):
                self.auto_return_started = False
        self.take_over_when_configured(True)

    def retry(self, *, once=False):
        with self.lock:
            self.failures, self.next_attempt = 0, 0
            self.last_ready_sent = None
            self.worker_retries = self.public_retries = MAX_ATTEMPTS if once else 0
            self.worker_retry_at = self.public_retry_at = 0
        self.wake.set()

    def _source_upload(self, info, payload):
        coordinator = self._guard(info, "source")
        signature = edit_hash(payload)
        now = time.monotonic()
        with self.lock:
            if signature != self.latest_hash:
                self.latest_hash, self.last_edit = signature, now
                self.failures, self.next_attempt = 0, 0
                if self.pending and self.pending["edit_hash"] != signature:
                    self.pending = None
            if signature == self.sent_hash and not self.pending:
                self.force = False
                return
            if now < self.next_attempt or self.failures >= MAX_ATTEMPTS:
                return
            if not self.force and now - self.last_edit < SYNC_DELAY and not self.pending:
                return
            pending = self.pending
            self.force = False
        if pending is None:
            self.sequence += 1
            transfer = uuid.uuid4().hex
            envelope = dict(schema=1, delegate=info["id"], source=info["source"], source_runtime=info["source_runtime"],
                            sequence=self.sequence, snapshot=dict(payload, revision=1, updated_at=timestamp()))
            validate_snapshot(envelope["snapshot"])
            pending = dict(transfer=transfer, sequence=self.sequence, hash=content_hash(envelope), edit_hash=signature,
                           envelope=envelope, uploaded=False)
            with self.lock:
                self._guard(info, "source")
                self.pending = pending
        if not pending["uploaded"]:
            self._guard(info, "source")
            path = artifact_path(info["id"], pending["transfer"])
            ok, error = self.app._github_put_json_file(path, pending["envelope"], sha=None,
                                                    message="BossTimer delegated web schedule", timeout=5)
            if not ok:
                # A timed-out PUT may have succeeded; verify only this exact artifact on retry.
                existing, _, read_error = self.app._github_get_json_file(path, timeout=5)
                if read_error or existing != pending["envelope"]:
                    raise ValueError(error or "웹 스케줄 업로드에 실패했습니다.")
            pending["uploaded"] = True
            self.manager._log("web_delegated_schedule_uploaded", delegate=info["id"], sequence=pending["sequence"])
        self._guard(info, "source")
        with self.lock:
            self.failures += 1
            self.next_attempt = time.monotonic() + ACK_TIMEOUT
            self.message = "업로드 완료 · 기존 웹 담당자 반영 응답 대기"
        self._send(info, "schedule", transfer=pending["transfer"], sequence=pending["sequence"], hash=pending["hash"])

    def _host_download(self, info, event):
        self._guard(info, "host")
        with self.lock:
            already = self.applied_transfer
            duplicate = already and all(already.get(key) == event.get(key) for key in ("transfer", "sequence", "hash"))
            if event["sequence"] <= self.applied_sequence and not duplicate:
                return
        if duplicate:
            self._ack_applied(info, event)
            return
        envelope, _, error = self.app._github_get_json_file(artifact_path(info["id"], event["transfer"]), timeout=5)
        if (error or not isinstance(envelope, dict) or content_hash(envelope) != event["hash"]
                or envelope.get("delegate") != info["id"] or envelope.get("source") != info["source"]
                or envelope.get("source_runtime") != info["source_runtime"] or envelope.get("sequence") != event["sequence"]):
            raise ValueError("웹 전달 데이터의 버전·내용 확인에 실패했습니다.")
        payload = validate_snapshot(envelope.get("snapshot"))
        if payload["season"] != info["season"]:
            raise ValueError("웹 전달 데이터의 시즌이 다릅니다.")
        self._guard(info, "host")
        with self.lock:
            self._guard(info, "host")
            if event["sequence"] <= self.applied_sequence:
                return
            self.remote_payload = payload
            self.applied_sequence = event["sequence"]
            self.last_applied_at = timestamp()
            self.message = "새 담당자의 스케줄로 웹 자동 갱신 완료"
            version = self.manager.offer_snapshot(dict(payload, captured_at=timestamp()), origin="delegated")
            self.applied_transfer = dict(event, version=version)
        self.manager._log("web_delegated_schedule_applied", delegate=info["id"], sequence=event["sequence"])
        self._ack_applied(info, event)

    def _ack_applied(self, info, event):
        deadline = time.monotonic() + 6
        while True:
            self._guard(info, "host")
            with self.manager.lock:
                version = (self.applied_transfer or {}).get("version")
                written = self.manager.written_snapshot_version
            if version is not None and written >= version:
                break
            if self.closed.wait(0.2) or time.monotonic() >= deadline:
                raise ValueError("웹서버 반영 확인 대기 중입니다. 자동으로 다시 확인합니다.")
        self._send(info, "ack", transfer=event["transfer"], sequence=event["sequence"], ok=True, applied_at=self.last_applied_at)

    def _return_request(self, info):
        coordinator = self._guard(info, "source", allow_joining=True)
        recovery = getattr(self.bridge, "recovery", None)
        just_prepared = False
        if self.prepared_return != info["id"]:
            if recovery is not None and not recovery.preflight(info, coordinator):
                self.host_job = None
                return
            self.prepared_return = info["id"]
            just_prepared = True
        if (not self.manager.status()["enabled"] or self.manager.public_paused
                or not self.manager.public.status().get("enabled")):
            self.host_job = self.prepared_return = None
            self.manager._log("web_return_preflight_cancelled", reason="web_settings_off", delegate=info["id"])
            return
        state = coordinator.policy.snapshot()
        if not ConnectionPolicy.has_authority(state, info["source_runtime"]):
            if not just_prepared:
                key = (info["guild"], info["source"], info["source_runtime"], info["generation"])
                self.manager.prepare_local_https(key, state["authority_until"] - time.monotonic())
            self.message = "웹 준비 완료 · 음성 입장 병행 중 · 기존 웹 유지"
            return
        def change(data):
            current = delegation_info(data)
            if not current or current["id"] != info["id"] or data.get("phase") != "active":
                raise ValueError("웹 담당 기록이 변경되었습니다.")
            data["web_delegate"]["state"] = "releasing"
        with coordinator.lock:
            data = coordinator.record.mutate(change)
        coordinator._process_record(data, coordinator._status())
        self.notice = data
        self.host_job = None
        self.message = "기존 웹의 공개 중지 확인 대기 · 확인 전에는 이 PC에서 공개하지 않습니다."

    def _release_host(self, info):
        coordinator = self._guard(info, "host")
        state_path = ""
        if self.manager.ddns_status()["configured"]:
            from web_shared_state import export_state
            if self.return_settings is None:
                self.return_settings = export_state(self.manager, (
                    info["guild"], info["host"], info["host_runtime"], info["generation"]))
            if not valid_token(info["request"]):
                raise ValueError("웹 설정 이전 요청을 확인하지 못했습니다.")
            state_path = f"data/web/transfers/{info['request']}.json"
            existing, sha, error = self.app._github_get_json_file(state_path, timeout=5)
            if error:
                raise ValueError("최신 웹 설정 이전을 준비하지 못했습니다.")
            if existing != self.return_settings:
                self._guard(info, "host")
                ok, error = self.app._github_put_json_file(state_path, self.return_settings, sha=sha,
                    message="BossTimer encrypted delegated web return", timeout=5)
                if not ok:
                    raise ValueError(error or "최신 웹 설정 이전에 실패했습니다.")
        self._guard(info, "host")
        self.manager.emergency_stop(standby=True)  # Deny publication, retain the saved ON preference.
        self.manager.set_authority((), message="웹 담당 변경 · 기존 웹 공개 중지 확인")
        self.manager._flush_authority(force=True)
        if read_json(self.manager.root / "authority_lease.json").get("enabled") is not False:
            raise ValueError("기존 웹 외부 접근 차단을 확인하지 못했습니다.")
        # Deactivation prevents another DDNS request. Drain a request already
        # sent so its delayed response cannot overwrite the next PC's address.
        deadline = time.monotonic() + 20
        while (self.manager.ddns.status().get("busy") or self.manager.public.status().get("busy")
               or self.manager.public.status()["state"] != "off"):
            self._guard(info, "host")
            if self.closed.wait(0.2) or time.monotonic() >= deadline:
                raise ValueError("기존 무료 주소 갱신 종료 대기 중입니다. 자동으로 다시 확인합니다.")
        def change(data):
            current = delegation_info(data)
            if not current or current["id"] != info["id"] or current["state"] != "releasing":
                raise ValueError("웹 중지 요청이 변경되었습니다.")
            data["web_delegate"]["state"] = "stopped"
            if state_path:
                data.setdefault("web_transfer", {})["web_state_path"] = state_path
        with coordinator.lock:
            data = coordinator.record.mutate(change)
        coordinator._process_record(data, coordinator._status())
        self.notice = data
        self.release_done = True
        self.manager._log("web_delegated_host_stopped", delegate=info["id"])

    def _finish_return(self, info):
        coordinator = self._guard(info, "source")
        def change(data):
            current = delegation_info(data)
            if not current or current["id"] != info["id"] or current["state"] != "stopped":
                raise ValueError("기존 웹 공개 중지가 확인되지 않았습니다.")
            from web_return_recovery import pending_return
            data["web_return_pending"] = pending_return(info)
            data.pop("web_delegate", None)
        with coordinator.lock:
            data = coordinator.record.mutate(change)
        coordinator._process_record(data, coordinator._status())
        self.bridge.retry()
        self.notice = data
        self.manager._log("web_delegated_return_completed", delegate=info["id"])

    def _step(self):
        with self.lock:
            info, role, coordinator = deepcopy(self.info), self.role, self.coordinator
            payload = self.local_payload
            if role == "source" and payload:
                signature = edit_hash(payload)
                if signature != self.latest_hash:
                    self.latest_hash, self.last_edit = signature, time.monotonic()
                    self.failures, self.next_attempt = 0, 0
                    if self.pending and self.pending["edit_hash"] != signature:
                        self.pending = None
            # A new pushed request may retry an exhausted older job; otherwise
            # failed work has bounded backoff, including host ACK/publication.
            if self.requests:
                event = self.requests.popleft()
                self.host_job = event
                self.failures, self.next_attempt = 0, 0
            else:
                event = self.host_job
            if time.monotonic() < self.next_attempt or self.failures >= MAX_ATTEMPTS:
                return
            notice = self.notice
        if notice:
            coordinator._notify(notice)
            with self.lock:
                if self.notice is notice:
                    self.notice = None
        if not info or not role:
            return
        if role == "host":
            if info["state"] == "releasing" and not self.release_done:
                self._release_host(info)
                return
            if info["state"] != "active":
                return
            if info.get("restore_after_failure") and not self.restore_started:
                self._guard(info, "host")
                self.manager.retry_public()
                self.restore_started = True
                self.manager._log("web_previous_host_restore_started", delegate=info["id"])
            state = self.manager.status()
            if state["state"] == "running":
                self.worker_retries = 0
            elif (state["state"] == "error" and state["enabled"] and not self.manager.public_paused
                  and self.worker_retries < MAX_ATTEMPTS and time.monotonic() >= self.worker_retry_at):
                self._guard(info, "host")
                self.worker_retries += 1
                self.worker_retry_at = time.monotonic() + 30
                self.manager.set_enabled(True)
                self.manager._log("web_delegated_worker_retry", attempt=self.worker_retries)
            if event and event.get("operation") == "schedule":
                self._host_download(info, event)
                with self.lock:
                    self.host_job = None
                    self.failures, self.next_attempt = 0, 0
            public = self.manager.public.status()
            if public["state"] == "prepared":
                self.public_retries = 0
            elif (public["state"] == "error" and public.get("enabled") and self.manager.publication_allowed()
                  and self.public_retries < MAX_ATTEMPTS and time.monotonic() >= self.public_retry_at):
                self._guard(info, "host")
                self.public_retries += 1
                self.public_retry_at = time.monotonic() + 30
                self.manager.public.configure(True)
                self.manager._log("web_delegated_public_retry", attempt=self.public_retries)
            ready = self.manager.publication_allowed() and public["state"] == "prepared"
            # Connecting/preparing is a pending state, never an automatic fallback trigger.
            if ready != self.last_ready_sent:
                self._send(info, "ready", ready=ready)
                self.last_ready_sent = ready
        elif info["state"] == "stopped":
            self._finish_return(info)
        elif event and event.get("operation") == "return":
            self._return_request(info)
        elif info["state"] == "active" and payload:
            if not ConnectionPolicy.has_authority(coordinator.policy.snapshot(), info["source_runtime"]):
                return  # Voice is joining/reconnecting: pending, never a hosting failure.
            # Relay only a read-only web snapshot of the current local schedule.
            # Canonical upload results never gate this relay; GUI synchronization is manual.
            self._source_upload(info, payload)

    def _loop(self):
        while not self.closed.is_set() and not self.manager.closed.is_set():
            with self.lock:
                delegate = (self.info or {}).get("id")
            try:
                self._step()
                with self.lock:
                    if self.pending and self.failures >= MAX_ATTEMPTS and time.monotonic() >= self.next_attempt:
                        self.message = "웹 반영 응답 없음 · 기존 웹 유지 · 지금 웹 갱신으로 재시도할 수 있습니다."
            except Exception as exc:
                with self.lock:
                    if (self.info or {}).get("id") != delegate:
                        continue
                    self.failures += 1
                    self.next_attempt = time.monotonic() + RETRY_DELAY
                    self.message = "웹 전달 지연 · 기존 일정 유지 · " + str(exc)[:140]
                self.manager._log("web_delegated_operation_failed", error_type=type(exc).__name__)
            self.wake.wait(1)
            self.wake.clear()

    def close(self):
        self.closed.set()
        self.wake.set()
