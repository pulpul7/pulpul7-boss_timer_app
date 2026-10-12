"""Optional web publication follows Discord authority; never controls voice."""
import hashlib
from copy import deepcopy
import threading
import time

from discord_connection_policy import ConnectionPolicy
from web_shared_state import export_state, import_state
from web_delegated_hosting import DelegatedHosting


class WebAuthorityTransition(ValueError):
    """A verified new assignment keeps this PC's existing public website."""


class WebOperationCancelled(ValueError):
    """An obsolete local operation cannot deny or roll back its replacement."""


class WebHandoverBridge:
    def __init__(self, app, manager):
        self.app, self.manager = app, manager
        self.lock = threading.RLock()
        self.wake, self.closed = threading.Event(), threading.Event()
        self.context = None
        self.ready_key = self.failed_key = None
        self.shared_signature = None
        self.next_shared_check = 0
        self.retry_serial = 0
        self.retained_host = None
        self.transfer_state = None
        self.operation_local = threading.local()
        from web_return_recovery import WebReturnRecovery
        self.recovery = manager.web_return_recovery = WebReturnRecovery(self)
        self.delegation = manager.delegation = DelegatedHosting(self)
        from web_standalone_hosting import StandaloneHosting
        self.standalone = manager.standalone = StandaloneHosting(self)
        self.after_id = app.root.after(500, self._tick)
        manager.authority_retry = self.retry
        self.thread = threading.Thread(target=self._loop, name="web-authority-bridge", daemon=True)
        self.thread.start()

    def _tick(self):
        try:
            self.refresh_context()
        finally:
            if not self.closed.is_set() and not self.manager.closed.is_set():
                self.after_id = self.app.root.after(1000, self._tick)

    def refresh_context(self):
        if self.closed.is_set() or self.manager.closed.is_set():
            return
        candidate = None
        coordinator = getattr(self.app, "discord_handover", None)
        if coordinator is not None and coordinator.alive:
            try:
                coordinator._assert_profile()
                state = coordinator.policy.snapshot()
                if ConnectionPolicy.has_authority(state, allow_joining=True) and state.get("client_id") == coordinator.client:
                    key = (coordinator.scope["guild"], coordinator.client, state["runtime_id"], state["authority_generation"])
                    candidate = (key, coordinator, str(self.app.current_season_no or ""))
            except Exception:
                pass
        with self.lock:
            changed = candidate != self.context
            self.context = candidate
            if changed:
                self.ready_key = self.failed_key = None
                self.shared_signature = None
                self.next_shared_check = 0
        manual = self.standalone.suppresses_normal()
        retained = self._keep_retained_host() if not manual else False
        if changed:
            try:
                if self.delegation.status()["role"] != "host" and not retained and not manual:
                    self.manager.set_authority((), message="스탠바이 · Discord 송출권한을 기다립니다." if not candidate else
                                               "송출권한 확인 · 웹 승계 자료를 준비합니다.")
            except Exception as exc:
                self.app._append_debug_log(f"web_authority_gate_write_failed type={type(exc).__name__}")
            self.wake.set()
        try:
            if manual:
                self.standalone.tick()
            else:
                self.delegation.tick()
        except Exception as exc:
            self.manager._log("web_delegated_lease_failed", error_type=type(exc).__name__)

    def retain_web_host(self, data, info, coordinator):
        """Reuniting voice on the existing host must not stop its working web."""
        if (not info or info.get("state") != "active" or info.get("host") != coordinator.client
                or info.get("host_runtime") != coordinator.runtime
                or not self.manager.publication_allowed()):
            return False
        with self.manager.lock:
            payload = deepcopy(self.manager.pending_snapshot)
            self.manager.delegated_snapshot_only = True
        with self.lock:
            self.retained_host = (data.get("request"), coordinator, dict(info), payload)
        kept = self._keep_retained_host()
        if kept:
            self.manager._log("web_existing_host_retained", request=data.get("request"),
                              generation=data.get("generation"))
        return kept

    def observe_transfer(self, data, coordinator):
        artifact = data.get("web_transfer") or {}
        signature = (data.get("request"), artifact.get("web_state_path"))
        with self.lock:
            previous, self.transfer_state = self.transfer_state, signature
            context = self.context
            if (signature == previous or artifact.get("request") != signature[0]
                    or signature[1] != f"data/web/transfers/{signature[0]}.json"
                    or not context or context[1] is not coordinator
                    or context[0][1] != data.get("owner") or context[0][2] != data.get("owner_runtime")
                    or context[0][3] != data.get("generation")):
                return
            self.ready_key = self.failed_key = None
            self.manager._log("web_handover_settings_available", request=signature[0])
        self.wake.set()

    def _keep_retained_host(self, key=None, coordinator=None):
        with self.lock:
            retained = self.retained_host
        if retained is None:
            return False
        request, previous_coordinator, info, _payload = retained
        coordinator = coordinator or previous_coordinator
        with self.delegation.lock:
            data = self.delegation.record
            phase = data.get("phase")
            assigned = (data.get("owner") == coordinator.client and
                        data.get("owner_runtime") == coordinator.runtime) if phase == "active" else (
                        phase in {"requested", "joining"} and data.get("receiver") == coordinator.client
                        and data.get("receiver_runtime") == coordinator.runtime)
            valid = (coordinator is previous_coordinator and data.get("request") == request
                     and assigned and not self.delegation.role
                     and str(data.get("scope", {}).get("server")) == info["server"]
                     and str(data.get("scope", {}).get("season")) == info["season"])
        state = coordinator.policy.snapshot()
        valid = (valid and not self.closed.is_set() and not self.manager.closed.is_set()
                 and not self.manager.public_paused and coordinator.alive
                 and state.get("runtime_id") == info["host_runtime"]
                 and state.get("query_until", 0) > time.monotonic()
                 and str(self.app.current_season_no) == info["season"]
                 and coordinator.scope["server"] == info["server"]
                 and (key is None or (key[1] == coordinator.client and key[2] == coordinator.runtime
                                     and key[3] == data.get("generation"))))
        if not valid:
            with self.lock:
                if self.retained_host is retained:
                    self.retained_host = None
            with self.manager.lock:
                self.manager.delegated_snapshot_only = self.delegation.role == "host"
            return False
        self.manager.set_authority((info["guild"], coordinator.client, coordinator.runtime,
                                    "retained:" + request), ready=self.manager.snapshot_version > 0,
                                   message="기존 웹 유지 · 현재 PC의 웹 준비 확인 대기")
        return True

    def retry(self, *, once=False):
        if self.standalone.suppresses_normal():
            if not self.standalone.status()['local']:
                raise ValueError('다른 PC의 웹 단독 운영을 유지합니다. 변경하려면 공동 운영의 웹 단독 강제 실행을 눌러주세요.')
            self.standalone.start()
            return
        self.refresh_context()
        role = self.delegation.status()["role"]
        if role == "source":
            self.delegation.return_web_here()
            return
        if role == "host":
            self.delegation.retry(once=once)
            return
        with self.lock:
            if self.context is None:
                raise ValueError("Discord 송출권한을 먼저 얻어주세요. 웹 재시도는 음성 권한을 변경하지 않습니다.")
            self._guard(self.context[0], self.context[1], operation_serial=self.retry_serial)
            self.failed_key = self.ready_key = None
            self.retry_serial += 1
        if not self._keep_retained_host():
            self.manager.set_authority((), message="현재 송출권한으로 웹 공개를 다시 준비합니다.")
        self.wake.set()

    def _guard(self, key, coordinator, operation_serial=None, *, delegated_info=None):
        with self.lock:
            context = self.context
            serial = self.retry_serial
        state = coordinator.policy.snapshot()
        delegated_info = delegated_info or getattr(self.operation_local, "delegated_info", None)
        if delegated_info:
            try:
                self.delegation._guard(delegated_info, "source", allow_joining=True)
            except ValueError as exc:
                raise WebOperationCancelled(str(exc)) from exc
        manual = getattr(self, "standalone", None)
        if (self.closed.is_set() or self.manager.closed.is_set() or self.manager.public_paused
                or (manual is not None and manual.suppresses_normal())
                or (self.delegation.role and not delegated_info)
                or not context or context[0] != key or context[1] is not coordinator
                or (getattr(self.operation_local, "serial", serial) if operation_serial is None else operation_serial) != serial
                or not coordinator.alive or not ConnectionPolicy.has_authority(state, allow_joining=True)
                or state.get("runtime_id") != key[2] or state.get("authority_generation") != key[3]):
            raise WebOperationCancelled("권한이 변경되어 이전 웹 작업을 취소했습니다.")

    def _read_owner(self, key, coordinator):
        self._guard(key, coordinator)
        with coordinator.lock:
            data, _ = coordinator.record.read()
        self._guard(key, coordinator)
        if (data.get("phase") not in {"joining", "active"} or data.get("owner") != key[1]
                or data.get("owner_runtime") != key[2] or data.get("generation") != key[3]):
            from web_delegated_hosting import delegation_info
            info = delegation_info(data)
            if (info and info['state'] == 'active' and info['host'] == coordinator.client
                    and info['host_runtime'] == coordinator.runtime
                    and info['guild'] == coordinator.scope['guild']
                    and info['server'] == coordinator.scope['server']
                    and info['season'] == str(self.app.current_season_no)):
                # GitHub may show the new assignment before the Discord hint
                # arrives. Adopt that verified host role before cancelling the
                # old voice owner's web task, so its public lease stays alive.
                coordinator._process_record(data, coordinator._status())
                self.delegation.tick()
                raise WebAuthorityTransition('웹 담당 유지가 확인되어 이전 송출권한의 웹 작업을 종료했습니다.')
            raise ValueError("현재 Discord 권한 기록이 달라 웹 공개를 중단했습니다.")
        return data

    def _shared_path(self, key):
        return "data/web/" + hashlib.sha256(key[0].encode()).hexdigest()[:24] + ".json"

    def _prepare(self, key, coordinator, data, season):
        operation_serial = getattr(self.operation_local, "serial", self.retry_serial)
        delegated_info = getattr(self.operation_local, "delegated_info", None)
        self.operation_local.prepare_phase = "shared-settings"
        if self.manager.ddns_status()["configured"]:
            path = (data.get("web_transfer") or {}).get("web_state_path")
            expected_path = f"data/web/transfers/{data.get('request', '')}.json"
            if path and path != expected_path:
                raise ValueError("웹 설정 인계 경로를 확인하지 못했습니다.")
            envelope, _, error = self.app._github_get_json_file(path or self._shared_path(key), timeout=5)
            if error:
                raise ValueError("공동 웹 설정 조회에 실패했습니다. 웹 강제 승계/재시도를 눌러주세요.")
            if envelope is not None:
                import_state(self.manager, envelope, key, lambda: self._guard(key, coordinator))
                self.manager._log("web_shared_state_imported", generation=key[3])
        self._read_owner(key, coordinator)
        self.operation_local.prepare_phase = "schedule-snapshot"
        # Publish the receiver's current schedule; schedule synchronization is manual.
        def snapshot():
            from web_schedule_snapshot import collect_snapshot
            self._guard(key, coordinator, operation_serial, delegated_info=delegated_info)
            self.manager.offer_snapshot(collect_snapshot(self.app),
                                        origin="delegated" if self.retained_host else "local")
            # This runs on Tk: release the retained snapshot before another
            # GUI heartbeat can replace the freshly collected local schedule.
            with self.lock:
                if self.retained_host is not None:
                    self.retained_host = None
                    with self.manager.lock:
                        self.manager.delegated_snapshot_only = False
        coordinator._ui(snapshot)
        self.ready_key = key
        self.manager._log("web_handover_local_schedule_prepared", generation=key[3],
                          request=data.get("request"), schedule_sync="manual")

    def _publish_shared(self, key, coordinator):
        if self.manager.public.status()["state"] != "prepared":
            return
        self.manager.pin_public_port(self.manager.status().get("https_external_port"))
        # Check disk revisions infrequently; no repeated GitHub upload for identical content.
        if time.monotonic() < self.next_shared_check:
            return
        self.next_shared_check = time.monotonic() + 10
        root = self.manager.root
        signature = tuple((path.stat().st_mtime_ns, path.stat().st_size) for path in
                          (root / "access_settings.json", root / "https" / "current.json", self.manager.settings_path))
        if signature == self.shared_signature:
            return
        payload = export_state(self.manager, key)
        _, sha, error = self.app._github_get_json_file(self._shared_path(key), timeout=5)
        if error:
            raise ValueError("공동 웹 설정 저장 준비에 실패했습니다.")
        self._read_owner(key, coordinator)
        ok, error = self.app._github_put_json_file(self._shared_path(key), payload, sha=sha,
                                                  message="BossTimer encrypted web handover", timeout=5)
        if not ok:
            raise ValueError("공동 웹 설정 저장에 실패했습니다.")
        self.shared_signature = signature
        self.manager._log("web_shared_state_published", generation=key[3])

    def _loop(self):
        while not self.closed.is_set() and not self.manager.closed.is_set():
            with self.lock:
                context, serial = self.context, self.retry_serial
            if context:
                try:
                    if self.standalone.resume_stopped(context[0], context[1]):
                        with self.lock:
                            self.failed_key = self.ready_key = None
                except Exception as exc:
                    self.manager._log("web_stopped_mode_resume_check_failed", error_type=type(exc).__name__)
                try:
                    # Drive acquisition from the web worker as well as the GUI
                    # snapshot callback. A delayed/absent repaint must not leave
                    # the configured voice owner permanently in split hosting.
                    if not self.standalone.suppresses_normal():
                        self.delegation.take_over_when_configured(context[1]._web_publication_configured())
                except Exception as exc:
                    self.manager._log("web_auto_return_check_failed", error_type=type(exc).__name__)
                try:
                    self.recovery.monitor(context[0], context[1])
                except Exception as exc:
                    self.manager._log("web_return_recovery_check_failed", error_type=type(exc).__name__)
            try:
                self.standalone.publish_shared()
            except Exception as exc:
                self.manager._log("web_standalone_settings_check_failed", error_type=type(exc).__name__)
            if (context and not self.standalone.suppresses_normal() and not self.delegation.status()["role"]
                    and self.manager.status()["enabled"] and not self.manager.public_paused):
                key, coordinator, season = context
                self.operation_local.serial = serial
                preparing = False
                try:
                    data = self._read_owner(key, coordinator)
                    self.recovery.observe(data)
                    if self.failed_key != key:
                        state = coordinator.policy.snapshot()
                        if (self.ready_key != key and (ConnectionPolicy.has_authority(state)
                                or not self._keep_retained_host(key, coordinator))):
                            preparing = True
                            self._prepare(key, coordinator, data, season)
                            preparing = False
                        with self.lock:
                            if serial != self.retry_serial:
                                raise WebOperationCancelled("새 웹 재시도로 이전 작업을 취소했습니다.")
                        self._guard(key, coordinator)
                        state = coordinator.policy.snapshot()
                        if ConnectionPolicy.has_authority(state):
                            self.manager.set_authority(key, ready=True, remaining=state["authority_until"] - time.monotonic(),
                                                       message="Discord 송출권한 확인 · 웹 공개 준비 중")
                            with self.lock:
                                self.retained_host = None
                            with self.manager.lock:
                                self.manager.delegated_snapshot_only = False
                            try:
                                self._publish_shared(key, coordinator)
                            except Exception as exc:
                                self.manager._log("web_shared_state_publish_failed", error_type=type(exc).__name__)
                        elif not self._keep_retained_host(key, coordinator):
                            # Prepare TLS alongside voice joining; a failed voice
                            # join must not interrupt the previous public host.
                            self.manager.prepare_local_https(key, state["authority_until"] - time.monotonic())
                except Exception as exc:
                    if isinstance(exc, WebAuthorityTransition):
                        self.manager._log("web_voice_task_cancelled_host_retained", generation=key[3])
                        self.wake.set()
                        continue
                    with self.lock:
                        current_serial = self.retry_serial
                        obsolete = serial != current_serial or context != self.context
                    if isinstance(exc, WebOperationCancelled) or obsolete:
                        # A return/retry can replace this job while it reads the
                        # record or imports settings. Cancellation is not a
                        # failed public trial, even if the authority key is equal.
                        self.manager._log("web_handover_obsolete_operation_cancelled", generation=key[3],
                                          operation_serial=serial, current_serial=current_serial)
                    else:
                        try:
                            if not self.delegation.status()["role"] and not self._keep_retained_host(key, coordinator):
                                self.manager.set_authority((), message=str(exc) if isinstance(exc, ValueError) else
                                                           "웹 승계 준비 실패 · 진단 로그 확인 후 웹 재시도를 눌러주세요.")
                        except OSError:
                            pass  # The independent worker lease still expires without renewal.
                        if preparing and serial == self.retry_serial:
                            self.failed_key = key
                        self.manager._log("web_handover_prepare_failed", generation=key[3], error_type=type(exc).__name__,
                                          reason=str(exc)[:300] if isinstance(exc, ValueError) else "")
                        self.recovery.fail(key, coordinator, exc)
            self.wake.wait(5)
            self.wake.clear()

    def close(self):
        self.delegation.close()
        self.closed.set()
        self.wake.set()
        try:
            self.manager.set_authority((), message="웹서비스 종료 중")
        except OSError:
            pass  # Shutdown must continue even when the lease file is unwritable.
        if self.after_id is not None:
            try:
                self.app.root.after_cancel(self.after_id)
            except Exception:
                pass
