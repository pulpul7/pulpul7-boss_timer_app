"""Session-prefetched Microsoft Edge online TTS audio for BossTimer."""

from __future__ import annotations

import asyncio
import configparser
import hashlib
import importlib
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from edge_tts_module import get_default_edge_tts_module_dir, get_edge_tts_module_status


# The Edge online voice package is deliberately loaded only from the optional
# module installation in release builds.  Keeping this dynamic import prevents
# PyInstaller from folding edge-tts and its dependencies into the main EXE.
_edge_tts = None
_edge_tts_module_dir = ""
_edge_tts_module_error = "TTS 모듈을 설치해주세요."


DEFAULT_EDGE_TTS_VOICE = "ko-KR-SunHiNeural"
DEFAULT_EDGE_TTS_RATE = 0
DEFAULT_EDGE_TTS_VOLUME = 0
DEFAULT_EDGE_TTS_PITCH = 15
EDGE_TTS_CACHE_FORMAT_VERSION = "strict-selected-voice-v2"
EDGE_TTS_KOREAN_VOICES = (
    {"ShortName": "ko-KR-SunHiNeural", "Gender": "Female", "Locale": "ko-KR"},
    {"ShortName": "ko-KR-InJoonNeural", "Gender": "Male", "Locale": "ko-KR"},
    {"ShortName": "ko-KR-HyunsuMultilingualNeural", "Gender": "Male", "Locale": "ko-KR"},
)
def _bounded_int(value: object, fallback: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _signed(value: int, suffix: str) -> str:
    return f"{int(value):+d}{suffix}"


def _replace_file_with_retry(source_path: str, target_path: str, *, attempts: int = 40) -> None:
    """Atomically replace a file while tolerating transient Windows file locks."""
    last_error: OSError | None = None
    for attempt in range(max(1, int(attempts))):
        try:
            os.replace(source_path, target_path)
            return
        except OSError as exc:
            last_error = exc
            retriable = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {5, 32}
            if not retriable or attempt >= max(1, int(attempts)) - 1:
                raise
            time.sleep(min(0.2, 0.02 + attempt * 0.01))
    if last_error is not None:
        raise last_error


@dataclass(frozen=True)
class EdgeTtsSettings:
    enabled: bool = True
    voice: str = DEFAULT_EDGE_TTS_VOICE
    rate: int = DEFAULT_EDGE_TTS_RATE
    volume: int = DEFAULT_EDGE_TTS_VOLUME
    pitch: int = DEFAULT_EDGE_TTS_PITCH

    def normalized(self) -> "EdgeTtsSettings":
        return replace(
            self,
            voice=str(self.voice or DEFAULT_EDGE_TTS_VOICE).strip() or DEFAULT_EDGE_TTS_VOICE,
            rate=_bounded_int(self.rate, 0, -50, 100),
            volume=_bounded_int(self.volume, 0, -100, 100),
            pitch=_bounded_int(self.pitch, 0, -100, 100),
        )

    @property
    def rate_value(self) -> str:
        return _signed(self.normalized().rate, "%")

    @property
    def volume_value(self) -> str:
        return _signed(self.normalized().volume, "%")

    @property
    def pitch_value(self) -> str:
        return _signed(self.normalized().pitch, "Hz")

    def cache_signature(self) -> str:
        normalized = self.normalized()
        return "|".join(
            (
                EDGE_TTS_CACHE_FORMAT_VERSION,
                normalized.voice,
                normalized.rate_value,
                normalized.volume_value,
                normalized.pitch_value,
            )
        )


def configure_edge_tts_module(
    module_dir: str | None = None,
    *,
    allow_development_fallback: bool = False,
) -> bool:
    """Load the optional module after it has been installed or updated."""
    global _edge_tts, _edge_tts_module_dir, _edge_tts_module_error
    _edge_tts = None
    _edge_tts_module_dir = ""
    status = get_edge_tts_module_status(module_dir or get_default_edge_tts_module_dir())
    package_root = os.path.join(status.module_dir, "packages") if status.installed else ""
    if package_root:
        normalized_root = os.path.normcase(os.path.abspath(package_root))
        sys.path[:] = [
            item
            for item in sys.path
            if os.path.normcase(os.path.abspath(str(item or "."))) != normalized_root
        ]
        sys.path.insert(0, package_root)
        importlib.invalidate_caches()
        # A just-installed module must not keep a failed/old import cached.
        for module_name in tuple(sys.modules):
            if module_name == "edge_tts" or module_name.startswith("edge_tts."):
                sys.modules.pop(module_name, None)
        try:
            _edge_tts = importlib.import_module("edge_tts")
            _edge_tts_module_dir = status.module_dir
            _edge_tts_module_error = ""
            return True
        except Exception as exc:
            _edge_tts_module_error = f"TTS 모듈을 불러오지 못했습니다: {type(exc).__name__}: {exc}"
            return False
    if allow_development_fallback:
        try:
            _edge_tts = importlib.import_module("edge_tts")
            _edge_tts_module_dir = "개발 환경"
            _edge_tts_module_error = ""
            return True
        except Exception:
            pass
    _edge_tts_module_error = status.reason or "TTS 모듈을 설치해주세요."
    return False


def edge_tts_available() -> bool:
    return _edge_tts is not None


def get_edge_tts_module_error() -> str:
    return str(_edge_tts_module_error or "TTS 모듈을 설치해주세요.")


# Source runs remain convenient for development.  Frozen distributions call
# configure_edge_tts_module from the GUI and therefore require the module ZIP.
configure_edge_tts_module(allow_development_fallback=not bool(getattr(sys, "frozen", False)))


def load_edge_tts_settings(path: str) -> EdgeTtsSettings:
    parser = configparser.ConfigParser()
    try:
        parser.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        parser = configparser.ConfigParser()
    section = parser["edge_tts"] if parser.has_section("edge_tts") else {}
    raw_enabled = str(section.get("enabled", "1") or "1").strip().lower()
    return EdgeTtsSettings(
        enabled=raw_enabled in {"1", "true", "yes", "on"},
        voice=str(section.get("voice", DEFAULT_EDGE_TTS_VOICE) or DEFAULT_EDGE_TTS_VOICE),
        rate=_bounded_int(section.get("rate", DEFAULT_EDGE_TTS_RATE), DEFAULT_EDGE_TTS_RATE, -50, 100),
        volume=_bounded_int(section.get("volume", DEFAULT_EDGE_TTS_VOLUME), DEFAULT_EDGE_TTS_VOLUME, -100, 100),
        pitch=_bounded_int(section.get("pitch", DEFAULT_EDGE_TTS_PITCH), DEFAULT_EDGE_TTS_PITCH, -100, 100),
    ).normalized()


def save_edge_tts_settings(path: str, settings: EdgeTtsSettings) -> None:
    normalized = settings.normalized()
    parser = configparser.ConfigParser()
    parser["edge_tts"] = {
        "enabled": "1" if normalized.enabled else "0",
        "voice": normalized.voice,
        "rate": str(normalized.rate),
        "volume": str(normalized.volume),
        "pitch": str(normalized.pitch),
    }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        parser.write(stream)
    _replace_file_with_retry(str(temporary), str(target))


def list_edge_tts_voices(locale: str = "ko-KR") -> list[dict[str, str]]:
    if _edge_tts is None:
        raise RuntimeError("edge-tts 패키지가 설치되어 있지 않습니다.")
    voices = asyncio.run(_edge_tts.list_voices())
    locale_key = str(locale or "").strip().casefold()
    filtered: list[dict[str, str]] = []
    for raw_voice in voices:
        if not isinstance(raw_voice, dict):
            continue
        voice_locale = str(raw_voice.get("Locale") or "").strip()
        if locale_key and voice_locale.casefold() != locale_key:
            continue
        short_name = str(raw_voice.get("ShortName") or raw_voice.get("Name") or "").strip()
        if not short_name:
            continue
        filtered.append(
            {
                "ShortName": short_name,
                "Gender": str(raw_voice.get("Gender") or "").strip(),
                "Locale": voice_locale,
            }
        )
    filtered.sort(key=lambda item: (item["Gender"], item["ShortName"]))
    return filtered


class EdgeTtsCache:
    """Single-worker persistent cache for prefetched Edge TTS MP3 files."""

    def __init__(
        self,
        settings: EdgeTtsSettings,
        *,
        logger: Callable[[str], None] | None = None,
        persistent_dir: str | None = None,
    ) -> None:
        self._settings = settings.normalized()
        self._logger = logger
        self._cache: dict[str, str] = {}
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._queue: queue.Queue[tuple[str, str, EdgeTtsSettings, int, int, str, int] | None] = queue.Queue()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._revision = 0
        self._temp_dir = ""
        self._persistent_dir = os.path.abspath(str(persistent_dir or "").strip()) if str(persistent_dir or "").strip() else ""
        self._manifest_path = os.path.join(self._persistent_dir, "manifest.json") if self._persistent_dir else ""
        self._manifest_entries = self._load_manifest_entries()
        self.last_error = ""
        self.last_voice = ""
        self.last_clear_removed_count = 0
        try:
            self.startup_pruned_count = self._prune_persistent_cache_for_active_settings()
        except OSError as exc:
            # 백신/인덱서의 순간 잠금 때문에 앱 시작 자체가 실패하지 않게 한다.
            self.startup_pruned_count = 0
            self.last_error = f"manifest_startup_save_failed {type(exc).__name__}: {exc}"
            self._log(self.last_error)

    def _load_manifest_entries(self) -> dict[str, dict[str, object]]:
        if not self._manifest_path:
            return {}
        try:
            with open(self._manifest_path, "r", encoding="utf-8") as stream:
                payload = json.load(stream)
        except (OSError, ValueError, TypeError):
            return {}
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            return {}
        return {
            str(key): dict(value)
            for key, value in entries.items()
            if isinstance(value, dict)
        }

    def _save_manifest_locked(self) -> None:
        if not self._manifest_path:
            return
        os.makedirs(self._persistent_dir, exist_ok=True)
        temporary_path = (
            f"{self._manifest_path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        )
        payload = {"version": 1, "entries": self._manifest_entries}
        try:
            with open(temporary_path, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            _replace_file_with_retry(temporary_path, self._manifest_path)
        finally:
            try:
                os.remove(temporary_path)
            except OSError:
                pass

    def _prune_persistent_cache_for_active_settings(self) -> int:
        if not self._persistent_dir or not os.path.isdir(self._persistent_dir):
            return 0
        retained_entries: dict[str, dict[str, object]] = {}
        retained_paths: set[str] = set()
        for cache_key, entry in self._manifest_entries.items():
            try:
                rate_steps = int(entry.get("rate_steps") or 0)
            except (TypeError, ValueError):
                rate_steps = 0
            try:
                volume_steps = int(entry.get("volume_steps") or 0)
            except (TypeError, ValueError):
                volume_steps = 0
            expected_settings = replace(
                self._settings,
                rate=self._settings.rate + rate_steps * 10,
                volume=self._settings.volume + volume_steps,
            ).normalized().cache_signature()
            relative_path = self._normalize_persistent_relpath(entry.get("path"))
            absolute_path = os.path.abspath(os.path.join(self._persistent_dir, relative_path)) if relative_path else ""
            try:
                path_is_owned = bool(
                    absolute_path
                    and os.path.commonpath((self._persistent_dir, absolute_path)) == self._persistent_dir
                )
            except ValueError:
                path_is_owned = False
            if (
                str(entry.get("settings") or "") != expected_settings
                or not path_is_owned
                or not os.path.isfile(absolute_path)
            ):
                continue
            retained_entries[str(cache_key)] = dict(entry)
            retained_paths.add(os.path.normcase(absolute_path))

        removed_count = 0
        manifest_path_key = os.path.normcase(os.path.abspath(self._manifest_path)) if self._manifest_path else ""
        for current_root, _dirs, filenames in os.walk(self._persistent_dir):
            for filename in filenames:
                file_path = os.path.abspath(os.path.join(current_root, filename))
                file_key = os.path.normcase(file_path)
                if file_key == manifest_path_key or file_key in retained_paths:
                    continue
                try:
                    os.remove(file_path)
                    removed_count += 1
                except OSError:
                    pass
        for current_root, dirnames, _files in os.walk(self._persistent_dir, topdown=False):
            for dirname in dirnames:
                try:
                    os.rmdir(os.path.join(current_root, dirname))
                except OSError:
                    pass
        self._manifest_entries = retained_entries
        with self._lock:
            self._save_manifest_locked()
        return int(removed_count)

    def _normalize_persistent_relpath(self, value: object) -> str:
        raw_value = str(value or "").strip().replace("\\", "/")
        if not raw_value:
            return ""
        normalized = os.path.normpath(raw_value).replace("\\", "/")
        if normalized.startswith("../") or normalized == ".." or os.path.isabs(normalized):
            return ""
        return normalized

    def _get_manifest_cached_path(self, cache_key: str) -> str | None:
        if not self._persistent_dir:
            return None
        with self._lock:
            entry = self._manifest_entries.get(cache_key)
            relative_path = self._normalize_persistent_relpath(entry.get("path")) if isinstance(entry, dict) else ""
        if not relative_path:
            return None
        absolute_path = os.path.abspath(os.path.join(self._persistent_dir, relative_path))
        try:
            if os.path.commonpath((self._persistent_dir, absolute_path)) != self._persistent_dir:
                return None
        except ValueError:
            return None
        return absolute_path if os.path.isfile(absolute_path) else None

    def _register_persistent_path(
        self,
        cache_key: str,
        path: str,
        text: str,
        settings: EdgeTtsSettings,
        rate_steps: int,
        volume_steps: int,
        *,
        expected_revision: int,
    ) -> bool:
        if not self._persistent_dir:
            return True
        relative_path = os.path.relpath(path, self._persistent_dir).replace("\\", "/")
        with self._lock:
            if int(expected_revision) != self._revision:
                return False
            for existing_key, entry in list(self._manifest_entries.items()):
                if existing_key != cache_key and str(entry.get("path") or "") == relative_path:
                    self._manifest_entries.pop(existing_key, None)
                    self._cache.pop(existing_key, None)
            self._manifest_entries[cache_key] = {
                "path": relative_path,
                "text": text,
                "rate_steps": int(rate_steps),
                "volume_steps": int(volume_steps),
                "settings": settings.cache_signature(),
                "updated_at": int(time.time()),
            }
            self._save_manifest_locked()
        return True

    @property
    def settings(self) -> EdgeTtsSettings:
        with self._lock:
            return self._settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.enabled and edge_tts_available())

    def update_settings(self, settings: EdgeTtsSettings) -> int:
        normalized = settings.normalized()
        with self._lock:
            changed = normalized != self._settings
            self._settings = normalized
        if changed:
            return self.clear(persistent=True)
        return 0

    def _log(self, message: str) -> None:
        if self._logger is None:
            return
        try:
            self._logger(message)
        except Exception:
            pass

    def _settings_with_rate_steps(self, rate_steps: int = 0, volume_steps: int = 0) -> EdgeTtsSettings:
        active = self.settings
        try:
            rate_step_value = int(rate_steps)
        except (TypeError, ValueError):
            rate_step_value = 0
        try:
            volume_step_value = int(volume_steps)
        except (TypeError, ValueError):
            volume_step_value = 0
        if rate_step_value == 0 and volume_step_value == 0:
            return active
        return replace(
            active,
            rate=active.rate + rate_step_value * 10,
            volume=active.volume + volume_step_value,
        ).normalized()

    def _key(self, text: str, settings: EdgeTtsSettings | None = None) -> str:
        active = (settings or self.settings).normalized()
        material = f"{active.cache_signature()}\n{text}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def get(self, text: object, *, rate_steps: int = 0, volume_steps: int = 0) -> str | None:
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return None
        settings = self._settings_with_rate_steps(rate_steps, volume_steps)
        cache_key = self._key(normalized_text, settings)
        with self._lock:
            path = self._cache.get(cache_key)
        if path and os.path.isfile(path):
            return path
        if path:
            with self._lock:
                self._cache.pop(cache_key, None)
        path = self._get_manifest_cached_path(cache_key)
        if path:
            with self._lock:
                self._cache[cache_key] = path
            return path
        return None

    def owns_path(self, path: object) -> bool:
        raw_path = str(path or "").strip()
        if not raw_path:
            return False
        try:
            normalized_path = os.path.normcase(os.path.abspath(raw_path))
        except OSError:
            return False
        with self._lock:
            cached_paths = tuple(self._cache.values())
        return any(
            os.path.normcase(os.path.abspath(cached_path)) == normalized_path
            for cached_path in cached_paths
        )

    def prefetch(
        self,
        text: object,
        *,
        rate_steps: int = 0,
        volume_steps: int = 0,
        persistent_relpath: str = "",
    ) -> bool:
        normalized_text = str(text or "").strip()
        settings = self._settings_with_rate_steps(rate_steps, volume_steps)
        if not self.configured or not normalized_text:
            return False
        cache_key = self._key(normalized_text, settings)
        if self.get(normalized_text, rate_steps=rate_steps, volume_steps=volume_steps):
            return True
        with self._lock:
            existing = self._cache.get(cache_key)
            if existing and os.path.isfile(existing):
                return True
            if cache_key in self._pending:
                return True
            self._pending.add(cache_key)
            revision = int(self._revision)
        self._ensure_worker()
        self._queue.put(
            (
                cache_key,
                normalized_text,
                settings,
                int(rate_steps),
                int(volume_steps),
                self._normalize_persistent_relpath(persistent_relpath),
                revision,
            )
        )
        return True

    def wait(
        self,
        text: object,
        *,
        timeout: float = 45.0,
        rate_steps: int = 0,
        volume_steps: int = 0,
        persistent_relpath: str = "",
    ) -> str | None:
        normalized_text = str(text or "").strip()
        if not normalized_text or not self.configured:
            return None
        settings = self._settings_with_rate_steps(rate_steps, volume_steps)
        existing = self.get(normalized_text, rate_steps=rate_steps, volume_steps=volume_steps)
        if existing:
            return existing
        if not self.prefetch(
            normalized_text,
            rate_steps=rate_steps,
            volume_steps=volume_steps,
            persistent_relpath=persistent_relpath,
        ):
            return None
        deadline = time.monotonic() + max(0.0, float(timeout))
        cache_key = self._key(normalized_text, settings)
        while not self._stop_event.is_set():
            existing = self.get(normalized_text, rate_steps=rate_steps, volume_steps=volume_steps)
            if existing:
                return existing
            with self._lock:
                pending = cache_key in self._pending
            if not pending or time.monotonic() >= deadline:
                return self.get(normalized_text, rate_steps=rate_steps, volume_steps=volume_steps)
            time.sleep(0.02)
        return None

    def synthesize(
        self,
        text: object,
        *,
        rate_steps: int = 0,
        volume_steps: int = 0,
        persistent_relpath: str = "",
    ) -> str | None:
        normalized_text = str(text or "").strip()
        settings = self._settings_with_rate_steps(rate_steps, volume_steps)
        with self._lock:
            request_revision = int(self._revision)
        if not self.configured or not normalized_text:
            if _edge_tts is None:
                self.last_error = "edge-tts 패키지가 설치되어 있지 않습니다."
            return None
        cache_key = self._key(normalized_text, settings)
        existing = self.get(normalized_text, rate_steps=rate_steps, volume_steps=volume_steps)
        if existing:
            return existing
        try:
            path = self._request_audio(
                normalized_text,
                cache_key,
                settings,
                persistent_relpath=self._normalize_persistent_relpath(persistent_relpath),
            )
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self._log(f"edge_tts_request_failed {self.last_error}")
            return None
        with self._lock:
            active_settings = replace(
                self._settings,
                rate=self._settings.rate + int(rate_steps) * 10,
                volume=self._settings.volume + int(volume_steps),
            ).normalized()
            if settings == active_settings:
                cache_is_active = request_revision == self._revision
                if cache_is_active:
                    self._cache[cache_key] = path
                    self.last_error = ""
            else:
                cache_is_active = False
        if cache_is_active:
            try:
                registered = self._register_persistent_path(
                    cache_key,
                    path,
                    normalized_text,
                    settings,
                    int(rate_steps),
                    int(volume_steps),
                    expected_revision=request_revision,
                )
            except OSError as exc:
                # MP3 생성은 완료됐으므로 현재 세션에서는 그대로 사용한다. 다음
                # 캐시 등록 시 메모리의 전체 manifest가 다시 저장되어 자동 복구된다.
                self.last_error = f"manifest_save_failed {type(exc).__name__}: {exc}"
                self._log(self.last_error)
                return path
            if registered:
                return path
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    def _ensure_worker(self) -> None:
        worker = self._worker
        if worker is not None and worker.is_alive():
            return
        with self._lock:
            worker = self._worker
            if worker is not None and worker.is_alive():
                return
            self._stop_event.clear()
            self._worker = threading.Thread(target=self._worker_loop, name="edge-tts-prefetch", daemon=True)
            self._worker.start()

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                job = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if job is None:
                self._queue.task_done()
                break
            cache_key, text, settings, rate_steps, volume_steps, persistent_relpath, revision = job
            try:
                if self.configured and revision == self._revision and cache_key == self._key(text, settings):
                    try:
                        self.synthesize(
                            text,
                            rate_steps=rate_steps,
                            volume_steps=volume_steps,
                            persistent_relpath=persistent_relpath,
                        )
                    except Exception as exc:
                        # 한 파일의 저장 오류가 전체 캐시 작업 스레드를 종료하지 않게 한다.
                        self.last_error = f"{type(exc).__name__}: {exc}"
                        self._log(f"edge_tts_worker_job_failed {self.last_error}")
            finally:
                with self._lock:
                    if revision == self._revision:
                        self._pending.discard(cache_key)
                self._queue.task_done()

    def _ensure_temp_dir(self) -> str:
        with self._lock:
            if self._temp_dir and os.path.isdir(self._temp_dir):
                return self._temp_dir
            self._temp_dir = tempfile.mkdtemp(prefix="boss_timer_edge_tts_")
            return self._temp_dir

    def _request_audio(
        self,
        text: str,
        cache_key: str,
        settings: EdgeTtsSettings,
        *,
        persistent_relpath: str = "",
    ) -> str:
        if _edge_tts is None:
            raise RuntimeError("edge-tts package is unavailable")
        if self._persistent_dir:
            relative_path = persistent_relpath or f"message/{cache_key}.mp3"
            final_path = os.path.abspath(os.path.join(self._persistent_dir, relative_path))
            os.makedirs(os.path.dirname(final_path), exist_ok=True)
        else:
            temp_dir = self._ensure_temp_dir()
            final_path = os.path.join(temp_dir, f"{cache_key}.mp3")
        temp_path = f"{final_path}.{os.getpid()}.{threading.get_ident()}.{time.time_ns()}.tmp"
        selected_voice = str(settings.voice or DEFAULT_EDGE_TTS_VOICE).strip() or DEFAULT_EDGE_TTS_VOICE
        try:
            try:
                os.remove(temp_path)
            except OSError:
                pass
            communicate = _edge_tts.Communicate(
                text,
                selected_voice,
                rate=settings.rate_value,
                volume=settings.volume_value,
                pitch=settings.pitch_value,
                connect_timeout=10,
                receive_timeout=30,
            )
            communicate.save_sync(temp_path)
            if not os.path.isfile(temp_path) or os.path.getsize(temp_path) <= 0:
                raise RuntimeError("empty audio response")
            _replace_file_with_retry(temp_path, final_path)
            self.last_voice = selected_voice
            self._log(f"edge_tts_cached text_length={len(text)} voice={selected_voice}")
            return final_path
        except Exception as exc:
            self._log(
                f"edge_tts_voice_failed voice={selected_voice} error={type(exc).__name__}: {exc}"
            )
            raise

    def clear(self, *, persistent: bool = False) -> int:
        with self._lock:
            self._revision += 1
            self._cache.clear()
            self._pending.clear()
            temp_dir = self._temp_dir
            self._temp_dir = ""
            self.last_voice = ""
            persistent_dir = self._persistent_dir
            if persistent:
                self._manifest_entries.clear()
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        removed_count = 0
        if persistent and persistent_dir:
            try:
                for _root, _dirs, filenames in os.walk(persistent_dir):
                    removed_count += len(filenames)
                shutil.rmtree(persistent_dir, ignore_errors=True)
                os.makedirs(persistent_dir, exist_ok=True)
            except OSError:
                pass
        self.last_clear_removed_count = int(removed_count)
        return int(removed_count)

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=0.5)
        self._worker = None
        self.clear()
