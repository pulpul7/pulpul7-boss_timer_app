"""Installer and validation helpers for the optional Edge TTS module.

The main executable intentionally does not ship the online TTS dependency.  The
module is a versioned ZIP published with the application on GitHub Releases and
is installed under the user's BossTimer settings directory.
"""

from __future__ import annotations

import json
import os
import shutil
import ssl
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


EDGE_TTS_MODULE_VERSION = "1.0.0"
EDGE_TTS_MODULE_ASSET_NAME = f"boss_timer_edge_tts_module-v{EDGE_TTS_MODULE_VERSION}.zip"
EDGE_TTS_MODULE_RELEASE_URL = (
    "https://github.com/pulpul7/boss_timer/releases/download/"
    f"tts-module-v{EDGE_TTS_MODULE_VERSION}/{EDGE_TTS_MODULE_ASSET_NAME}"
)
MODULE_MANIFEST_FILENAME = "module.json"


def _create_download_ssl_context() -> ssl.SSLContext:
    """Use Windows CryptoAPI trust for this download, without global injection.

    This dependency belongs in the main EXE: the optional ZIP isn't installed
    yet. Missing truststore must not silently switch verification backends.
    """
    if sys.platform != "win32":
        return ssl.create_default_context()
    try:
        import truststore
    except ImportError as exc:
        raise RuntimeError(
            "Windows 인증서 저장소를 사용하는 truststore가 프로그램에 포함되지 않았습니다. "
            "수정된 보탐매니저 배포본을 사용해 주세요. 소스 실행/빌드 환경에서는 "
            "python -m pip install -r requirements-gui.txt 를 먼저 실행해 주세요."
        ) from exc
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    return context


@dataclass(frozen=True)
class EdgeTtsModuleStatus:
    installed: bool
    module_dir: str
    version: str = ""
    reason: str = ""


def get_default_edge_tts_module_dir() -> str:
    base_dir = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base_dir, "BossTimer", "tts_module")


def get_edge_tts_module_download_url() -> str:
    """An environment override is useful for private/test release channels."""
    return str(os.environ.get("BOSS_TIMER_EDGE_TTS_MODULE_URL") or EDGE_TTS_MODULE_RELEASE_URL).strip()


def get_edge_tts_module_status(module_dir: str | None = None) -> EdgeTtsModuleStatus:
    root = Path(module_dir or get_default_edge_tts_module_dir()).expanduser()
    manifest_path = root / MODULE_MANIFEST_FILENAME
    package_dir = root / "packages" / "edge_tts"
    try:
        is_installed_layout = manifest_path.is_file() and package_dir.is_dir()
    except OSError as exc:
        return EdgeTtsModuleStatus(False, str(root), reason=f"TTS 모듈 폴더에 접근할 수 없습니다: {exc}")
    if not is_installed_layout:
        return EdgeTtsModuleStatus(False, str(root), reason="설치된 TTS 모듈이 없습니다.")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return EdgeTtsModuleStatus(False, str(root), reason="TTS 모듈 정보 파일을 읽을 수 없습니다.")
    if not isinstance(payload, dict) or str(payload.get("module") or "") != "boss_timer_edge_tts":
        return EdgeTtsModuleStatus(False, str(root), reason="TTS 모듈 정보가 올바르지 않습니다.")
    required_python = str(payload.get("python") or "").strip()
    current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if required_python and required_python != current_python:
        return EdgeTtsModuleStatus(
            False,
            str(root),
            version=str(payload.get("version") or ""),
            reason=f"TTS 모듈 Python {required_python} / 프로그램 Python {current_python} 버전이 다릅니다.",
        )
    return EdgeTtsModuleStatus(True, str(root), version=str(payload.get("version") or ""))


def _safe_extract_archive(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path, "r") as archive:
        for member in archive.infolist():
            target_path = (destination / member.filename).resolve()
            try:
                target_path.relative_to(destination.resolve())
            except ValueError as exc:
                raise RuntimeError("TTS 모듈 압축 파일에 허용되지 않은 경로가 있습니다.") from exc
        archive.extractall(destination)


def install_edge_tts_module(
    module_dir: str | None = None,
    *,
    download_url: str | None = None,
    urlopen=urllib.request.urlopen,
) -> EdgeTtsModuleStatus:
    """Download and atomically replace the optional module installation."""
    target = Path(module_dir or get_default_edge_tts_module_dir()).expanduser()
    url = str(download_url or get_edge_tts_module_download_url()).strip()
    if not url:
        raise RuntimeError("TTS 모듈 다운로드 주소가 설정되지 않았습니다.")
    context = _create_download_ssl_context()
    target.parent.mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix="boss_timer_tts_module_", dir=str(target.parent)))
    archive_path = work_dir / "module.zip"
    extracted_dir = work_dir / "extracted"
    backup_dir = target.with_name(f"{target.name}.previous")
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "BossTimer-TTS-Module"})
        try:
            with urlopen(request, timeout=90, context=context) as response, archive_path.open("wb") as stream:
                shutil.copyfileobj(response, stream)
        except urllib.error.HTTPError as exc:
            if int(getattr(exc, "code", 0) or 0) == 404:
                raise RuntimeError(
                    "GitHub Release 파일을 찾을 수 없습니다. "
                    f"태그 tts-module-v{EDGE_TTS_MODULE_VERSION}에 "
                    f"{EDGE_TTS_MODULE_ASSET_NAME} 파일이 공개 상태로 첨부되어 있는지 확인해주세요."
                ) from exc
            raise
        except (urllib.error.URLError, ssl.SSLCertVerificationError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
                store = "Windows 인증서 저장소" if sys.platform == "win32" else "시스템 인증서 저장소"
                raise RuntimeError(
                    f"{store}를 사용했지만 TTS 다운로드 서버의 인증서를 검증하지 못했습니다.\n"
                    "PC의 날짜/시간과 Windows 업데이트 상태를 확인해 주세요. "
                    "백신·회사 프록시가 HTTPS를 검사하는 환경이라면 해당 관리자의 인증서 설정 확인이 필요합니다.\n"
                    "인증서 검증을 끄지 않았으며 기존 TTS 모듈은 변경하지 않았습니다.\n"
                    f"상세: {reason}"
                ) from exc
            raise
        if not zipfile.is_zipfile(archive_path):
            raise RuntimeError("다운로드한 TTS 모듈 파일이 ZIP 형식이 아닙니다.")
        extracted_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract_archive(archive_path, extracted_dir)
        # Release archives may contain one top-level directory, but the installed
        # layout is always module.json + packages/ at the module root.
        candidate = extracted_dir
        children = [item for item in extracted_dir.iterdir() if item.name not in {"__MACOSX"}]
        if not (candidate / MODULE_MANIFEST_FILENAME).is_file() and len(children) == 1 and children[0].is_dir():
            candidate = children[0]
        staged_status = get_edge_tts_module_status(str(candidate))
        if not staged_status.installed:
            raise RuntimeError(staged_status.reason or "다운로드한 TTS 모듈을 확인할 수 없습니다.")
        if backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)
        if target.exists():
            target.replace(backup_dir)
        shutil.move(str(candidate), str(target))
        shutil.rmtree(backup_dir, ignore_errors=True)
        installed_status = get_edge_tts_module_status(str(target))
        if not installed_status.installed:
            raise RuntimeError(installed_status.reason or "TTS 모듈 설치를 확인할 수 없습니다.")
        return installed_status
    except Exception:
        if not target.exists() and backup_dir.exists():
            try:
                backup_dir.replace(target)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
