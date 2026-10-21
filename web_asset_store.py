"""Validated, immutable web pages. Downloaded files never become Python code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import uuid

from web_service_common import diagnostic, read_json, write_json

FILES = ("index.html", "style.css", "schedule.js", "login.html", "login.js", "metadata.json")
MAX_FILE = 1024 * 1024
ASSET_API = 1
LOGIN_API = 2


def git_hash(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def version_id(hashes):
    if set(hashes) != set(FILES) or any(not re.fullmatch(r"[0-9a-f]{40}", v) for v in hashes.values()):
        raise ValueError("웹 파일 목록 또는 해시가 올바르지 않습니다.")
    return hashlib.sha256("\n".join(name + ":" + hashes[name] for name in FILES).encode()).hexdigest()


def page_revision(files):
    metadata = json.loads(files["metadata.json"])
    if not isinstance(metadata, dict):
        raise ValueError("웹 화면 개정 정보가 올바르지 않습니다.")
    value = metadata.get("page_revision", 0)
    if type(value) is not int or not 0 <= value <= 2147483647:
        raise ValueError("웹 화면 개정 번호가 올바르지 않습니다.")
    return value


def validate_assets(files):
    if set(files) != set(FILES):
        raise ValueError("웹 업데이트에 필요한 파일이 빠졌습니다.")
    for raw in files.values():
        if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_FILE or b"\0" in raw:
            raise ValueError("웹 파일 크기 또는 형식이 올바르지 않습니다.")
        raw.decode("utf-8-sig")
    meta = json.loads(files["metadata.json"])
    if not isinstance(meta, dict) or meta.get("schema") != 1 or meta.get("asset_api") != ASSET_API:
        raise ValueError("현재 프로그램과 호환되지 않는 웹 업데이트입니다. GUI 업데이트가 필요합니다.")
    if meta.get("login_api", 1) != LOGIN_API:
        raise ValueError("구버전 입장 화면입니다. web_assets에 닉네임·게스트 비밀번호를 지원하는 새 웹 파일을 올려주세요.")
    page_revision(files)
    for name, script in (("index.html", "/schedule.js"), ("login.html", "/login.js")):
        page = files[name].decode("utf-8-sig").lower()
        if "<html" not in page or "</html>" not in page or script not in page or "/style.css" not in page:
            raise ValueError("웹 페이지의 필수 HTML·파일 연결을 확인하지 못했습니다.")


def bundled_assets():
    folder = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "web_assets"
    if folder.is_dir():
        files = {name: (folder / name).read_bytes() for name in FILES}
    else:
        from web_service_page import PAGE, SCRIPT, STYLE
        from web_service_login import LOGIN_PAGE, LOGIN_SCRIPT
        files = dict(zip(FILES, (PAGE, STYLE, SCRIPT, LOGIN_PAGE, LOGIN_SCRIPT,
            b'{"schema":1,"asset_api":1,"login_api":2,"page_revision":223,"version":"v1.0.1"}')))
    validate_assets(files)
    return files


def read_version(root, version):
    if not isinstance(version, str) or re.fullmatch(r"[0-9a-f]{64}", version) is None:
        raise ValueError("잘못된 웹 업데이트 버전입니다.")
    folder = Path(root) / "pages" / "versions" / version
    files = {}
    for name in FILES:
        path = folder / name
        if path.stat().st_size > MAX_FILE:
            raise ValueError("웹 파일 용량을 초과했습니다.")
        files[name] = path.read_bytes()
    validate_assets(files)
    if version_id({name: git_hash(raw) for name, raw in files.items()}) != version:
        raise ValueError("웹 업데이트 파일 검증에 실패했습니다.")
    return files


def current_version(root):
    files = load_assets(root)
    return version_id({name: git_hash(raw) for name, raw in files.items()})


def stage_version(root, version, files):
    validate_assets(files)
    if version_id({name: git_hash(raw) for name, raw in files.items()}) != version:
        raise ValueError("다운로드한 웹 파일 해시가 일치하지 않습니다.")
    parent = Path(root) / "pages" / "versions"
    parent.mkdir(parents=True, exist_ok=True)
    final = parent / version
    if final.exists():
        read_version(root, version)
        return
    stage = parent / (version + "." + uuid.uuid4().hex + ".tmp")
    stage.mkdir()
    try:
        for name, raw in files.items():
            (stage / name).write_bytes(raw)
        copied = {name: (stage / name).read_bytes() for name in FILES}
        validate_assets(copied)
        if version_id({name: git_hash(raw) for name, raw in copied.items()}) != version:
            raise ValueError("웹 파일 복사 후 검증에 실패했습니다.")
        stage.rename(final)
    finally:
        # Only known staging files under this updater-owned directory are removed.
        for name in FILES:
            (stage / name).unlink(missing_ok=True)
        if stage.exists():
            stage.rmdir()


def load_assets(root, logger=None):
    bundled = bundled_assets()

    def select(files):
        if page_revision(files) < page_revision(bundled):
            if logger:
                diagnostic(logger, "web_pages_bundled_preferred",
                           installed_revision=page_revision(files), bundled_revision=page_revision(bundled))
            return bundled
        return files

    try:
        pointer = read_json(Path(root) / "pages" / "current.json")
    except FileNotFoundError:
        pointer = None
    except (OSError, ValueError):
        pointer = {}
    if pointer is not None:
        try:
            return select(read_version(root, pointer["version"]))
        except (OSError, ValueError, KeyError, TypeError):
            if logger:
                diagnostic(logger, "web_pages_load_failed")
            try:
                return select(read_version(root, pointer["previous"]))
            except (OSError, ValueError, KeyError, TypeError):
                pass
    return bundled
