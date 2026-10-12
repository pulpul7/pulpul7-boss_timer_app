"""Owns the local web child process; no Tk, Discord, audio, or schedule imports."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from web_service_common import (DEFAULT_PORT, PHASE, PROTOCOL, WORKER_FLAG, close_logger,
                                diagnostic, diagnostic_logger, read_json, timestamp,
                                ACCESS_DISPLAY_DAYS, access_state_path, schedule_path, worker_paths, write_json)
from web_access_settings import AccessSettings, readable_pins, valid_id, normalize_ip
from web_network_diagnostics import NetworkDiagnostics
from web_ddns_service import DDNSService
from web_certificate_service import CertificateService
from web_direct_hosting import DirectHostingService
from web_page_updater import WebPageUpdater
from web_asset_store import read_version

MESSAGES = {
    "port_in_use": "웹 포트를 다른 프로그램이 사용 중입니다. 다른 포트로 재시도할 수 있습니다.",
    "worker_failed": "웹서버를 시작하지 못했습니다. 진단 로그를 확인해주세요.",
    "worker_exited": "웹서버가 종료되었습니다. 다시 시도해주세요.",
    "worker_timeout": "웹서버 시작 응답이 없습니다. 다시 시도해주세요.",
    "worker_unresponsive": "웹서버의 상태 확인이 중단되었습니다. 다시 시도해주세요.",
    "settings_read_failed": "웹 설정을 읽지 못했습니다. 진단 로그를 확인해주세요.",
    "worker_stop_failed": "웹서버 종료를 확인하지 못했습니다. 진단 로그를 확인한 뒤 다시 시도해주세요.",
}


class WebServiceManager:
    def __init__(self, app_root):
        self.root = Path(app_root) / "web_service"
        self.settings_path = self.root / "settings.json"
        self.lock = threading.RLock()
        self.wake, self.closed = threading.Event(), threading.Event()
        self.thread = None
        self.process = None
        self.logger = None
        self.recent = deque(maxlen=160)
        self.settings = dict(enabled=True, port=DEFAULT_PORT, external_port=None)
        self.generation = 0
        self.desired = False
        self.run_id = ""
        self.started_at = self.last_heartbeat_at = 0.0
        self.last_heartbeat = None
        self.process_generation = -1
        self.pending_snapshot = None
        self.delegated_snapshot_only = False
        self.snapshot_version = 0
        self.written_snapshot_version = -1
        self.snapshot_signature = None
        self.snapshot_revision = 0
        self.snapshot_updated_at = None
        self.authority_key = ()
        self.authority_until = 0.0
        self.authority_ready = False
        self.local_https_preparing = False
        self.authority_snapshot_version = 0
        self.public_paused = False
        self.public_resume_serial = 0
        self.authority_message = "스탠바이 · Discord 송출권한을 기다립니다."
        self.authority_retry = None
        self.last_authority_write = 0.0
        self.previous_mapping_owner = ""
        self.page_update_job = None
        self.access = AccessSettings(self.root)
        self.access_view = dict(self.access.public_view(), visitors=[], failures=[], live=False)
        self.desktop_pins = {}
        self.desktop_pin_signature = None
        self.access_signature = None
        self.access_jobs = deque()
        self.access_results = {}
        self.network = NetworkDiagnostics(self.root)
        self.ddns = DDNSService(self.root, on_saved=self._on_ddns_saved)
        self.https = CertificateService(self.root)
        self.public = DirectHostingService(self.root, self._public_context)
        self.public_preference_touched = self.public.path.exists()
        self.view = dict(state="stopped", enabled=True, port=DEFAULT_PORT,
                         address="", phase=PHASE, message="웹서비스가 꺼져 있습니다.", request_count=0,
                         schedule_state="waiting", schedule_count=0, schedule_updated_at=None)
        try:
            saved = read_json(self.settings_path)
            port = saved.get("port", DEFAULT_PORT)
            if type(port) is not int or not 1024 <= port <= 65535 or type(saved.get("enabled")) is not bool:
                raise ValueError("invalid web settings")
            self.settings.update(enabled=saved["enabled"], port=port)
            external = saved.get("external_port")
            if external is not None and (type(external) is not int or not 1 <= external <= 65535):
                raise ValueError("invalid external port")
            self.settings["external_port"] = external
            self.view.update(enabled=saved["enabled"], port=port)
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            self._update(state="error", message=MESSAGES["settings_read_failed"])
            self._log("settings_read_failed", error_type=type(exc).__name__)
        self.page_updater = WebPageUpdater(self)

    def _log(self, event, **fields):
        if self.logger is None:
            self.logger = diagnostic_logger(self.root, "manager")
        diagnostic(self.logger, event, **fields)
        with self.lock:
            self.recent.append(f"{timestamp()} phase={PHASE} {event} {fields}")

    def _update(self, **values):
        with self.lock:
            generation = values.pop("_generation", None)
            if generation is not None and generation != self.generation:
                return
            self.view.update(values)
            active = self.view["state"] == "running" and self.view["enabled"]
        owns = self.has_web_authority()
        self.ddns.set_active(active and self.publication_allowed() and self.public.status()["state"] == "prepared")
        self.https.set_active(active and owns and (self.authority_ready or getattr(self, "local_https_preparing", False)))
        self.public.set_active(active and self.publication_allowed())

    def has_web_authority(self):
        with self.lock:
            return bool(self.authority_key and self.authority_until > time.monotonic() and not self.public_paused)

    def publication_allowed(self):
        with self.lock:
            return (self.has_web_authority() and self.authority_ready
                    and self.view.get("state") == "running"
                    and self.authority_snapshot_version > 0
                    and self.written_snapshot_version >= self.authority_snapshot_version)

    def set_authority(self, key, *, ready=False, message="", remaining=10):
        with self.lock:
            if not key or ready:
                self.local_https_preparing = False
            changed = tuple(key) != self.authority_key
            if key and ready and (changed or not self.authority_ready):
                self.authority_snapshot_version = self.snapshot_version
            self.authority_key = tuple(key)
            self.authority_until = time.monotonic() + min(10, max(0, remaining)) if key else 0
            self.authority_ready = bool(key and ready)
            if message:
                self.authority_message = message
        if changed:
            self._log("web_authority_changed", active=bool(key), generation=key[-1] if key else None)
        if not key or not ready:
            self._flush_authority(force=True)
        self._ensure_coordinator()
        self.wake.set()

    def _flush_authority(self, *, force=False):
        if not force and time.monotonic() - self.last_authority_write < 1:
            return
        with self.lock:
            enabled = self.publication_allowed() and self.desired and not self.closed.is_set()
            remaining = max(0, self.authority_until - time.monotonic())
            data = dict(schema=1, enabled=enabled, run_id=self.run_id,
                        valid_until=time.time() + remaining if enabled else 0)
            self.root.mkdir(parents=True, exist_ok=True)
            write_json(self.root / "authority_lease.json", data)
            self.last_authority_write = time.monotonic()

    def prepare_local_https(self, key, remaining):
        """Run loopback TLS only; authority_ready stays false and the public lease stays denied."""
        with self.lock:
            self.local_https_preparing = True
            self.set_authority(key, ready=False, remaining=remaining, message="기존 웹 유지 · 이 PC의 HTTPS 준비 확인")

    def retry_public(self, *, once=False):
        if self.authority_retry is None:
            raise ValueError("Discord 송출권한을 먼저 얻어주세요.")
        self.public_paused = False
        if once:
            self.authority_retry(once=True)
        else:
            self.authority_retry()
        self.set_enabled(True)
        self.public_preference_touched = True
        self.public.configure(True)
        self._log("web_public_retry_requested")

    def emergency_stop(self):
        self.public_paused = True
        self.authority_ready = False
        self.authority_message = "웹 긴급 중지 · 웹 강제 승계/재시도로 다시 시작할 수 있습니다."
        try:
            self._flush_authority(force=True)
        except OSError as exc:
            self._log("web_emergency_gate_write_failed", error_type=type(exc).__name__)
        try:
            self.set_enabled(False)
        except OSError:
            self._request(False)  # Stop the owned worker even if OFF cannot be saved.
            raise
        self._log("web_emergency_stop_requested")

    def force_start_web(self):
        self.standalone.start()

    def stop_web(self):
        self.standalone.stop()

    def stop_web_verified(self):
        self.standalone.stop_sync()

    def _public_context(self):
        with self.lock:
            return dict(self.view, run_id=self.run_id, preferred_external_port=self.settings["external_port"],
                        previous_mapping_owner=self.previous_mapping_owner)

    def pin_public_port(self, port):
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("웹 외부 포트를 확인하지 못했습니다.")
        with self.lock:
            if self.settings["external_port"] != port:
                saved = dict(self.settings, external_port=port)
                write_json(self.settings_path, dict(protocol=PROTOCOL, **saved))
                self.settings = saved

    def public_status(self):
        state = self.public.status()
        if state["enabled"] and not self.publication_allowed():
            state.update(state="standby" if not self.has_web_authority() else "waiting",
                         address="", message=self.view["message"] if self.view["state"] == "error" else self.authority_message)
        return state

    def configure_public(self, enabled):
        if enabled:
            self.public_paused = False
            self.public_resume_serial = getattr(self, "public_resume_serial", 0) + 1
        self.public_preference_touched = True
        return self.public.configure(enabled)

    def status(self):
        with self.lock:
            return dict(self.view, web_authority=self.has_web_authority(),
                        web_ready=self.publication_allowed(), authority_message=self.authority_message)

    def access_status(self):
        with self.lock:
            data = deepcopy(self.access_view)
        cutoff = time.time() - ACCESS_DISPLAY_DAYS * 86400
        def recent(row, key):
            return isinstance(row, dict) and type(row.get(key)) in {int, float} and row[key] >= cutoff
        data["visitors"] = [row for row in data["visitors"] if recent(row, "last_seen")]
        data["failures"] = [row for row in data["failures"] if recent(row, "at")]
        return data

    def _desktop_pin_key(self):
        return (self.access.error is not None, tuple(
            self.access.data.get(prefix + field, "")
            for prefix in ("", "guest_") for field in ("salt", "verifier", "protected_pin")))

    def desktop_pin_status(self):
        """Private GUI accessor, deliberately separate from worker/public status."""
        self._ensure_coordinator()
        with self.lock:
            if self.desktop_pin_signature == self._desktop_pin_key():
                return deepcopy(self.desktop_pins)
        self.wake.set()
        return {role: dict(value="", state="checking") for role in ("member", "guest")}

    def _refresh_desktop_pins(self):
        with self.lock:
            signature = self._desktop_pin_key()
            if signature == self.desktop_pin_signature:
                return
            data = deepcopy(self.access.data)
        # DPAPI and password verification run once per changed value, outside Tk.
        if signature[0]:
            pins = {role: dict(value="", state="unavailable") for role in ("member", "guest")}
        else:
            pins = readable_pins(data)
        with self.lock:
            if signature != self._desktop_pin_key():
                return  # A newer import/edit superseded this calculation.
            self.desktop_pins, self.desktop_pin_signature = pins, signature
        for role, item in pins.items():
            if item["state"] == "unavailable":
                self._log("access_pin_display_unavailable", role=role)

    def network_status(self):
        return self.network.status()

    def ddns_status(self):
        return self.ddns.status()

    def configure_ddns(self, enabled, domain, token):
        return self.ddns.configure(enabled, domain, token)

    def _on_ddns_saved(self, saved):
        """Credential setup also arms hosting; runs on DDNS's worker, even with the panel closed."""
        if self.closed.is_set() or not saved["enabled"]:
            return ""
        self.public_paused = False
        if not self.status()["enabled"]:
            self.set_enabled(True)
        public = self.public.status()
        if not public["enabled"] or public["state"] == "error":
            self.configure_public(True)
        relay = getattr(self, "delegation", None)
        role = relay.status()["role"] if relay is not None else ""
        if role == "source":
            relay.configuration_saved()
        elif (not role and self.authority_retry is not None
                and not (self.publication_allowed() and public["state"] == "prepared")):
            try:
                self.authority_retry()
            except ValueError:
                pass  # No voice authority yet: the normal acquisition event starts preparation.
        self._log("web_credentials_saved_setup_armed", role=role)
        return "웹서비스·외부 공개를 켰습니다. 송출권한에 따라 웹 준비와 승계를 자동 진행합니다."

    def update_ddns(self):
        if not self.publication_allowed() or self.public.status()["state"] != "prepared":
            raise ValueError("송출권한 획득 및 웹 공개 준비 후 주소를 자동 갱신합니다.")
        return self.ddns.update_now()

    def ddns_result(self, job):
        return self.ddns.result(job)

    def https_status(self):
        state = self.https.status()
        worker = self.status()
        state.update({key: worker.get(key, "") for key in
                      ("https_state", "https_domain", "https_port", "https_message")})
        if worker["state"] != "running":
            state.update(https_state="stopped", https_message="웹서비스를 먼저 켜주세요.")
        return state

    def request_https(self, operation, *, agreed=False, port=8766):
        if operation == "issue" and port == self.status()["port"]:
            raise ValueError("HTTPS 포트는 기존 로컬 웹 포트와 달라야 합니다. 기본값 8766을 사용하세요.")
        return self.https.request(operation, agreed=agreed, port=port)

    def https_result(self, job):
        return self.https.result(job)

    def diagnose_network(self):
        if not self.closed.is_set():
            return self.network.start(self.status())
        return False

    def network_report(self):
        state = self.network.status()
        lines = ["BossTimer 웹 네트워크 진단 · 4단계", f"확인 시각: {state['checked_at'] or '진단 중 / 미실행'}", state["message"]]
        for row in state["checks"]:
            lines.extend(("", f"{row['label']}: {row['value']}", row["detail"]))
        lines.append("\n기본 HTTP는 같은 PC 전용입니다. HTTPS 외부 공개 상태: " + self.public_status()["message"])
        return "\n".join(lines)

    def change_access(self, operation, value=""):
        """Submit desktop-only policy changes; expensive PIN hashing stays off Tk."""
        with self.lock:
            if self.closed.is_set() or len(self.access_jobs) >= 16:
                raise ValueError("웹 설정 작업을 잠시 후 다시 시도해주세요.")
            job = uuid.uuid4().hex
            self.access_jobs.append((job, operation, value))
        self._ensure_coordinator()
        self.wake.set()
        return job

    def access_result(self, job):
        with self.lock:
            return self.access_results.pop(job, None)

    def _access_jobs(self):
        with self.lock:
            task = self.access_jobs.popleft() if self.access_jobs else None
        if task is None:
            return
        job, operation, value = task
        try:
            message = self.access.change(operation, value)
            result = dict(ok=True, message=message)
            self._log("access_settings_changed", operation=operation, revision=self.access.data["revision"])
        except Exception as exc:
            self._log("access_settings_change_failed", operation=operation, error_type=type(exc).__name__)
            result = dict(ok=False, message=str(exc) if isinstance(exc, ValueError) else
                          "웹 접속 설정을 저장하지 못했습니다. 진단 로그를 확인해주세요.")
        with self.lock:
            self.access_view.update(self.access.public_view())
            if len(self.access_results) >= 32:
                self.access_results.pop(next(iter(self.access_results)))
            self.access_results[job] = result

    def _read_access_state(self):
        if self.process is None:
            return
        try:
            path = access_state_path(self.root, self.run_id)
            stat = path.stat()
            signature = (self.run_id, stat.st_mtime_ns, stat.st_size)
            if signature == self.access_signature:
                return
            if stat.st_size > 1024 * 1024:
                raise ValueError("access status too large")
            data = read_json(path)
            if data.get("protocol") != PROTOCOL or data.get("run_id") != self.run_id:
                raise ValueError("access status mismatch")
            rows, failures = data.get("visitors"), data.get("failures")
            if not isinstance(rows, list) or len(rows) > 1000 or not isinstance(failures, list) or len(failures) > 30:
                raise ValueError("invalid access status")
            public = []
            for row in rows:
                if not isinstance(row, dict) or not valid_id(row.get("id")):
                    raise ValueError("invalid visitor status")
                public.append(dict(id=row["id"], ip=normalize_ip(row["ip"]),
                    nickname=str(row.get("nickname", "guest"))[:40], role=str(row.get("role", ""))[:10],
                    browser=str(row["browser"])[:80], state=str(row["state"])[:40],
                    last_seen=float(row["last_seen"]), failures=int(row["failures"])))
            with self.lock:
                self.access_view.update(visitors=public, failures=failures, live=True)
            self.access_signature = signature
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if self.access_signature != "failed":
                self._log("access_status_read_failed", error_type=type(exc).__name__)
            self.access_signature = "failed"

    def offer_snapshot(self, payload, *, origin="local"):
        # The GUI passes a newly built, detached dictionary. Disk/JSON work is
        # coalesced on the coordinator thread, never on the Tk thread.
        with self.lock:
            if self.closed.is_set() or not self.view["enabled"]:
                return
            if self.delegated_snapshot_only and origin != "delegated":
                return
            self.pending_snapshot = payload
            self.snapshot_version += 1
            version = self.snapshot_version
        self.wake.set()
        return version

    def _flush_snapshot(self):
        if self.process is None or self.process.poll() is not None:
            return
        with self.lock:
            version, payload = self.snapshot_version, self.pending_snapshot
        if payload is None or version == self.written_snapshot_version:
            return
        try:
            content = {key: value for key, value in payload.items() if key != "captured_at"}
            signature = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            changed = signature != self.snapshot_signature
            revision = self.snapshot_revision + int(changed)
            updated_at = timestamp() if changed else self.snapshot_updated_at
            write_json(schedule_path(self.root, self.run_id),
                       dict(payload, protocol=PROTOCOL, run_id=self.run_id,
                            revision=revision, updated_at=updated_at))
            self.snapshot_signature, self.snapshot_revision = signature, revision
            self.snapshot_updated_at = updated_at
            self.written_snapshot_version = version
            if changed:
                self._log("schedule_snapshot_published", run_id=self.run_id, revision=revision,
                          event_count=len(payload["events"]), active_count=len(payload["active"]))
        except Exception as exc:
            self._log("schedule_snapshot_write_failed", error_type=type(exc).__name__)

    def start_saved(self):
        self.page_updater.start()
        if self.generation == 0 and self.settings["enabled"] and not self.closed.is_set():
            self._request(True)

    def set_enabled(self, enabled, *, alternate_port=False):
        if self.closed.is_set():
            return
        settings = dict(self.settings, enabled=bool(enabled))
        if alternate_port:
            settings["port"] = settings["port"] + 1 if settings["port"] < 65535 else DEFAULT_PORT
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            write_json(self.settings_path, dict(protocol=PROTOCOL, **settings))
        except OSError as exc:
            self._log("settings_write_failed", error_type=type(exc).__name__, errno=exc.errno)
            raise
        self.settings = settings
        if settings["enabled"]:
            self.public_paused = False
            self.public_resume_serial = getattr(self, "public_resume_serial", 0) + 1
        self._request(settings["enabled"])
        self.page_updater.start()

    def apply_page_update(self, version):
        job = dict(version=version, done=threading.Event(), cancelled=threading.Event(), error=None)
        with self.lock:
            if self.closed.is_set() or self.page_update_job is not None:
                raise ValueError("웹 업데이트를 적용할 수 없습니다. 종료 또는 진행 중인 작업을 확인해주세요.")
            self.page_update_job = job
        self._ensure_coordinator()
        self.wake.set()
        deadline = time.monotonic() + 60
        while not job["done"].wait(0.25):
            if self.closed.is_set() or time.monotonic() > deadline:
                job["cancelled"].set()
                raise ValueError("웹 업데이트 재시작 확인 시간이 초과됐습니다. 웹 상태와 진단 로그를 확인해주세요.")
        if job["error"]:
            raise ValueError(job["error"])

    def _wait_page_start(self, generation, expected_version=None):
        deadline = time.monotonic() + 20
        while not self.closed.is_set() and time.monotonic() < deadline:
            with self.lock:
                if generation != self.generation or not self.desired:
                    return  # A user's OFF/retry request takes precedence.
            self._flush_snapshot()
            self._monitor()
            if self.status()["state"] == "running":
                if expected_version and self.status().get("web_page_version") != expected_version:
                    raise ValueError("재시작한 웹서버의 파일 버전이 일치하지 않습니다.")
                self._log("web_page_worker_restart_ready", run_id=self.run_id)
                return
            if self.status()["state"] == "error":
                break
            self.closed.wait(0.1)
        raise ValueError("새 웹서비스의 시작을 확인하지 못했습니다.")

    def _page_update_jobs(self):
        with self.lock:
            job, self.page_update_job = self.page_update_job, None
            generation, desired, port = self.generation, self.desired, self.settings["port"]
        if job is None:
            return
        pointer_path = self.root / "pages" / "current.json"
        before = None
        committed = False
        stopped = False
        try:
            if job["cancelled"].is_set() or self.closed.is_set():
                raise ValueError("웹 업데이트 적용을 취소했습니다.")
            read_version(self.root, job["version"])
            try:
                before = read_json(pointer_path)
            except FileNotFoundError:
                pass
            self._update(state="updating", address="", message="웹 파일 적용 · 웹서비스를 다시 시작합니다.")
            self._flush_authority(force=True)
            if not self._stop_owned():
                raise ValueError("기존 웹 프로세스의 종료를 확인하지 못했습니다. 파일 적용은 중단했습니다.")
            stopped = True
            if job["cancelled"].is_set() or self.closed.is_set():
                raise ValueError("웹 업데이트 적용을 취소했습니다.")
            write_json(pointer_path, dict(schema=1, version=job["version"],
                       previous=before.get("version", "") if isinstance(before, dict) else "", applied_at=timestamp()))
            committed = True
            self._log("web_page_files_committed", version=job["version"][:12])
            with self.lock:
                restart = desired and self.desired and generation == self.generation and not self.closed.is_set()
            if restart:
                self._start(port, generation)
                self._wait_page_start(generation, job["version"])
            elif not self.desired:
                self._update(state="stopped", address="", message="웹 파일 적용 완료 · 웹서비스는 꺼져 있습니다.")
        except Exception as exc:
            job["error"] = str(exc) if isinstance(exc, ValueError) else "웹 파일 적용에 실패했습니다. 진단 로그를 확인해주세요."
            if stopped:
                try:
                    if not self._stop_owned():
                        raise ValueError("웹 복구를 위한 프로세스 종료에 실패했습니다.")
                    if committed:
                        if before is not None:
                            write_json(pointer_path, before)
                        else:
                            pointer_path.unlink(missing_ok=True)
                    self._log("web_page_update_rolled_back")
                    with self.lock:
                        restart = generation == self.generation and self.desired and not self.closed.is_set()
                    if restart:
                        self._start(port, generation)
                        self._wait_page_start(generation)
                    elif not self.desired:
                        self._update(state="stopped", address="", message="웹 업데이트 취소 · 웹서비스는 꺼져 있습니다.")
                except Exception as rollback:
                    self._log("web_page_update_rollback_failed", error_type=type(rollback).__name__)
                    job["error"] += " 웹 자동 복구도 실패했습니다. 웹 재시도를 눌러주세요."
            self._log("web_page_update_apply_failed", error_type=type(exc).__name__)
        finally:
            job["done"].set()

    def _request(self, enabled):
        with self.lock:
            self.desired = bool(enabled)
            self.generation += 1
            self.view.update(enabled=self.desired, state="starting" if enabled else "stopping",
                             message="웹서버를 시작합니다." if enabled else "웹서버를 종료합니다.",
                             port=self.settings["port"], address="", request_count=0)
        self._log("start_requested" if enabled else "stop_requested", port=self.settings["port"])
        self.ddns.set_active(False)
        self.https.set_active(False)
        self.public.set_active(False)
        self._ensure_coordinator()
        self.wake.set()

    def _ensure_coordinator(self):
        with self.lock:
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._coordinate, name="boss-web-manager", daemon=True)
                self.thread.start()

    def _coordinate(self):
        seen = -1
        try:
            while not self.closed.is_set():
                self._access_jobs()
                self._refresh_desktop_pins()
                self._flush_authority()
                self._update()
                with self.lock:
                    generation, desired, port = self.generation, self.desired, self.settings["port"]
                if generation != seen:
                    seen = generation
                    if not self._stop_owned():
                        self.wake.wait(0.25)
                        self.wake.clear()
                        continue
                    with self.lock:
                        current = generation == self.generation and not self.closed.is_set()
                    if current:
                        if desired:
                            self._start(port, generation)
                        else:
                            self._update(_generation=generation, state="stopped", address="", message="웹서비스가 꺼져 있습니다.")
                self._page_update_jobs()
                self._flush_snapshot()
                self._monitor()
                self._read_access_state()
                self.wake.wait(0.25)
                self.wake.clear()
        except Exception as exc:
            self._log("manager_failed", error_type=type(exc).__name__)
            self._update(state="error", address="", message=MESSAGES["worker_failed"])
        finally:
            self._stop_owned()

    def _start(self, port, generation):
        # A missing/unreadable policy must never publish an unprotected schedule.
        try:
            self.access.ensure_saved()
        except Exception as exc:
            self._log("access_settings_read_failed", error_type=type(exc).__name__)
            self._update(_generation=generation, state="error", message="웹 접속 설정을 읽지 못했습니다. 진단 로그를 확인해주세요.")
            return
        self.process_generation = generation
        self.run_id = uuid.uuid4().hex
        self.started_at = self.last_heartbeat_at = time.monotonic()
        self.last_heartbeat = None
        self.written_snapshot_version = -1
        self.snapshot_signature = None
        self.snapshot_revision = 0
        self.snapshot_updated_at = None
        self.access_signature = None
        self._update(_generation=generation, state="starting", address="", message="웹서버를 시작합니다.",
                     schedule_state="waiting", schedule_count=0, schedule_updated_at=None)
        args = ["--root", str(self.root), "--run-id", self.run_id,
                "--parent-pid", str(os.getpid()), "--port", str(port)]
        if getattr(sys, "frozen", False):
            command = [sys.executable, WORKER_FLAG, *args]
        else:
            command = [sys.executable, "-B", str(Path(__file__).with_name("web_service_worker.py")), *args]
        environment = os.environ.copy()
        environment["PYINSTALLER_SUPPRESS_SPLASH_SCREEN"] = "1"
        # This worker ends with the GUI and may reuse its extracted bundle.
        environment.pop("PYINSTALLER_RESET_ENVIRONMENT", None)
        try:
            self.process = subprocess.Popen(command, env=environment,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), close_fds=True)
            self._log("worker_spawned", run_id=self.run_id, pid=self.process.pid, port=port)
        except OSError as exc:
            self.process = None
            self._log("worker_spawn_failed", error_type=type(exc).__name__, errno=exc.errno)
            self._update(_generation=generation, state="error", address="", message=MESSAGES["worker_failed"])

    def _read_worker_state(self):
        status_path, _ = worker_paths(self.root, self.run_id)
        try:
            data = read_json(status_path)
            if (data.get("run_id") == self.run_id and data.get("protocol") == PROTOCOL
                    and data.get("pid") == self.process.pid and data.get("port") == self.status()["port"]):
                return data
        except (OSError, ValueError):
            pass
        return None

    def _monitor(self):
        if self.process is None:
            return
        with self.lock:
            if self.process_generation != self.generation or self.view["state"] == "error":
                return
        data = self._read_worker_state()
        exit_code = self.process.poll()
        if data and data.get("state") == "error":
            self._fail(str(data.get("error_code") or "worker_failed"))
        elif exit_code is not None:
            self._fail("worker_exited", exit_code=exit_code)
        elif data and data.get("state") == "running":
            heartbeat = data.get("heartbeat")
            if heartbeat != self.last_heartbeat:
                self.last_heartbeat, self.last_heartbeat_at = heartbeat, time.monotonic()
            if time.monotonic()-self.last_heartbeat_at > 10:
                self._fail("worker_unresponsive")
                return
            if self.status()["state"] != "running":
                self._log("local_connection_ready", run_id=self.run_id, port=data["port"])
            self._update(_generation=self.process_generation, state="running", address=f"http://127.0.0.1:{data['port']}",
                         message="같은 PC의 브라우저에서 접속할 수 있습니다.",
                         request_count=data.get("request_count", 0),
                         schedule_state=data.get("schedule_state", "waiting"),
                         schedule_count=data.get("schedule_count", 0),
                         schedule_updated_at=data.get("schedule_updated_at"),
                         **{key: data.get(key, "") for key in
                            ("https_state", "https_domain", "https_port", "https_message", "https_public", "https_activation", "https_external_port", "web_page_version")})
        elif time.monotonic()-self.last_heartbeat_at > (20 if self.last_heartbeat is None else 10):
            self._fail("worker_timeout" if self.last_heartbeat is None else "worker_unresponsive")

    def _fail(self, code, **fields):
        self._log(code, run_id=self.run_id, **fields)
        self._update(_generation=self.process_generation, state="error", address="", message=MESSAGES.get(code, MESSAGES["worker_failed"]))
        self._flush_authority(force=True)
        self._stop_owned()

    def _stop_owned(self):
        process = self.process
        if process is None:
            return True
        status_path, stop_path = worker_paths(self.root, self.run_id)
        try:
            stop_path.touch()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.terminate()
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        exit_code = process.poll()
        if exit_code is None:
            self._log("worker_stop_failed", run_id=self.run_id)
            self._update(state="error", address="", message=MESSAGES["worker_stop_failed"])
            return False
        self._log("worker_stopped", run_id=self.run_id, exit_code=exit_code)
        self.process = None
        with self.lock:
            self.access_view["live"] = False
        for path in (status_path, stop_path, schedule_path(self.root, self.run_id), access_state_path(self.root, self.run_id)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return True

    def diagnostics(self):
        lines = []
        for component in ("manager", "worker", "network", "ddns", "https", "public"):
            try:
                with (self.root / f"{component}.log").open("rb") as stream:
                    stream.seek(0, 2)
                    stream.seek(max(0, stream.tell()-65536))
                    lines.extend(stream.read().decode("utf-8", errors="replace").splitlines()[-100:])
            except OSError:
                pass
        if not lines:
            with self.lock:
                lines = list(self.recent)
        return "\n".join(lines) or "웹서비스를 사용하면 단계별 진단 로그가 기록됩니다."

    def close(self, *, wait=False):
        self.closed.set()
        self.page_updater.close()
        try:
            self._flush_authority(force=True)
        except OSError:
            pass
        self.network.close()
        self.ddns.close()
        self.https.close()
        self.public.close(wait=wait)
        self.wake.set()
        if wait and self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)
        if self.logger is not None and (self.thread is None or not self.thread.is_alive()):
            close_logger(self.logger)
