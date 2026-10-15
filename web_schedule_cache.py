"""Worker-side snapshot cache. HTTP threads read memory, not GUI/profile files."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import threading
import time

from web_schedule_snapshot import schedule_summary, validate_snapshot
from web_service_common import PROTOCOL, diagnostic, schedule_path, timestamp, open_shared_read


class ScheduleCache:
    def __init__(self, root, run_id, logger):
        self.path = schedule_path(root, run_id)
        self.run_id, self.logger = run_id, logger
        self.lock = threading.Lock()
        self.data = None
        self.file_signature = None
        self.last_capture = None
        self.last_capture_at = 0.0
        self.read_failed = False
        self.last_state = "waiting"
        self.structure_hash = ""
        self.major_hash = ""
        self.major_rows = {}
        self.catalog_hash = ""

    def refresh(self):
        try:
            stat = self.path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature != self.file_signature:
                if stat.st_size > 4 * 1024 * 1024:
                    raise ValueError("schedule snapshot too large")
                with open_shared_read(self.path) as stream:
                    content = stream.read(4 * 1024 * 1024 + 1)
                if len(content) > 4 * 1024 * 1024:
                    raise ValueError("schedule snapshot too large")
                envelope = json.loads(content)
                if (not isinstance(envelope, dict) or envelope.get("run_id") != self.run_id
                        or envelope.get("protocol") != PROTOCOL):
                    raise ValueError("invalid schedule session")
                data = validate_snapshot(envelope)
                structure = {key: value for key, value in data.items() if key not in
                             {"events", "active", "boss_catalog", "revision", "captured_at", "updated_at"}}
                for kind in ("events", "active"):
                    structure[kind] = [{key: value for key, value in row.items() if key not in {"important", "major"}}
                                       for row in data[kind]]
                major_rows = {row["id"]: {key: row[key] for key in ("important", "major")} for row in data["events"]}
                encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                structure_hash = hashlib.sha256(encode(structure)).hexdigest()
                major_hash = hashlib.sha256(encode(dict(events=major_rows, active=data["active"]))).hexdigest()
                catalog_hash = hashlib.sha256(encode(data.get("boss_catalog", []))).hexdigest()
                with self.lock:
                    changed = self.data is None or self.data["revision"] != data["revision"]
                    self.data = data
                    self.structure_hash, self.major_hash, self.major_rows = structure_hash, major_hash, major_rows
                    self.catalog_hash = catalog_hash
                    if self.last_capture != data["captured_at"]:
                        self.last_capture = data["captured_at"]
                        self.last_capture_at = time.monotonic()
                    self.read_failed = False
                self.file_signature = signature
                if changed:
                    diagnostic(self.logger, "schedule_snapshot_loaded", revision=data["revision"],
                               event_count=len(data["events"]), active_count=len(data["active"]))
        except FileNotFoundError:
            with self.lock:
                if self.data is not None:
                    self.read_failed = True
        except Exception as exc:
            with self.lock:
                first_failure = not self.read_failed
                self.read_failed = True
            if first_failure:
                diagnostic(self.logger, "schedule_snapshot_read_failed", error_type=type(exc).__name__)
        state = self.status()["schedule_state"]
        if state != self.last_state:
            diagnostic(self.logger, "schedule_data_state", state=state)
            self.last_state = state

    def _stale(self, now):
        return (self.read_failed or time.monotonic() - self.last_capture_at > 15
                or (now - datetime.fromisoformat(self.data["captured_at"])).total_seconds() > 15)

    def status(self, known_major_version=None, known_catalog_version=None):
        with self.lock:
            status = dict(schedule_state="waiting" if self.data is None else
                        "stale" if self._stale(datetime.now(timezone.utc)) else "ready",
                        schedule_count=len(self.data["events"]) if self.data else 0,
                        schedule_version=self._version(),
                        major_version=self._major_version(),
                        catalog_version=self._catalog_version(),
                        schedule_updated_at=self.data["updated_at"] if self.data else None)
            if self.data and known_major_version is not None and known_major_version != status["major_version"]:
                status["major_updates"] = self.major_rows
                status["major_active"] = self.data["active"]
            if self.data and known_catalog_version is not None and known_catalog_version != status["catalog_version"]:
                status["boss_catalog"] = self.data.get("boss_catalog", [])
            return status

    def _version(self):
        # Public cache identity changes after a worker restart as well as edits.
        return hashlib.sha256(f"{self.run_id}:{self.structure_hash}".encode()).hexdigest()[:24] if self.data else ""

    def _major_version(self):
        return hashlib.sha256(f"{self.run_id}:{self.major_hash}".encode()).hexdigest()[:24] if self.data else ""

    def _catalog_version(self):
        return hashlib.sha256(f"{self.run_id}:{self.catalog_hash}".encode()).hexdigest()[:24] if self.data else ""

    def payload(self):
        now = datetime.now(timezone.utc)
        with self.lock:
            if self.data is None:
                return 503, dict(online=True, server_time=timestamp(), schedule_ready=False,
                                 schedule_stale=False, message="프로그램에서 스케줄을 준비하고 있습니다.")
            data, stale, version, major_version, catalog_version = self.data, self._stale(now), self._version(), self._major_version(), self._catalog_version()
        # Validated nested values are immutable after publication.
        return 200, dict(data, online=True, server_time=now.isoformat(timespec="milliseconds"),
                         schedule_ready=True, schedule_stale=stale,
                         schedule_version=version,
                         major_version=major_version,
                         catalog_version=catalog_version,
                         summary=schedule_summary(data, now),
                         message="스케줄 갱신이 지연되었습니다. 마지막으로 받은 자료입니다." if stale else "")
