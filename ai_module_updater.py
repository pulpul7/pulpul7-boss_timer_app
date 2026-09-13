"""Isolated, data-preserving updater for the future AI/notice module.

Only published stable Releases in REPOSITORY are trusted. This does not update
BossTimer.exe. Runtime selection accepts a host callback for checked startup;
network/installation calls remain separate and run off the UI thread.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "pulpul7/pulpul7-boss_timer_app"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
MODULE_ID = "boss_timer_notice_ai"
MODULE_API = 1
MAX_DOWNLOAD = 32 * 1024 * 1024
MAX_UNPACKED = 96 * 1024 * 1024
KST = timezone(timedelta(hours=9))
_VERSION = re.compile(r"(?:v)?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
_ASSET = re.compile(r"Update_AI_v(\d+\.\d+\.\d+)\.zip\Z")


class UpdateError(ValueError):
    pass


def version_tuple(value: str) -> tuple[int, int, int]:
    match = _VERSION.fullmatch(str(value))
    if not match:
        raise UpdateError(f"잘못된 버전: {value}")
    return tuple(int(part) for part in match.groups())


def now_text() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def fetch_bytes(url: str, limit: int) -> bytes:
    request = urllib.request.Request(url, headers={
        "User-Agent": "BossTimer-AI-Updater/1", "Accept": "application/vnd.github+json",
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        if urllib.parse.urlsplit(response.url).scheme != "https":
            raise UpdateError("HTTPS가 아닌 다운로드는 허용하지 않습니다.")
        length = int(response.headers.get("Content-Length", 0))
        if length > limit:
            raise UpdateError("다운로드 허용 용량을 초과했습니다.")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("다운로드 허용 용량을 초과했습니다.")
    return data


def release_rows(releases: list) -> list[dict]:
    rows = []
    seen = set()
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        for asset in release.get("assets", []):
            match = _ASSET.fullmatch(str(asset.get("name", "")))
            if not match:
                continue
            version = match[1]
            version_tuple(version)
            if version in seen:
                raise UpdateError(f"같은 버전의 배포 파일이 여러 개입니다: {version}")
            seen.add(version)
            digest = str(asset.get("digest") or "")
            url = str(asset.get("browser_download_url") or "")
            expected = (f"https://github.com/{REPOSITORY}/releases/download/"
                        f"{urllib.parse.quote(str(release.get('tag_name', '')), safe='')}/{asset['name']}")
            reason = ""
            if url != expected:
                reason = "공식 저장소의 배포 주소와 일치하지 않습니다."
            elif not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
                reason = "GitHub SHA-256 정보가 없어 설치할 수 없습니다."
            elif not 0 < int(asset.get("size", 0)) <= MAX_DOWNLOAD:
                reason = "배포 파일 용량이 허용 범위를 벗어났습니다."
            rows.append({"version": version, "name": asset["name"], "url": url,
                         "sha256": digest.removeprefix("sha256:").lower(),
                         "size": int(asset.get("size", 0)), "blocked": reason,
                         "date": release.get("published_at") or "",
                         "title": release.get("name") or asset["name"],
                         "description": release.get("body") or "등록된 설명이 없습니다."})
    return sorted(rows, key=lambda row: version_tuple(row["version"]), reverse=True)


def _safe_member(name: str) -> None:
    parts = name.split("/")
    if ("\\" in name or not name or any(p in {"", ".", ".."} for p in parts)
            or any(re.search(r'[<>:"|?*\x00-\x1f]', p) or p.endswith((".", " ")) for p in parts)
            or any(re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p) for p in parts)):
        raise UpdateError(f"허용하지 않는 압축 경로: {name}")
    if name != "module.json" and not name.startswith("payload/"):
        raise UpdateError("압축 파일은 module.json과 payload/만 포함해야 합니다.")
    if name != "module.json" and PurePosixPath(name).suffix.lower() not in {
        ".py", ".json", ".txt", ".md", ".png", ".jpg", ".ico", ".wav", ".mp3",
    }:
        raise UpdateError(f"허용하지 않는 파일 형식: {name}")


def inspect_package(data: bytes, row: dict, app_version: str) -> tuple[dict, dict[str, bytes]]:
    if row.get("blocked"):
        raise UpdateError(row["blocked"])
    if len(data) > MAX_DOWNLOAD or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise UpdateError("배포 파일 SHA-256 검증 실패")
    if len(data) != row["size"]:
        raise UpdateError("배포 파일 크기가 일치하지 않습니다.")
    files = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > 2000 or sum(m.file_size for m in members) > MAX_UNPACKED:
            raise UpdateError("압축 해제 용량 또는 파일 수 제한을 초과했습니다.")
        seen = set()
        for member in members:
            name = member.filename.rstrip("/") if member.is_dir() else member.filename
            # Directory entries are optional; never extract them blindly.
            if member.is_dir():
                _safe_member(name + "/placeholder.txt")
                continue
            _safe_member(name)
            if name.casefold() in seen or member.flag_bits & 1:
                raise UpdateError("중복 경로 또는 암호화된 ZIP은 허용하지 않습니다.")
            seen.add(name.casefold())
            mode = member.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                raise UpdateError("링크/특수 파일은 허용하지 않습니다.")
            files[name] = archive.read(member)
    manifest = json.loads(files.get("module.json", b"{}"))
    validate_manifest(manifest, row["version"], app_version)
    hashes = manifest.get("files")
    if not isinstance(hashes, dict) or set(hashes) != set(files) - {"module.json"}:
        raise UpdateError("module.json 파일 목록이 실제 파일과 다릅니다.")
    for name, digest in hashes.items():
        if hashlib.sha256(files[name]).hexdigest() != digest:
            raise UpdateError(f"내부 파일 검증 실패: {name}")
        if name.endswith(".py"):
            ast.parse(files[name], filename=name)
    if manifest["entrypoint"] not in files:
        raise UpdateError("모듈 진입 파일이 없습니다.")
    return manifest, files


def validate_manifest(manifest: dict, expected_version: str, app_version: str) -> None:
    if manifest.get("module_id") != MODULE_ID or manifest.get("api_version") != MODULE_API:
        raise UpdateError("지원하지 않는 모듈 또는 인터페이스입니다.")
    if manifest.get("version") != expected_version:
        raise UpdateError("파일명과 내부 버전이 일치하지 않습니다.")
    version_tuple(expected_version)
    if version_tuple(app_version) < version_tuple(manifest.get("min_app_version", "")):
        raise UpdateError("보탐매니저 본체를 먼저 업데이트해야 합니다.")
    if manifest.get("max_app_version") and version_tuple(app_version) > version_tuple(manifest["max_app_version"]):
        raise UpdateError("현재 본체와 호환되지 않는 모듈입니다.")
    if manifest.get("python_version") != f"{sys.version_info.major}.{sys.version_info.minor}":
        raise UpdateError("Python 실행 환경 버전이 일치하지 않습니다.")
    entry = str(manifest.get("entrypoint", ""))
    _safe_member(entry)
    if not entry.startswith("payload/") or not entry.endswith(".py"):
        raise UpdateError("모듈 진입 파일은 payload/ 안의 Python 파일이어야 합니다.")


class AiModuleUpdater:
    def __init__(self, root: Path, app_version: str, fetch=fetch_bytes):
        self.root = Path(root)
        self.app_version = app_version
        self.fetch = fetch
        self._thread_lock = threading.Lock()

    @contextmanager
    def _locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, (self.root / "updater.lock").open("a+b") as lock:
            if sys.platform == "win32":
                import msvcrt
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise UpdateError("다른 보탐매니저에서 업데이트를 처리 중입니다.") from exc
                try:
                    # Windows permits locking beyond EOF. Initialize only after
                    # acquiring it: even reading a byte held by another handle
                    # otherwise raises PermissionError before the lock handler.
                    if os.fstat(lock.fileno()).st_size == 0:
                        lock.write(b"0")
                        lock.flush()
                    yield
                finally:
                    lock.seek(0)
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)

    def snapshot(self) -> dict:
        try:
            state = json.loads((self.root / "state.json").read_text(encoding="utf-8"))
            if not isinstance(state, dict) or state.get("schema") != 1:
                raise UpdateError("업데이트 상태 파일 형식이 잘못되었습니다. 원본을 보존했습니다.")
            for field in ("active", "pending", "previous"):
                if state.get(field):
                    version_tuple(state[field])
            return state
        except FileNotFoundError:
            return {"schema": 1, "auto_enabled": True, "check_after": "06:00",
                    "active": "", "pending": "", "previous": "", "catalog": [], "history": []}

    def _save(self, state: dict) -> None:
        fd, name = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(state, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.root / "state.json")
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def record(self, status: str, version: str = "", detail: str = "") -> None:
        with self._locked():
            state = self.snapshot()
            state["history"].append({"date": now_text(), "version": version, "status": status, "detail": detail})
            self._save(state)

    def configure(self, auto_enabled: bool, check_after: str) -> None:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", check_after):
            raise UpdateError("확인 시작 시각은 HH:MM 형식으로 입력해 주세요.")
        with self._locked():
            state = self.snapshot()
            state.update(auto_enabled=bool(auto_enabled), check_after=check_after)
            self._save(state)

    def claim_daily_check(self, now: datetime | None = None) -> bool:
        now = (now or datetime.now(KST)).astimezone(KST)
        with self._locked():
            state = self.snapshot()
            today = now.date().isoformat()
            if (not state["auto_enabled"] or now.strftime("%H:%M") < state["check_after"]
                    or state.get("last_attempt_day") == today):
                return False
            state["last_attempt_day"] = today
            self._save(state)
            return True

    def check(self) -> list[dict]:
        releases = []
        for page in range(1, 21):
            chunk = json.loads(self.fetch(f"{API_URL}?per_page=100&page={page}", 8 * 1024 * 1024))
            if not isinstance(chunk, list):
                raise UpdateError("GitHub 배포 목록을 가져오지 못했습니다.")
            releases.extend(chunk)
            if len(chunk) < 100:
                break
        else:
            raise UpdateError("배포 목록이 너무 많아 조회를 완료하지 못했습니다. 기존 기록을 유지합니다.")
        rows = release_rows(releases)
        with self._locked():
            state = self.snapshot()
            state.update(catalog=rows, last_checked=now_text())
            self._save(state)
        return rows

    @staticmethod
    def newer(version: str, state: dict) -> bool:
        installed = [version_tuple(state[key]) for key in ("active", "pending", "baseline_version") if state.get(key)]
        return not installed or version_tuple(version) > max(installed)

    def install(self, version: str) -> None:
        # Fresh release data, not a stale UI row, is the first line of defence.
        version_tuple(version)
        rows = self.check()
        row = next((row for row in rows if row["version"] == version), None)
        if row is None or row["blocked"]:
            raise UpdateError(row["blocked"] if row else "이 버전은 현재 공식 배포 목록에 없습니다.")
        if not self.newer(version, self.snapshot()):
            raise UpdateError("현재/설치 대기 버전보다 높은 버전만 설치할 수 있습니다.")
        data = self.fetch(row["url"], MAX_DOWNLOAD)
        manifest, files = inspect_package(data, row, self.app_version)
        with self._locked():
            state = self.snapshot()
            # Second check after download, under a cross-process lock.
            if not self.newer(version, state):
                raise UpdateError("다운로드 중 버전이 변경되었습니다. 구버전 설치를 거부했습니다.")
            versions = self.root / "versions"
            versions.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="stage-", dir=self.root) as stage_name:
                stage = Path(stage_name)
                for name, content in files.items():
                    target = stage / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                # Unique directory: never overwrite a running module or user files.
                folder = f"{version}-{hashlib.sha256(data).hexdigest()[:16]}"
                target = versions / folder
                if target.exists():
                    self._verify_directory(target, manifest)
                else:
                    os.replace(stage, target)
                state.setdefault("packages", {})[version] = {"folder": folder, "manifest": manifest}
                state["pending"] = version
                state.setdefault("failed_versions", {}).pop(version, None)
                state["history"].append({"date": now_text(), "version": version,
                                         "status": "설치 완료 · 다음 실행 적용", "detail": row["name"]})
                self._save(state)

    def _verify_directory(self, directory: Path, manifest: dict) -> None:
        if directory.is_symlink():
            raise UpdateError("모듈 폴더 링크는 허용하지 않습니다.")
        expected = {"module.json", *manifest["files"]}
        if {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()} != expected:
            raise UpdateError("설치된 모듈 파일 목록이 일치하지 않습니다.")
        for name, digest in manifest["files"].items():
            _safe_member(name)
            target = directory / name
            if not target.resolve().is_relative_to(directory.resolve()) or target.is_symlink():
                raise UpdateError("모듈 폴더 밖의 파일은 허용하지 않습니다.")
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise UpdateError(f"설치된 파일이 손상되었습니다: {name}")
        if json.loads((directory / "module.json").read_bytes()) != manifest:
            raise UpdateError("설치된 모듈 정보가 변경되었습니다.")

    def _package_path(self, state: dict, version: str) -> Path:
        package = state["packages"][version]
        folder = package["folder"]
        if not re.fullmatch(re.escape(version) + r"-[0-9a-f]{16}", folder):
            raise UpdateError("설치 폴더 정보가 잘못되었습니다.")
        directory = self.root / "versions" / folder
        validate_manifest(package["manifest"], version, self.app_version)
        self._verify_directory(directory, package["manifest"])
        return directory

    def activate_pending(self) -> None:
        """Select verified files for the next host session; never execute code here."""
        with self._locked():
            state = self.snapshot()
            version = state.get("pending")
            if not version:
                return
            try:
                self._package_path(state, version)
                if state.get("active") and version_tuple(version) <= version_tuple(state["active"]):
                    raise UpdateError("동일/구버전 적용을 거부했습니다.")
            except Exception as exc:
                state["pending"] = ""
                state["history"].append({"date": now_text(), "version": version,
                                         "status": "적용 실패", "detail": str(exc)})
                self._save(state)
                return
            state.update(previous=state.get("active", ""), active=version, pending="")
            state["history"].append({"date": now_text(), "version": version,
                                     "status": "파일 적용 완료", "detail": "이전 버전 보존"})
            self._save(state)

    def active_module_path(self) -> Path | None:
        state = self.snapshot()
        return self._package_path(state, state["active"]) if state.get("active") else None

    def start_runtime(self, bundled_path: Path, bundled_manifest: dict, launch):
        """Call on the GUI thread, once per session, before opening update UI.

        launch(path, manifest) must start/check a plugin without network, data
        migrations or synchronous background work, cleaning up on exceptions.
        The cross-process lock covers startup so another instance cannot treat
        a live trial as a crashed one. A stale trial means the previous process
        exited before confirming startup; skip that version on the next launch.
        """
        baseline = bundled_manifest["version"]
        validate_manifest(bundled_manifest, baseline, self.app_version)
        with self._locked():
            state = self.snapshot()
            state["baseline_version"] = baseline
            failed = state.setdefault("failed_versions", {})
            trial = state.pop("runtime_trial", None)
            if trial and trial.get("source") == "installed":
                failed[trial["version"]] = "이전 실행에서 모듈 시작 확인을 완료하지 못했습니다."
                state["history"].append({"date": now_text(), "version": trial["version"],
                    "status": "시작 중단 감지", "detail": failed[trial["version"]]})
                if state.get("pending") == trial["version"]:
                    state["pending"] = ""
            pending = state.get("pending")
            if pending and not self.newer(pending, {"active": state.get("active"), "baseline_version": baseline}):
                state["pending"] = ""
                state["history"].append({"date": now_text(), "version": pending,
                    "status": "적용 거부", "detail": "현재/내장 버전보다 높은 버전이 아닙니다."})
            candidates = []
            for key in ("pending", "active", "previous"):
                version = state.get(key)
                if (version and version not in failed and version not in candidates
                        and version_tuple(version) > version_tuple(baseline)):
                    candidates.append(version)
            candidates.append(None)  # Trusted source shipped inside this EXE.
            self._save(state)
            for version in candidates:
                source = "installed" if version else "bundled"
                state["runtime_trial"] = {"version": version or baseline, "source": source}
                self._save(state)
                session = None
                try:
                    manifest = state["packages"][version]["manifest"] if version else bundled_manifest
                    path = self._package_path(state, version) if version else bundled_path
                    session = launch(path, manifest)
                    committed = deepcopy(state)
                    if version and version != committed.get("active"):
                        old = committed.get("active")
                        if old and old not in failed:
                            committed["previous"] = old
                    committed["active"] = version or ""
                    if committed.get("pending") == version:
                        committed["pending"] = ""
                    committed.pop("runtime_trial", None)
                    committed["runtime_loaded"] = {"version": version or baseline, "source": source, "at": now_text()}
                    committed["history"].append({"date": now_text(), "version": version or baseline,
                        "status": "모듈 시작 확인", "detail": "설치 모듈" if version else "내장 기본 모듈"})
                    self._save(committed)
                    return session
                except (Exception, SystemExit) as exc:
                    if session is not None:
                        session.close()
                    state.pop("runtime_trial", None)
                    reason = str(exc) or type(exc).__name__
                    if version:
                        failed[version] = reason
                        if state.get("pending") == version:
                            state["pending"] = ""
                    state["history"].append({"date": now_text(), "version": version or baseline,
                        "status": "모듈 시작 실패 · 복구 시도", "detail": reason})
                    self._save(state)
                    if not version:
                        raise UpdateError(f"내장 알림 모듈도 시작하지 못했습니다: {reason}") from exc
