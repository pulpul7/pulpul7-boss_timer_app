# -*- coding: utf-8 -*-
"""Discord voice announcer for BossTimer schedules.

The GUI starts this process separately and checks its local status port.
Secrets are read from the per-user AppData config, never from source code.
"""

from __future__ import annotations

import asyncio
import configparser
import hashlib
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tempfile
import uuid
import warnings
import wave
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from voice_bridge_receipts import observe_audio_source
from discord_notice_output import DiscordNoticeOutput
from discord_connection_policy import ConnectionPolicy, MAX_RETRIES, CONTROL_PROTOCOL, is_access_error, ACCESS_ERROR_TEXT, _read_state_text
from discord_query_routing import QueryLedger

try:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        import audioop
except ImportError:
    audioop = None


DEFAULT_STATUS_PORT = 18765
VOICE_BRIDGE_POLL_INTERVAL_SEC = 0.05
VOICE_CLIP_GAP_SEC = 0.02
DISCORD_PCM_FRAME_BYTES = 3840
DISCORD_PCM_BYTES_PER_SECOND = 48000 * 2 * 2
DISCORD_COUNTDOWN_COMPOSITE_OUTPUT_LEAD_MS = 250
DISCORD_COUNTDOWN_COMPOSITE_IDLE_KEEPALIVE_SEC = 10.0
DISCORD_COUNTDOWN_COMPOSITE_WARMUP_SEC = 1.2
QUERY_ROUTING_WAIT_TEXT = "조회 담당자의 응답을 기다리고 있습니다."
# Edge TTS 원본은 녹음 WAV보다 체감 음량이 작다. 모든 Edge 캐시를 기존
# 10/11초 보정 수준으로 재생해 초읽기와 연쇄 안내의 음량이 바뀌지 않게 한다.
EDGE_TTS_PLAYBACK_GAIN = 1.80
# Recorded invasion sequences are assembled as a timed PCM stream.  The GUI
# intentionally sends a colliding right-lane invasion at 0.8 volume; cancel
# that attenuation here and give standalone Discord playback the small gain
# needed to match the same WAV files played by the local WPF host.
DISCORD_INVASION_SEQUENCE_GAIN = 1.25
TIMED_REPLACE_CURRENT_AUDIO_PHASES = {
    "COUNTDOWN_SEQUENCE",
    # 10초 이내 초확정 젠은 로컬과 동일하게 차임벨 한 번 뒤
    # "보스 젠, 보스 젠"으로 이어진다. 일반 순차 재생으로 기다리면
    # 앞 클립 길이만큼 뒤 보스가 밀리므로 예약 시각에 바로 전환한다.
    "SPAWN_CONFIRMED_NEAR_SEQUENCE",
}
DEFAULT_CHIME_PATHS = {
    "general": "wave/안내방송_비행기1.wav",
    "fixed": "wave/안내방송_비행기3.wav",
    "rapid_chain": "wave/안내방송_비행기2.wav",
}
WEEKDAY_LABELS = ("월", "화", "수", "목", "금", "토", "일")


def get_app_root() -> Path:
    env_root = os.environ.get("BOSS_TIMER_APP_ROOT")
    if env_root:
        return Path(env_root).resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_resource_root() -> Path:
    """Return the packaged resource directory without losing the writable app root."""
    env_root = os.environ.get("BOSS_TIMER_RESOURCE_ROOT")
    if env_root:
        return Path(env_root).resolve()
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()
    return Path(__file__).resolve().parent


def get_user_config_dir() -> Path:
    base_dir = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base_dir) / "BossTimer"


APP_ROOT = get_app_root()
RESOURCE_ROOT = get_resource_root()
CONFIG_PATH = Path(os.environ.get("BOSS_TIMER_DISCORD_CONFIG") or get_user_config_dir() / "discord_bot.ini")
VOICE_BRIDGE_PATH = Path(os.environ.get("BOSS_TIMER_DISCORD_VOICE_QUEUE") or get_user_config_dir() / "discord_voice_queue.jsonl")
# The GUI keeps schedule data per game-server profile.  The bot is launched as
# a separate process, so it must receive those resolved paths explicitly rather
# than falling back to the installation directory.
SCHEDULE_STATE_PATH = Path(os.environ.get("BOSS_TIMER_SCHEDULE_STATE") or APP_ROOT / "schedule_state.json")
ALARM_SETTINGS_PATH = Path(os.environ.get("BOSS_TIMER_SCHEDULE_ALARM_SETTINGS") or APP_ROOT / "schedule_alarm_settings.json")
FIXED_BOSSES_PATH = Path(os.environ.get("BOSS_TIMER_SCHEDULE_FIXED_BOSSES") or APP_ROOT / "init" / "schedule_fixed_bosses.txt")
SCHEDULE_BOSS_DEFINITIONS_PATH = Path(
    os.environ.get("BOSS_TIMER_SCHEDULE_BOSS_DEFINITIONS") or APP_ROOT / "init" / "schedule_boss_definitions.txt"
)
SCHEDULE_SHARE_LATEST_IMAGE_PATH = APP_ROOT / "schedule_share_latest.png"
SCHEDULE_SHARE_DISCORD_IMAGE_REQUEST_PATH = APP_ROOT / "schedule_share_discord_request.json"
SCHEDULE_SHARE_DISCORD_IMAGE_RESPONSE_PREFIX = "schedule_share_discord_response_"
DISCORD_SCHEDULE_REQUEST_DIR = APP_ROOT / "discord_schedule_requests"
DISCORD_VOICE_COMMANDS_PATH = Path(
    os.environ.get("BOSS_TIMER_DISCORD_VOICE_COMMANDS")
    or get_user_config_dir() / "discord_voice_commands.json"
)
LOG_PATH = get_user_config_dir() / "discord_bot.log"
STATUS_PORT = int(os.environ.get("BOSS_TIMER_DISCORD_STATUS_PORT") or DEFAULT_STATUS_PORT)

BUILTIN_DISCORD_VOICE_COMMANDS = {
    "광역체크": "광역 체크해주세요.",
    "집결지": "집결지 모여주세요.",
    "웃음": "하하하..",
}
DISCORD_VOICE_COMMAND_MAX_LENGTH = 40
# A Discord message has at most five component rows.  Reserve the last row
# for previous/next controls so the soundboard can always be paged.
DISCORD_VOICE_COMMAND_MENU_PAGE_SIZE = 20
DISCORD_VOICE_CHANNEL_MENU_PAGE_SIZE = 25
DISCORD_VOICE_COMMAND_INVALID_CHARS = frozenset('/\\:*?"<>|')


def normalize_discord_voice_command_name(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).casefold()


def validate_discord_voice_command_name(value: object) -> tuple[bool, str]:
    name = str(value or "").strip()
    if not name:
        return False, "음성 명령 이름을 입력하세요."
    if len(name) > DISCORD_VOICE_COMMAND_MAX_LENGTH:
        return False, f"음성 명령은 {DISCORD_VOICE_COMMAND_MAX_LENGTH}자 이하로 입력하세요."
    if name.startswith("/") or any(ch in DISCORD_VOICE_COMMAND_INVALID_CHARS for ch in name):
        return False, "음성 명령 이름에 / \\ : * ? \" < > | 문자는 사용할 수 없습니다."
    if any(ord(ch) < 32 for ch in name):
        return False, "음성 명령 이름에 줄바꿈이나 제어 문자를 사용할 수 없습니다."
    return True, re.sub(r"\s+", " ", name)


def parse_discord_voice_command_addition(
    value: object,
    *,
    allow_empty_tts: bool = False,
) -> tuple[bool, str, str]:
    raw_value = str(value or "").strip()
    split_parts = re.split(r"\s*[,，]\s*", raw_value, maxsplit=1)
    command_part = split_parts[0] if split_parts else ""
    tts_part = split_parts[1] if len(split_parts) == 2 else ""
    valid, command_name_or_error = validate_discord_voice_command_name(command_part)
    if not valid:
        return False, command_name_or_error, ""
    has_tts_separator = len(split_parts) == 2
    tts_source = tts_part if (allow_empty_tts and has_tts_separator) else (tts_part or command_name_or_error)
    tts_text = re.sub(r"\s+", " ", str(tts_source or "").strip())
    if not tts_text:
        if allow_empty_tts and has_tts_separator:
            return True, command_name_or_error, ""
        return False, "읽을 TTS 문장을 입력하세요.", ""
    if len(tts_text) > 200 or any(ord(ch) < 32 for ch in tts_text):
        return False, "TTS 문장은 200자 이하의 한 줄로 입력하세요.", ""
    return True, command_name_or_error, tts_text


def load_custom_discord_voice_commands(
    path: Path = DISCORD_VOICE_COMMANDS_PATH,
) -> dict[str, tuple[str, str]]:
    payload = load_json(path)
    raw_names = payload.get("commands") if isinstance(payload, dict) else None
    if not isinstance(raw_names, list):
        return {}
    commands: dict[str, tuple[str, str]] = {}
    for raw_entry in raw_names:
        if isinstance(raw_entry, dict):
            raw_name = raw_entry.get("name")
            raw_tts_text = raw_entry.get("tts_text")
        else:
            raw_name = raw_entry
            raw_tts_text = raw_entry
        raw_name_text = str(raw_name or "").strip()
        raw_tts_text_value = str(raw_tts_text or "").strip()
        if isinstance(raw_entry, dict) and "tts_text" in raw_entry:
            parse_value = f"{raw_name_text}, {raw_tts_text_value}"
            valid, name_or_error, tts_text = parse_discord_voice_command_addition(
                parse_value,
                allow_empty_tts=True,
            )
        else:
            valid, name_or_error, tts_text = parse_discord_voice_command_addition(raw_name_text)
        if not valid:
            continue
        normalized = normalize_discord_voice_command_name(name_or_error)
        # Keep built-in names as explicit overrides as well.  Previously they
        # were discarded during load, so a replacement vanished after restart.
        if normalized:
            commands[normalized] = (name_or_error, tts_text)
    return commands


def load_disabled_builtin_discord_voice_commands(
    path: Path = DISCORD_VOICE_COMMANDS_PATH,
) -> set[str]:
    """Load server-scoped built-in commands hidden by /음성삭제."""
    payload = load_json(path)
    raw_names = payload.get("disabled_builtin_commands") if isinstance(payload, dict) else None
    if not isinstance(raw_names, list):
        return set()
    builtin_keys = {
        normalize_discord_voice_command_name(command_name)
        for command_name in BUILTIN_DISCORD_VOICE_COMMANDS
    }
    return {
        normalized
        for normalized in (normalize_discord_voice_command_name(raw_name) for raw_name in raw_names)
        if normalized in builtin_keys
    }


def save_custom_discord_voice_commands(
    commands: dict[str, tuple[str, str]],
    path: Path = DISCORD_VOICE_COMMANDS_PATH,
    *,
    disabled_builtin_commands: set[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    serializable_commands: set[tuple[str, str]] = set()
    for command_data in commands.values():
        if isinstance(command_data, tuple) and len(command_data) == 2:
            name, tts_text = command_data
        else:
            name = command_data
            tts_text = command_data
        valid, name_or_error, parsed_tts_text = parse_discord_voice_command_addition(
            f"{name}, {tts_text}" if str(tts_text or "").strip() != str(name or "").strip() else name,
            allow_empty_tts=not bool(str(tts_text or "").strip()),
        )
        if valid:
            serializable_commands.add((name_or_error, parsed_tts_text))
    builtin_names_by_key = {
        normalize_discord_voice_command_name(command_name): command_name
        for command_name in BUILTIN_DISCORD_VOICE_COMMANDS
    }
    disabled_builtin_names = sorted(
        {
            builtin_names_by_key[normalized]
            for normalized in (disabled_builtin_commands or set())
            if normalized in builtin_names_by_key
        },
        key=str.casefold,
    )
    payload = {
        "version": 3,
        "commands": [
            {"name": name, "tts_text": tts_text}
            for name, tts_text in sorted(
                serializable_commands,
                key=lambda item: item[0].casefold(),
            )
        ],
        "disabled_builtin_commands": disabled_builtin_names,
    }
    try:
        temporary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def parse_discord_schedule_message(content: str) -> dict[str, Any] | None:
    """Recognize only schedule-edit messages; leave ordinary channel chat alone."""
    cleaned = str(content or "").strip()
    if not cleaned or cleaned.startswith("/"):
        return None
    delete_match = re.fullmatch(r"삭제\s+(.+)", cleaned, flags=re.DOTALL)
    if delete_match:
        raw_names = re.sub(r"[\r\n\t]+", " ", delete_match.group(1)).strip()
        names = [name.strip() for name in raw_names.split(",") if name.strip()]
        if not names:
            return None
        return {"operation": "delete", "boss_names": names, "raw_text": cleaned}

    source_lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not source_lines:
        return None
    clock_and_boss_pattern = re.compile(
        r"(?P<clock>(?:\d{4}|\d{6}|\d+:\d{2}(?::\d{2})?)(?:\.\d{1,6})?)\s+(?P<bosses>.+)$"
    )
    lines: list[str] = []
    for source_line in source_lines:
        line_match = clock_and_boss_pattern.fullmatch(source_line)
        if line_match is None:
            return None
        clock_text = str(line_match.group("clock") or "").strip()
        bosses_text = str(line_match.group("bosses") or "").strip()
        common_suffix = ""
        cut_match = re.fullmatch(r"(?P<bosses>.+?)\s+컷", bosses_text)
        if cut_match is not None:
            bosses_text = str(cut_match.group("bosses") or "").strip()
            common_suffix = " 컷"
        boss_names = [name.strip() for name in re.split(r"[,，]", bosses_text) if name.strip()]
        if not boss_names:
            return None
        lines.extend(f"{clock_text} {boss_name}{common_suffix}" for boss_name in boss_names)
    schedule_pattern = re.compile(
        r"(?:\d{4}|\d{6}|\d+:\d{2}(?::\d{2})?)(?:\.\d{1,6})?\s+.+?(?:\s+컷)?$"
    )
    if any(schedule_pattern.fullmatch(line) is None for line in lines):
        return None
    return {"operation": "apply", "raw_text": "\n".join(lines), "line_count": len(lines)}


def log(message: str) -> None:
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > 1024 * 1024:
            LOG_PATH.write_text("", encoding="utf-8")
        with LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")
    except OSError:
        pass


def deserialize(value: Any) -> Any:
    if isinstance(value, list):
        return [deserialize(item) for item in value]
    if isinstance(value, dict):
        if value.get("__type__") == "datetime":
            try:
                return datetime.fromisoformat(str(value.get("value") or ""))
            except ValueError:
                return None
        if value.get("__type__") == "tuple":
            return tuple(deserialize(item) for item in value.get("items", []) if isinstance(value.get("items", []), list))
        return {str(key): deserialize(item) for key, item in value.items()}
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def load_config() -> dict[str, str]:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(CONFIG_PATH, encoding="utf-8")
    except (OSError, configparser.Error):
        return {}
    if not parser.has_section("discord_bot"):
        return {}
    section = parser["discord_bot"]
    application_id = "".join(ch for ch in str(section.get("application_id", "") or "") if ch.isdigit())
    invite_url = str(section.get("invite_url", "") or "").strip()
    if not invite_url and application_id:
        invite_url = f"https://discord.com/oauth2/authorize?client_id={application_id}"
    return {
        "bot_token": "".join(str(section.get("bot_token", "") or "").split()),
        "application_id": application_id,
        "server_id": str(section.get("server_id", "") or "").strip(),
        "voice_channel_id": str(section.get("voice_channel_id", "") or "").strip(),
        "default_voice_channel_id": str(section.get("default_voice_channel_id", section.get("voice_channel_id", "")) or "").strip(),
        "text_channel_id": str(section.get("text_channel_id", "") or "").strip(),
        "authority_control_channel_id": str(section.get("text_channel_id", "") or "").strip(),
        "voice_panel_channel_id": str(section.get("voice_panel_channel_id", "") or "").strip(),
        "voice_panel_message_id": str(section.get("voice_panel_message_id", "") or "").strip(),
        "text_channel_keep_count": str(section.get("text_channel_keep_count", "2")).strip(),
        "invite_url": invite_url,
        "voice_bridge_enabled": str(section.get("voice_bridge_enabled", "1") or "1").strip(),
    }


def config_bool(config: dict[str, Any], key: str, default: bool = False) -> bool:
    value = config.get(key)
    if isinstance(value, bool):
        return value
    text = str(value if value is not None else "").strip().casefold()
    if not text:
        return bool(default)
    return text in {"1", "true", "yes", "on", "y"}


def save_config_value(key: str, value: str) -> bool:
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read(CONFIG_PATH, encoding="utf-8")
    except (OSError, configparser.Error):
        pass
    if not parser.has_section("discord_bot"):
        parser["discord_bot"] = {}
    parser["discord_bot"][key] = str(value or "").strip()
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as file:
            parser.write(file)
    except OSError as exc:
        log(f"config_save_failed key={key} error={exc}")
        return False
    return True


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def resolve_clip(relative_or_absolute: str) -> str:
    raw_path = str(relative_or_absolute or "").strip()
    if not raw_path:
        return ""
    candidates = [Path(raw_path)]
    relative_path = raw_path.replace("/", os.sep)
    candidates.append(RESOURCE_ROOT / relative_path)
    candidates.append(APP_ROOT / relative_path)
    for candidate in candidates:
        try:
            resolved = candidate if candidate.is_absolute() else candidate.resolve()
        except OSError:
            continue
        if resolved.is_file():
            return str(resolved)
    return ""


def voice_file(subdir: str, stem: str) -> str:
    wanted = str(stem or "").strip()
    for root_dir in (RESOURCE_ROOT, APP_ROOT):
        target_dir = root_dir / "voice" / subdir
        if not target_dir.is_dir():
            continue
        for extension in (".wav", ".mp3", ".m4a", ".aac", ".mp4"):
            path = target_dir / f"{wanted}{extension}"
            if path.is_file():
                return str(path)
    return ""


def chime_file(kind: str, alarm_settings: dict[str, Any], *, countdown: bool = False) -> str:
    chime_settings = alarm_settings.get("chime_settings")
    if isinstance(chime_settings, dict):
        if countdown and bool(chime_settings.get("skip_countdown", True)):
            return ""
        configured = resolve_clip(str(chime_settings.get(kind) or ""))
        if configured:
            return configured
    return resolve_clip(DEFAULT_CHIME_PATHS.get(kind, DEFAULT_CHIME_PATHS["general"]))


def build_offset_clip(offset_seconds: int) -> str:
    seconds = max(0, int(offset_seconds))
    if seconds >= 60 and seconds % 60 == 0:
        return voice_file("min", f"{seconds // 60}min01")
    if seconds > 0:
        return voice_file("sec", str(seconds))
    return ""


def is_second_confirmed(item: dict[str, Any]) -> bool:
    if parse_datetime(item.get("second_precision_origin_at")) is not None:
        return True
    scheduled_at = parse_datetime(item.get("scheduled_at"))
    return bool(scheduled_at and (scheduled_at.second != 0 or scheduled_at.microsecond != 0))


def event_name(item: dict[str, Any]) -> str:
    return str(item.get("display_name") or item.get("boss_name") or item.get("raw_name") or "").strip()


def compact_alert_names(names: tuple[str, ...]) -> tuple[str, int, bool]:
    """Return one spoken representative, extra member count, and all-invasion flag."""
    unique_names = list(dict.fromkeys(str(name or "").strip() for name in names if str(name or "").strip()))
    if not unique_names:
        return "", 0, False
    is_invasion = lambda name: bool(re.match(r"^\s*침공\s*", name))
    primary_name = next((name for name in unique_names if not is_invasion(name)), unique_names[0])
    all_invasion = all(is_invasion(name) for name in unique_names)
    primary_name = re.sub(r"^\s*침공\s*", "", primary_name).strip()
    return primary_name, max(0, len(unique_names) - 1), all_invasion


@dataclass(frozen=True)
class AlertJob:
    due_at: datetime
    target_at: datetime
    offset_seconds: int
    phase: str
    kind: str
    names: tuple[str, ...]
    second_confirmed: bool = False
    invasion: bool = False

    @property
    def key(self) -> str:
        name_key = "|".join(self.names)
        return f"{self.phase}:{self.kind}:{self.target_at.isoformat()}:{self.offset_seconds}:{name_key}"


@dataclass(frozen=True)
class VoiceBridgeJob:
    id: str
    created_at: datetime
    phase: str
    category: str
    lane: str
    volume: float
    clip_paths: tuple[str, ...]
    timed_clips: tuple[tuple[datetime, str], ...] = ()
    fallback_text: str = ""
    scope_id: str = ""
    target_time: str = ""
    offset_sec: int = 0
    notice_group_id: str = ""

    @property
    def key(self) -> str:
        return f"VOICE_BRIDGE:{self.id}"


@dataclass(frozen=True)
class VoiceBridgeControl:
    id: str
    created_at: datetime
    action: str
    scope_id: str = ""
    message: str = ""


class VoiceBridgeReader:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = self._current_file_size()

    def _current_file_size(self) -> int:
        try:
            return int(self.path.stat().st_size)
        except OSError:
            return 0

    def read_new_jobs(self) -> list[VoiceBridgeJob | VoiceBridgeControl]:
        try:
            size = int(self.path.stat().st_size)
        except OSError:
            self.offset = 0
            STATUS.update(voice_bridge_offset=0)
            return []
        if size < self.offset:
            self.offset = size
            STATUS.update(voice_bridge_offset=self.offset)
            return []
        if size == self.offset:
            return []
        jobs: list[VoiceBridgeJob | VoiceBridgeControl] = []
        try:
            with self.path.open("r", encoding="utf-8") as file:
                file.seek(self.offset)
                while True:
                    line = file.readline()
                    if not line:
                        break
                    self.offset = file.tell()
                    job = self._parse_job(line)
                    if job is not None:
                        jobs.append(job)
        except OSError as exc:
            log(f"voice_bridge_read_failed error={exc}")
        STATUS.update(voice_bridge_offset=self.offset)
        return jobs

    def _parse_job(self, line: str) -> VoiceBridgeJob | VoiceBridgeControl | None:
        try:
            payload = json.loads(line)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        created_at = parse_datetime(payload.get("created_at")) or datetime.now()
        job_id = str(payload.get("id") or "").strip() or f"{int(time.time() * 1000)}:{self.offset}"
        scope_id = str(payload.get("scope_id") or "").strip()
        action = str(payload.get("action") or "").strip().casefold()
        if action == "cancel_scope" and scope_id:
            return VoiceBridgeControl(
                id=job_id,
                created_at=created_at,
                action=action,
                scope_id=scope_id,
            )
        if action == "heartbeat":
            return VoiceBridgeControl(
                id=job_id,
                created_at=created_at,
                action=action,
            )
        if action == "text_notice":
            message = str(payload.get("message") or "").strip()
            if message:
                return VoiceBridgeControl(
                    id=job_id,
                    created_at=created_at,
                    action=action,
                    message=message,
                )
        raw_paths = payload.get("clip_paths")
        if not isinstance(raw_paths, list):
            raw_paths = []
        clip_paths = tuple(
            str(path).strip()
            for path in raw_paths
            if str(path).strip() and Path(str(path).strip()).is_file()
        )
        timed_clips: list[tuple[datetime, str]] = []
        raw_timed_clips = payload.get("timed_clips")
        if isinstance(raw_timed_clips, list):
            for entry in raw_timed_clips:
                if not isinstance(entry, dict):
                    continue
                play_at = parse_datetime(entry.get("play_at"))
                clip_path = str(entry.get("path") or "").strip()
                if play_at is None or not clip_path or not Path(clip_path).is_file():
                    continue
                timed_clips.append((play_at, clip_path))
        timed_clips.sort(key=lambda item: item[0])
        if not clip_paths and not timed_clips:
            return None
        try:
            volume = max(0.0, min(1.0, float(payload.get("volume", 1.0))))
        except (TypeError, ValueError):
            volume = 1.0
        try:
            offset_sec = max(0, min(86400, int(payload.get('offset_sec') or 0)))
        except (TypeError, ValueError):
            offset_sec = 0
        return VoiceBridgeJob(
            id=job_id,
            created_at=created_at,
            phase=str(payload.get("phase") or "AUDIO").strip() or "AUDIO",
            category=str(payload.get("category") or "general").strip() or "general",
            lane=str(payload.get("lane") or "center").strip() or "center",
            volume=volume,
            clip_paths=clip_paths,
            timed_clips=tuple(timed_clips),
            fallback_text=str(payload.get("fallback_text") or "").strip(),
            scope_id=scope_id,
            target_time=str(payload.get('target_time') or ''),
            offset_sec=offset_sec,
            notice_group_id=str(payload.get('notice_group_id') or '').strip(),
        )


class BotStatus:
    def __init__(self) -> None:
        self.started_at = datetime.now()
        self.pid = os.getpid()
        self.online = False
        self.connected_at = 0.0
        self.guild_id = ""
        self.voice_channel_id = ""
        self.voice_connected = False
        self.voice_disconnect_confirmed = False
        self.voice_authority_generation = 0
        self.last_error = ""
        self.configuration_error = ""
        self.voice_error = ""
        self.voice_reconnect_attempts = 0
        self.standby = True
        self.runtime_id = uuid.uuid4().hex
        self.client_id = ""
        self.application_id = ""
        self.bot_user_id = ""
        self.send_authorized = False
        self.authority_joining = False
        self.handover_hold = False
        self.reconnect_attempts = 0
        self.connection_control = True
        self.last_schedule_mtime = 0.0
        self.last_played = ""
        self.voice_bridge_enabled = True
        self.voice_bridge_timed_clips = True
        self.voice_bridge_offset = 0
        self.voice_bridge_last_id = ""
        self.voice_bridge_last_heartbeat_id = ""
        self.voice_bridge_receipts: list[dict[str, str]] = []
        self.notice_opportunities = []
        self.text_commands_enabled = True
        self.nacl_available = False
        self.nacl_import_error = ""
        self.shutdown_requested = threading.Event()
        self.lock = threading.Lock()

    def as_payload(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": True,
                "pid": self.pid,
                "control_protocol": CONTROL_PROTOCOL,
                "runtime_id": self.runtime_id,
                "client_id": self.client_id,
                "application_id": self.application_id,
                "bot_user_id": self.bot_user_id,
                "config_path": str(CONFIG_PATH.resolve()),
                "send_authorized": self.send_authorized,
                "authority_joining": self.authority_joining,
                "online": self.online,
                "connected_at": self.connected_at,
                "guild_id": self.guild_id,
                "voice_channel_id": self.voice_channel_id,
                "voice_connected": self.voice_connected,
                "voice_disconnect_confirmed": self.voice_disconnect_confirmed,
                "voice_authority_generation": self.voice_authority_generation,
                "last_error": self.last_error,
                "configuration_error": self.configuration_error,
                "voice_error": self.voice_error,
                "voice_reconnect_attempts": self.voice_reconnect_attempts,
                "standby": self.standby,
                "handover_hold": self.handover_hold,
                "reconnect_attempts": self.reconnect_attempts,
                "reconnect_limit": MAX_RETRIES,
                "connection_control": self.connection_control,
                "last_schedule_mtime": self.last_schedule_mtime,
                "last_played": self.last_played,
                "voice_bridge_enabled": self.voice_bridge_enabled,
                "voice_bridge_timed_clips": self.voice_bridge_timed_clips,
                "voice_bridge_offset": self.voice_bridge_offset,
                "voice_bridge_last_id": self.voice_bridge_last_id,
                "voice_bridge_last_heartbeat_id": self.voice_bridge_last_heartbeat_id,
                "voice_bridge_receipt_protocol": 1,
                "voice_bridge_receipt_modes": ["sequential"],
                "voice_bridge_receipts": [dict(row) for row in self.voice_bridge_receipts],
                "notice_opportunities": [dict(row) for row in self.notice_opportunities],
                "text_commands_enabled": self.text_commands_enabled,
                "nacl_available": self.nacl_available,
                "nacl_import_error": self.nacl_import_error,
                "shutdown_requested": self.shutdown_requested.is_set(),
                "started_at": self.started_at.isoformat(timespec="seconds"),
            }

    def update(self, **kwargs: Any) -> None:
        with self.lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def record_bridge_result(self, job_id: str, state: str, scope_id: str = "") -> None:
        if state not in {"started", "completed", "cancelled", "failed", "timed_untracked"}:
            raise ValueError("invalid bridge receipt state")
        with self.lock:
            rows = [row for row in self.voice_bridge_receipts if row["id"] != job_id]
            rows.append({"id": job_id, "state": state, "scope_id": scope_id,
                         "at": datetime.now().isoformat(timespec="milliseconds")})
            self.voice_bridge_receipts = rows[-128:]

    def record_notice_opportunity(self, job):
        if (job.scope_id or job.offset_sec != 60 or not job.target_time
                or job.phase not in {'PRE_ALERT', 'FIXED_PRE_ALERT', 'PRE_ALERT_SEQUENCE', 'FIXED_PRE_ALERT_SEQUENCE'}):
            return
        with self.lock:
            rows = [row for row in self.notice_opportunities if row['id'] != job.id]
            rows.append(dict(id=job.id, phase=job.phase, scheduled_at=job.target_time,
                             created_at=job.created_at.isoformat(), text=job.fallback_text,
                             at=datetime.now().isoformat(timespec='milliseconds')))
            self.notice_opportunities = rows[-32:]


STATUS = BotStatus()


def force_process_exit_after_shutdown(delay_seconds: float = 1.25) -> None:
    def worker() -> None:
        time.sleep(max(0.5, float(delay_seconds)))
        if STATUS.shutdown_requested.is_set():
            log("shutdown_force_exit")
            os._exit(0)

    threading.Thread(target=worker, daemon=True).start()


class StatusHandler(BaseHTTPRequestHandler):
    notice_bot = None

    def do_POST(self) -> None:
        import hmac
        from notice_output_bridge import handle_notice_command
        bot = self.notice_bot
        secret = os.environ.get('BOSS_TIMER_NOTICE_SECRET', '')
        if (self.path not in {'/notice', '/authority'} or not secret or bot is None
                or not hmac.compare_digest(self.headers.get('X-Notice-Secret', ''), secret)):
            self.send_error(403)
            return
        future = None
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 16384:
                raise ValueError('invalid request size')
            self.connection.settimeout(2)
            command = json.loads(self.rfile.read(length))
            handler = bot.authority_events.command(command) if self.path == '/authority' else handle_notice_command(bot, command, STATUS)
            future = asyncio.run_coroutine_threadsafe(handler, bot.client.loop)
            self._send_json(future.result(timeout=6 if self.path == '/authority' else 3))
        except Exception as exc:
            if future is not None:
                future.cancel()
            if self.path == '/authority':
                self._send_json(dict(ok=False, pid=STATUS.pid, runtime_id=STATUS.runtime_id,
                                     id=locals().get('command', {}).get('id'), error=str(exc)))
            else:
                self.send_error(409, 'Notice command rejected')

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _send_json(self, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path.startswith("/shutdown"):
            STATUS.shutdown_requested.set()
            force_process_exit_after_shutdown()
            self._send_json({"ok": True, "shutdown": True})
            return
        self._send_json(STATUS.as_payload())


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR permits competing listeners on the same port.
    # The status listener also gates startup before any Discord login.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def start_status_server() -> ThreadingHTTPServer | None:
    try:
        server = ReusableThreadingHTTPServer(("127.0.0.1", STATUS_PORT), StatusHandler)
    except OSError as exc:
        log(f"status_port_bind_failed port={STATUS_PORT} error={exc}")
        STATUS.update(last_error=f"상태 포트를 열 수 없습니다: {exc}")
        return None
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class ScheduleReader:
    def __init__(self) -> None:
        self.schedule_mtime = 0.0
        self.alarm_mtime = 0.0
        self.fixed_mtime = 0.0
        self.jobs: list[AlertJob] = []

    def maybe_reload(self) -> None:
        schedule_mtime = self._mtime(SCHEDULE_STATE_PATH)
        alarm_mtime = self._mtime(ALARM_SETTINGS_PATH)
        fixed_mtime = self._mtime(FIXED_BOSSES_PATH)
        if (schedule_mtime, alarm_mtime, fixed_mtime) == (self.schedule_mtime, self.alarm_mtime, self.fixed_mtime):
            return
        self.schedule_mtime = schedule_mtime
        self.alarm_mtime = alarm_mtime
        self.fixed_mtime = fixed_mtime
        self.jobs = self._build_jobs()
        STATUS.update(last_schedule_mtime=schedule_mtime)
        log(f"schedule_reloaded jobs={len(self.jobs)}")

    def due_jobs(self, now: datetime, announced: set[str]) -> list[AlertJob]:
        self.maybe_reload()
        due: list[AlertJob] = []
        for job in self.jobs:
            if job.key in announced:
                continue
            delta = (now - job.due_at).total_seconds()
            if -0.25 <= delta <= 2.5:
                due.append(job)
        return sorted(due, key=lambda item: (item.due_at, item.target_at, item.phase))

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def _build_jobs(self) -> list[AlertJob]:
        alarm_settings = load_json(ALARM_SETTINGS_PATH)
        payload = deserialize(load_json(SCHEDULE_STATE_PATH))
        if not isinstance(payload, dict):
            payload = {}
        jobs = self._build_schedule_event_jobs(payload, alarm_settings)
        jobs.extend(self._build_fixed_boss_jobs(alarm_settings))
        jobs.sort(key=lambda item: (item.due_at, item.target_at, item.phase))
        return jobs

    def _build_schedule_event_jobs(self, payload: dict[str, Any], alarm_settings: dict[str, Any]) -> list[AlertJob]:
        raw_events = payload.get("schedule_events")
        if not isinstance(raw_events, list):
            return []
        common_offsets = self._normalize_offsets(alarm_settings.get("common_offsets") or [300, 60])
        boss_overrides = alarm_settings.get("boss_overrides") if isinstance(alarm_settings.get("boss_overrides"), dict) else {}
        grouped: dict[datetime, list[dict[str, Any]]] = {}
        now = datetime.now()
        window_start = now - timedelta(seconds=10)
        window_end = now + timedelta(days=8)
        for raw_item in raw_events:
            if not isinstance(raw_item, dict):
                continue
            scheduled_at = parse_datetime(raw_item.get("scheduled_at"))
            if scheduled_at is None or scheduled_at < window_start or scheduled_at > window_end:
                continue
            if str(raw_item.get("state") or "scheduled") not in {"scheduled", ""}:
                continue
            name = event_name(raw_item)
            if not name:
                continue
            grouped.setdefault(scheduled_at, []).append(raw_item)
        jobs: list[AlertJob] = []
        for scheduled_at, items in grouped.items():
            names = tuple(event_name(item) for item in items if event_name(item))
            if not names:
                continue
            enabled_items = []
            merged_offsets: set[int] = set()
            for item in items:
                name = event_name(item)
                override = boss_overrides.get(name) if isinstance(boss_overrides, dict) else None
                if isinstance(override, dict):
                    if not bool(override.get("enabled", True)):
                        continue
                    offsets = self._normalize_offsets(override.get("offsets") or common_offsets)
                else:
                    offsets = common_offsets
                enabled_items.append(item)
                merged_offsets.update(offsets)
            if not enabled_items:
                continue
            invasion = any(bool(item.get("is_invasion")) for item in enabled_items)
            second_confirmed = any(is_second_confirmed(item) for item in enabled_items)
            for offset in sorted(merged_offsets, reverse=True):
                if offset <= 0:
                    continue
                jobs.append(AlertJob(
                    due_at=scheduled_at - timedelta(seconds=offset),
                    target_at=scheduled_at,
                    offset_seconds=offset,
                    phase="PRE_ALERT",
                    kind="general",
                    names=names,
                    second_confirmed=second_confirmed,
                    invasion=invasion,
                ))
            jobs.append(AlertJob(
                due_at=scheduled_at,
                target_at=scheduled_at,
                offset_seconds=0,
                phase="SPAWN",
                kind="general",
                names=names,
                second_confirmed=second_confirmed,
                invasion=invasion,
            ))
        return jobs

    def _build_fixed_boss_jobs(self, alarm_settings: dict[str, Any]) -> list[AlertJob]:
        if not bool(alarm_settings.get("fixed_boss_enabled", True)):
            return []
        overrides = alarm_settings.get("fixed_boss_overrides") if isinstance(alarm_settings.get("fixed_boss_overrides"), dict) else {}
        skip_due_time = bool(alarm_settings.get("fixed_boss_skip_due_time", True))
        now = datetime.now()
        dates = [now.date(), (now + timedelta(days=1)).date()]
        jobs: list[AlertJob] = []
        for entry in self._load_fixed_boss_entries():
            name = entry.get("boss_name", "")
            if not name:
                continue
            override = overrides.get(name) if isinstance(overrides, dict) else None
            if isinstance(override, dict):
                if not bool(override.get("enabled", True)):
                    continue
                offsets = self._normalize_offsets(override.get("offsets") or [60])
            else:
                offsets = [60]
            for target_date in dates:
                if not self._fixed_boss_matches_date(entry, target_date):
                    continue
                scheduled_at = datetime.combine(target_date, entry["time_value"])
                if scheduled_at < now - timedelta(seconds=10) or scheduled_at > now + timedelta(days=1, minutes=5):
                    continue
                for offset in sorted(offsets, reverse=True):
                    if offset <= 0:
                        continue
                    jobs.append(AlertJob(
                        due_at=scheduled_at - timedelta(seconds=offset),
                        target_at=scheduled_at,
                        offset_seconds=offset,
                        phase="FIXED_PRE_ALERT",
                        kind="fixed",
                        names=(name,),
                    ))
                if not skip_due_time:
                    jobs.append(AlertJob(
                        due_at=scheduled_at,
                        target_at=scheduled_at,
                        offset_seconds=0,
                        phase="FIXED_SPAWN",
                        kind="fixed",
                        names=(name,),
                    ))
        return jobs

    def _load_fixed_boss_entries(self) -> list[dict[str, Any]]:
        try:
            lines = FIXED_BOSSES_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries: list[dict[str, Any]] = []
        for line in lines:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            parts = text.split("|")
            if len(parts) < 5:
                continue
            name = parts[0].strip()
            time_text = parts[1].strip()
            days_text = parts[2].strip()
            repeat_mode = parts[3].strip() or "weekly"
            anchor_date = parts[4].strip()
            enabled_text = parts[5].strip() if len(parts) > 5 else "1"
            try:
                time_value = datetime.strptime(time_text, "%H:%M:%S").time()
            except ValueError:
                continue
            if enabled_text in {"0", "false", "False"}:
                continue
            entries.append({
                "boss_name": name,
                "time_value": time_value,
                "days": {day for day in WEEKDAY_LABELS if day in days_text},
                "repeat_mode": repeat_mode,
                "anchor_date": anchor_date,
                "text_color": parts[6].strip() if len(parts) > 6 else "#FFFFFF",
                "bg_color": parts[7].strip() if len(parts) > 7 else "#2563EB",
            })
        return entries

    @staticmethod
    def _fixed_boss_matches_date(entry: dict[str, Any], target_date: Any) -> bool:
        days = entry.get("days")
        if isinstance(days, set) and days:
            if WEEKDAY_LABELS[target_date.weekday()] not in days:
                return False
        repeat_mode = str(entry.get("repeat_mode") or "weekly")
        if repeat_mode.startswith("biweekly"):
            anchor_text = str(entry.get("anchor_date") or "").strip()
            try:
                anchor_date = datetime.strptime(anchor_text, "%Y-%m-%d").date()
            except ValueError:
                return True
            return ((target_date - anchor_date).days // 7) % 2 == 0
        return True

    @staticmethod
    def _normalize_offsets(raw_offsets: Any) -> list[int]:
        if not isinstance(raw_offsets, list):
            return []
        normalized: set[int] = set()
        for raw_value in raw_offsets:
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 24 * 60 * 60:
                normalized.add(value)
        return sorted(normalized, reverse=True)

    @staticmethod
    def _normalize_hex_color(value: object, fallback: int = 0x2563EB) -> int:
        text = str(value or "").strip().lstrip("#")
        if re.fullmatch(r"[0-9a-fA-F]{6}", text):
            return int(text, 16)
        return int(fallback)

    @staticmethod
    def _load_boss_definition_map() -> dict[str, dict[str, Any]]:
        definitions: dict[str, dict[str, Any]] = {}
        try:
            lines = SCHEDULE_BOSS_DEFINITIONS_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            return definitions
        for line in lines:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            parts = text.split("|")
            if not parts:
                continue
            boss_name = parts[0].strip()
            alias = parts[1].strip() if len(parts) > 1 else ""
            area_name = parts[3].strip() if len(parts) > 3 else ""
            absolute = parts[4].strip() in {"1", "true", "True", "yes", "Y"} if len(parts) > 4 else False
            color_text = parts[6].strip() if len(parts) > 6 else ""
            color_value = ScheduleReader._normalize_hex_color(color_text, fallback=0x2563EB)
            definition = {
                "color": color_value,
                "area": area_name,
                "absolute": absolute,
            }
            if boss_name:
                definitions[boss_name] = dict(definition)
            if alias:
                definitions[alias] = dict(definition)
        return definitions

    def upcoming_rows(self, *, limit: int = 80) -> list[dict[str, Any]]:
        """Return the upcoming rows used by Discord text and image commands."""
        payload = deserialize(load_json(SCHEDULE_STATE_PATH))
        raw_events = payload.get("schedule_events") if isinstance(payload, dict) else []
        now = datetime.now()
        # Discord 안내/복사용 목록은 다음날 오전 8시까지만 제공한다.
        end_at = datetime.combine((now + timedelta(days=1)).date(), datetime.min.time()).replace(hour=8)
        boss_definitions = self._load_boss_definition_map()
        rows: list[dict[str, Any]] = []
        if isinstance(raw_events, list):
            for item in raw_events:
                if not isinstance(item, dict) or str(item.get("state") or "scheduled") not in {"", "scheduled"}:
                    continue
                scheduled_at = parse_datetime(item.get("scheduled_at"))
                name = event_name(item)
                if not isinstance(scheduled_at, datetime) or not name or scheduled_at < now - timedelta(seconds=2) or scheduled_at > end_at:
                    continue
                invasion = bool(item.get("is_invasion"))
                definition = boss_definitions.get(name, {})
                area_name = str(definition.get("area") or "")
                absolute = bool(definition.get("absolute"))
                niflheim_or_higher = area_name in {"니플하임", "바나하임"}
                tier = "절대자" if absolute else "니플하임+" if niflheim_or_higher else "침공" if invasion else "일반"
                rows.append({
                    "scheduled_at": scheduled_at,
                    "name": name,
                    "kind": "침공" if invasion else "일반",
                    "tier": tier,
                    "color": 0xA855F7 if absolute else 0x06B6D4 if niflheim_or_higher else 0xDC2626 if invasion else int(definition.get("color") or 0x2563EB),
                    "text_color": "#FFFFFF",
                    "bg_color": "#DC2626" if invasion else "",
                })
        for entry in self._load_fixed_boss_entries():
            for day_offset in range(0, 8):
                target_date = (now + timedelta(days=day_offset)).date()
                if not self._fixed_boss_matches_date(entry, target_date):
                    continue
                scheduled_at = datetime.combine(target_date, entry["time_value"])
                if scheduled_at < now - timedelta(seconds=2) or scheduled_at > end_at:
                    continue
                rows.append({
                    "scheduled_at": scheduled_at,
                    "name": str(entry.get("boss_name") or ""),
                    "kind": "고정",
                    "tier": "고정보스",
                    "color": self._normalize_hex_color(entry.get("bg_color"), fallback=0x0891B2),
                    "text_color": str(entry.get("text_color") or "#FFFFFF"),
                    "bg_color": str(entry.get("bg_color") or "#0891B2"),
                })
        rows.sort(key=lambda item: (item["scheduled_at"], item["kind"], item["name"]))
        return rows[:max(1, int(limit))]


class DiscordConfigurationError(ValueError):
    """Configuration must be corrected before retrying a connection."""


class DiscordScheduleBot:
    def __init__(self, discord_module: Any, *, enable_message_content: bool = True) -> None:
        self.discord = discord_module
        self.disconnect_only = str(os.environ.get("BOSS_TIMER_DISCORD_DISCONNECT_ONLY") or "").strip() == "1"
        self.disconnect_only_result_path = str(
            os.environ.get("BOSS_TIMER_DISCORD_DISCONNECT_RESULT") or ""
        ).strip()
        self.message_content_enabled = bool(enable_message_content)
        intents = discord_module.Intents.default()
        intents.message_content = self.message_content_enabled
        owner = self

        class ValidatedClient(discord_module.Client):
            async def setup_hook(client_self) -> None:
                await super().setup_hook()
                owner._validate_authenticated_application(client_self.application_id)

        self.client = ValidatedClient(intents=intents)
        from discord_authority_events import AuthorityEvents
        self.authority_events = AuthorityEvents(self, STATUS)
        self.authority_disconnect_generation = -1
        self.tree = discord_module.app_commands.CommandTree(self.client)
        self.schedule_reader = ScheduleReader()
        self.voice_bridge_reader = VoiceBridgeReader(VOICE_BRIDGE_PATH)
        self.announced: set[str] = set()
        self.play_queue: queue.Queue[tuple[int, AlertJob | VoiceBridgeJob] | None] = queue.Queue()
        self.voice_client: Any = None
        self.voice_play_lock = asyncio.Lock()
        self.voice_transition_lock = asyncio.Lock()
        self.notice_output = None
        StatusHandler.notice_bot = self
        self.regular_voice_job_active = False
        self.timed_bridge_tasks: dict[str, tuple[str, asyncio.Task[Any]]] = {}
        self.cancelled_voice_bridge_scopes: dict[str, float] = {}
        self.text_notice_keys: set[str] = set()
        self.boss_notice_groups: dict[tuple[str, ...], dict[str, Any]] = {}
        self.boss_notice_lock = asyncio.Lock()
        self.current_voice_scope_id = ""
        self.current_voice_playback_token: object | None = None
        self.current_voice_player_thread: Any = None
        self.current_timed_composite_source: Any = None
        self.current_timed_composite_scope_id = ""
        self.timed_pcm_cache: dict[tuple[str, int, int, int, str], bytes] = {}
        self.timed_pcm_cache_lock = threading.Lock()
        self.schedule_task: asyncio.Task[Any] | None = None
        self.play_task: asyncio.Task[Any] | None = None
        self.shutdown_task: asyncio.Task[Any] | None = None
        self.command_sync_task: asyncio.Task[Any] | None = None
        self.gateway_recovery_task: asyncio.Task[Any] | None = None
        self.voice_panel_startup_task: asyncio.Task[Any] | None = None
        self.gateway_disconnected_at: float | None = None
        self.last_voice_reconnect_attempt_at = 0.0
        self.schedule_request_lock = asyncio.Lock()
        self.config = load_config()
        self.connection_policy = ConnectionPolicy(CONFIG_PATH)
        state = self.connection_policy.snapshot()
        self.connection_policy.update(protocol=CONTROL_PROTOCOL, runtime_id=STATUS.runtime_id,
                                      standby=True, authority_until=0, authority_active=False, voice_requested=False)
        STATUS.update(client_id=state["client_id"], application_id=str(self.config.get("application_id") or ""))
        self.authority_watch_task = None
        self.authority_connect_task = None
        self.connected_authority_generation = None
        self.voice_authority_was_owned = False
        self.commands_registered = False
        self.command_generation = None
        self.next_command_sync_at = 0
        self.voice_connect_lock = asyncio.Lock()
        self.voice_channel_panel_message_id = str(self.config.get("voice_panel_message_id") or "").strip()
        self.custom_voice_commands = load_custom_discord_voice_commands()
        self.disabled_builtin_voice_commands = load_disabled_builtin_discord_voice_commands()
        self.alarm_settings: dict[str, Any] = {}
        STATUS.update(
            guild_id=str(self.config.get("server_id") or "").strip(),
            configuration_error="",
            voice_bridge_enabled=config_bool(self.config, "voice_bridge_enabled", True),
            text_commands_enabled=self.message_content_enabled,
        )
        self._bind_events()
        self._bind_commands()

    def _connection_state(self):
        state = self.connection_policy.snapshot()
        owns = ConnectionPolicy.has_authority(state, STATUS.runtime_id, allow_joining=True)
        STATUS.update(standby=not owns, reconnect_attempts=state["retries"],
                      handover_hold=state["handover_hold"], configuration_error=state["error"],
                      voice_error=state["voice_error"], voice_reconnect_attempts=state["voice_retries"],
                      send_authorized=owns and state["authority_active"] and state["voice_requested"] and not state["voice_error"],
                      authority_joining=owns and not state["authority_active"])
        return state

    def _connection_is_blocked(self):
        state = self._connection_state()
        return bool(not ConnectionPolicy.has_authority(state, STATUS.runtime_id, allow_joining=True)
                    or not state["voice_requested"] or state["voice_error"] or state["error"])

    def _send_allowed(self):
        state = self._connection_state()
        task = asyncio.current_task()
        expected = getattr(task, "boss_authority_generation", state["authority_generation"])
        return bool(STATUS.online and ConnectionPolicy.has_authority(state, STATUS.runtime_id)
                    and state["voice_requested"] and not state["voice_error"]
                    and expected == state["authority_generation"])

    def _tag_authority_task(self):
        task = asyncio.current_task()
        if task is not None and not hasattr(task, "boss_authority_generation"):
            task.boss_authority_generation = self._connection_state()["authority_generation"]

    def _authority_voice_client_type(self, expected_generation):
        bot = self
        class AuthorityVoiceClient(bot.discord.VoiceClient):
            def owns_authority(voice_self):
                local = bot.connection_policy.snapshot()
                return (ConnectionPolicy.has_authority(local, STATUS.runtime_id, allow_joining=True)
                        and local["authority_generation"] == expected_generation)

            def cleanup(voice_self):
                # A late disconnect must not remove a replacement from the
                # library's per-guild voice-client registry.
                if getattr(voice_self.guild, "voice_client", None) is voice_self:
                    super().cleanup()

            async def disconnect(voice_self, *, force=False):
                voice_self.stop()
                try:
                    # Revoked instances never send a guild leave, so no leave
                    # acknowledgement will arrive for them.
                    await voice_self._connection.disconnect(force=force, wait=voice_self.owns_authority())
                finally:
                    voice_self.cleanup()

            def create_connection_state(voice_self):
                state = super().create_connection_state()
                generation = expected_generation
                voice_self.authority_generation = generation
                connect, disconnect = state._voice_connect, state._voice_disconnect
                def authorized():
                    return voice_self.owns_authority()
                async def guarded_connect(**kw):
                    if not authorized():
                        raise RuntimeError("관리자 권한이 변경되어 음성 접속을 중단했습니다.")
                    await connect(**kw)
                async def guarded_disconnect():
                    # A superseded PC must not send a guild-wide leave and kick
                    # the new owner's voice session. Local resources still close.
                    if authorized() or bot.authority_disconnect_generation == generation:
                        await disconnect()
                state._voice_connect, state._voice_disconnect = guarded_connect, guarded_disconnect
                return state

            def send_audio_packet(voice_self, data, *, encode=True):
                # AudioPlayer runs on its own thread; fence every packet even
                # when the asyncio loop stalls or an old clip is still alive.
                local = bot.connection_policy.snapshot()
                if (not STATUS.online or not ConnectionPolicy.has_authority(local, STATUS.runtime_id)
                        or not local["voice_requested"]
                        or local["authority_generation"] != voice_self.authority_generation):
                    return
                super().send_audio_packet(data, encode=encode)
        return AuthorityVoiceClient

    async def _close_voice_client(self, voice, *, reason):
        if voice is None:
            return
        try:
            await asyncio.wait_for(voice.disconnect(force=True), timeout=3)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"voice_cleanup_failed reason={reason} type={type(exc).__name__} error={exc!r}")
        finally:
            for close in (voice.stop, voice.cleanup):
                try:
                    close()
                except Exception as exc:
                    log(f"voice_resource_cleanup_failed reason={reason} type={type(exc).__name__} error={exc!r}")
            if self.voice_client is voice:
                self.voice_client = None
                STATUS.update(voice_connected=False)

    async def _disconnect_authority_voice(self):
        for _, task in list(self.timed_bridge_tasks.values()):
            task.cancel()
        while True:
            try:
                self.play_queue.get_nowait()
            except queue.Empty:
                break
        async with self.voice_transition_lock:
            await self._stop_current_voice_playback_and_wait(reason="authority_lost")
        if self.notice_output is not None:
            await self.notice_output.stop()
        async with self.voice_connect_lock:
            voice = self.voice_client
            local = self.connection_policy.snapshot()
            release_generation = local.get("release_voice_generation", -1)
            self.authority_disconnect_generation = (release_generation if voice is not None
                and getattr(voice, "authority_generation", None) == release_generation else -1)
            try:
                await self._close_voice_client(voice, reason="authority_lost")
            finally:
                self.authority_disconnect_generation = -1
        guild = self.client.get_guild(int(self._get_configured_server_id()))
        deadline = time.monotonic() + 3
        while (guild is not None and getattr(guild, "me", None) is not None
               and getattr(getattr(guild.me, "voice", None), "channel", None) is not None
               and time.monotonic() < deadline):
            await asyncio.sleep(.1)
        confirmed = (STATUS.online and guild is not None and getattr(guild, "me", None) is not None
                     and getattr(getattr(guild.me, "voice", None), "channel", None) is None)
        STATUS.update(voice_connected=False, voice_disconnect_confirmed=confirmed)

    async def _authority_watch_loop(self):
        while not STATUS.shutdown_requested.is_set():
            try:
                state = self._connection_state()
                owns = ConnectionPolicy.has_authority(state, STATUS.runtime_id, allow_joining=True)
                requested = owns and state["voice_requested"]
                if self.voice_authority_was_owned and not requested:
                    reason = ("state_read_error" if state["error"] else "voice_released" if not state["voice_requested"]
                              else "controller_unavailable" if state["authority_until"] <= time.monotonic() else "authority_revoked")
                    log(f"voice_authority_stopped reason={reason} generation={state['authority_generation']} "
                        f"lease_remaining_sec={state['authority_until'] - time.monotonic():.3f}")
                self.voice_authority_was_owned = requested
                if not owns or not state["voice_requested"]:
                    if self.authority_connect_task is not None and not self.authority_connect_task.done():
                        self.authority_connect_task.cancel()
                        await asyncio.gather(self.authority_connect_task, return_exceptions=True)
                    if self.voice_client is not None:
                        await self._disconnect_authority_voice()
                    guild = self.client.get_guild(int(self._get_configured_server_id()))
                    confirmed = (STATUS.online and guild is not None and getattr(guild, "me", None) is not None
                        and getattr(getattr(guild.me, "voice", None), "channel", None) is None)
                    STATUS.update(voice_connected=False, voice_disconnect_confirmed=confirmed)
                elif STATUS.online and not state["voice_error"]:
                    generation = state["authority_generation"]
                    if (self.authority_connect_task is not None and not self.authority_connect_task.done()
                            and getattr(self.authority_connect_task, "voice_generation", generation) != generation):
                        self.authority_connect_task.cancel()
                        await asyncio.gather(self.authority_connect_task, return_exceptions=True)
                    if self.connected_authority_generation != generation:
                        if self.authority_connect_task is None or self.authority_connect_task.done():
                            self.authority_connect_task = asyncio.create_task(self._join_authority_voice(generation))
                            self.authority_connect_task.voice_generation = generation
                if (state["command_sync_allowed"] and self.client.is_ready()
                        and (not self.commands_registered or self.command_generation != state["authority_generation"])
                        and time.monotonic() >= self.next_command_sync_at):
                    if self.command_sync_task is None or self.command_sync_task.done():
                        self.next_command_sync_at = time.monotonic() + 30
                        self.command_sync_task = asyncio.create_task(self._sync_commands())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log(f"authority_watch_failed type={type(exc).__name__} error={exc!r}")
            await asyncio.sleep(.1)

    async def _join_authority_voice(self, generation):
        try:
            STATUS.update(voice_disconnect_confirmed=False)
            if self.voice_client is not None:
                await self._disconnect_authority_voice()
            if self._connection_is_blocked() or self._connection_state()["authority_generation"] != generation:
                return
            await self._disconnect_stale_configured_voice_session(wait_seconds=.5, force=True)
            if self._connection_is_blocked() or self._connection_state()["authority_generation"] != generation:
                return
            connected = await self._connect_configured_voice_channel(automatic=False, expected_generation=generation)
            if not connected:
                raise RuntimeError(STATUS.last_error or "음성채널 입장에 실패했습니다.")
            self.connected_authority_generation = generation
            # Publish panels only after the GUI commits active ownership.
            self.voice_panel_startup_task = asyncio.create_task(self._initialize_voice_panel_when_authorized(generation))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.connection_policy.snapshot()["authority_generation"] == generation:
                self.connection_policy.update(voice_error=str(exc))
                STATUS.update(last_error=str(exc), voice_connected=False)

    async def _initialize_voice_panel_when_authorized(self, generation):
        while ConnectionPolicy.has_authority(self._connection_state(), STATUS.runtime_id, allow_joining=True):
            if self._connection_state()["authority_generation"] != generation:
                return
            if self._send_allowed():
                self._tag_authority_task()
                await self._initialize_voice_panel_after_ready()
                return
            await asyncio.sleep(.1)

    async def _defer_interaction_once(self, interaction) -> bool | None:
        if not self._is_configured_server_id(getattr(interaction, "guild_id", None)):
            return False
        # The successful first acknowledgement selects one handler across PCs.
        try:
            await interaction.response.defer(ephemeral=True, thinking=True)
        except self.discord.HTTPException as exc:
            if getattr(exc, "code", None) == 10062:
                return None  # Expired interactions cannot participate in routing.
            if getattr(exc, "code", None) == 40060:
                return False
            raise
        except self.discord.InteractionResponded:
            return False
        return True

    async def _reply_private_text(self, interaction, body: str, *, ansi: bool = False) -> None:
        # Leave room for code fences and keep every Discord message below 2000.
        lines = []
        for line in body.splitlines():
            lines.extend(line[offset:offset + 1850] for offset in range(0, len(line), 1850))
            if not line:
                lines.append("")
        chunks, current = [], ""
        for line in lines:
            candidate = current + "\n" + line if current else line
            if current and len(candidate) > 1850:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)
        for chunk in chunks:
            await interaction.followup.send(
                f"```ansi\n{chunk}\n```" if ansi else chunk,
                ephemeral=True, allowed_mentions=self.discord.AllowedMentions.none(),
            )

    async def _handle_readonly_query(self, interaction, query: str) -> bool:
        """Use the cached responder directly; shared routing is only a fallback."""
        if not self._is_configured_server_id(getattr(interaction, "guild_id", None)):
            return True
        state = self._connection_state()
        # An interaction belongs to one application; another bot token cannot
        # acknowledge it. Select among PCs running this application only.
        members = [member for member in state.get("query_members", [])
                   if str(member.get("application_id") or STATUS.application_id) == STATUS.application_id]
        own_member = next((member for member in members if member.get("client_id") == STATUS.client_id
                           and member.get("runtime") == STATUS.runtime_id), {})
        owns = self._send_allowed()
        preferred = members[0] if members and state.get("query_until", 0) > time.monotonic() else {}
        direct = owns or (preferred.get("client_id") == STATUS.client_id
                          and preferred.get("runtime") == STATUS.runtime_id and not preferred.get("sending"))
        if direct and not owns:
            # A newly elected sender can precede a still-cached standby PC.
            await asyncio.sleep(.25)
        elif not direct:
            # Let the selected PC acknowledge first without any GitHub round trip.
            await asyncio.sleep(.75)
        coordinator = await self._defer_interaction_once(interaction)
        if coordinator is None:
            return True
        if coordinator and direct:
            try:
                log(f"query_direct query_id={interaction.id} query={query} client_id={STATUS.client_id[:8]} sender={int(owns)}")
                await self._execute_readonly_query(interaction, query, own_member)
            except Exception as exc:
                log(f"query_direct_failed query_id={interaction.id} type={type(exc).__name__} error={exc!r}")
                await self._reply_private_text(interaction, "조회 자료를 표시하지 못했습니다. 다시 시도하세요.")
            return True
        if coordinator:
            if self.connection_policy.snapshot().get("query_protocol") != 1:
                await self._reply_private_text(interaction, "조회 연결 정보가 준비되지 않았습니다. GUI와 봇을 모두 새 버전으로 실행한 뒤 다시 조회하세요.")
                return True
            await interaction.edit_original_response(content=QUERY_ROUTING_WAIT_TEXT,
                                                     allowed_mentions=self.discord.AllowedMentions.none())
        elif not await self._is_query_routing_pending(interaction):
            return True  # The direct responder already claimed this query.
        # Only a missing/late direct responder goes through the shared CAS ledger.
        tasks = [asyncio.create_task(self._participate_readonly_query(interaction, query))]
        if coordinator:
            tasks.append(asyncio.create_task(self._coordinate_readonly_query(interaction, query)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return True

    async def _is_query_routing_pending(self, interaction) -> bool:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                # original_response() caches its first value; fetch fresh while
                # the initial ack is still loading on another PC.
                message = await interaction.followup.fetch_message("@original")
            except self.discord.HTTPException:
                return False
            if message.content == QUERY_ROUTING_WAIT_TEXT:
                return True
            if not message.flags.loading:
                return False
            await asyncio.sleep(.1)
        return False

    def _cache_query_responder(self, message):
        if not message.get("retries") or not message.get("source"):
            return
        state = self.connection_policy.snapshot()
        generation = int(message.get("routing_generation", -1))
        if generation < state.get("query_routing_generation", -1):
            return
        member = message["source"]
        members = [value for value in state.get("query_members", []) if value.get("client_id") != member.get("client_id")]
        members.append(member)
        members.sort(key=lambda value: (not bool(value.get("sending")), -float(value.get("connected_at", 0)), value.get("client_id", "")))
        self.connection_policy.update(query_members=QueryLedger.order_candidates(members, member),
                                      query_responder=member, query_routing_generation=generation,
                                      query_until=time.monotonic() + 10)

    async def _query_routing_results(self, interaction, query, operation):
        request_id = f"query_{interaction.id}_{STATUS.runtime_id}_{operation}"
        result_path = DISCORD_SCHEDULE_REQUEST_DIR / "results" / (request_id + ".json")
        await self._queue_local_schedule_request(dict(
            operation=operation, request_id=request_id, query_id=str(interaction.id), query=query,
            application_id=STATUS.application_id,
            server_id=self._get_configured_server_id(), runtime_id=STATUS.runtime_id,
            created_at=time.time(), raw_text="스케줄 조회 담당자 선택",
        ))
        deadline = time.monotonic() + 120
        last_payload = None
        try:
            while time.monotonic() < deadline and not STATUS.shutdown_requested.is_set():
                try:
                    result = json.loads(_read_state_text(result_path))
                except (FileNotFoundError, PermissionError, ValueError):
                    await asyncio.sleep(.1)
                    continue
                if result != last_payload:
                    last_payload = result
                    yield result
                message = result.get("message")
                if not result.get("ok") or not isinstance(message, dict) or message.get("state") != "progress":
                    return
                await asyncio.sleep(.1)
            yield dict(ok=False, message="조회 응답을 받지 못했습니다. 관리자를 변경하고 다시 시도하세요.")
        finally:
            try:
                result_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _coordinate_readonly_query(self, interaction, query):
        announced = 0
        try:
            async for result in self._query_routing_results(interaction, query, "readonly_query_route"):
                if not result.get("ok"):
                    await self._reply_private_text(interaction, "조회 요청을 처리하지 못했습니다. 관리자를 변경하고 다시 시도하세요.")
                    log(f"query_route_failed query_id={interaction.id} error={result.get('message')!r}")
                    return
                message = result["message"]
                retries = message.get("retries", [])
                for retry in retries[announced:]:
                    previous = retry["previous"]
                    following = retry.get("next")
                    previous_name = f"{previous.get('name') or '담당자'} ({previous.get('display_id') or previous.get('client_id', '')[:8]})"
                    if following:
                        next_name = f"{following.get('name') or '담당자'} ({following.get('display_id') or following.get('client_id', '')[:8]})"
                        text = f"{previous_name} 담당자가 3초 안에 응답하지 않아 {next_name} 담당자로 조회 담당자를 변경하고 다시 시도합니다."
                    else:
                        text = f"{previous_name} 담당자가 3초 안에 응답하지 않았습니다. 관리자를 변경하고 다시 시도하세요."
                    await self._reply_private_text(interaction, text)
                announced = len(retries)
                if message.get("state") == "unavailable" and not retries:
                    await self._reply_private_text(interaction, "조회할 관리자가 없습니다. 관리자를 변경하거나 봇 연결을 확인한 뒤 다시 시도하세요.")
        except OSError as exc:
            log(f"query_route_local_failed query_id={interaction.id} error={exc!r}")
            await self._reply_private_text(interaction, "조회 요청을 전달하지 못했습니다. 관리자를 변경하고 다시 시도하세요.")

    async def _participate_readonly_query(self, interaction, query):
        selected = False
        try:
            async for result in self._query_routing_results(interaction, query, "readonly_query_wait"):
                message = result.get("message")
                if not result.get("ok"):
                    log(f"query_participation_failed query_id={interaction.id} error={message!r}")
                if result.get("ok") and isinstance(message, dict):
                    self._cache_query_responder(message)
                if result.get("ok") and isinstance(message, dict) and message.get("state") == "selected":
                    selected = True
                    await self._execute_readonly_query(interaction, query, message.get("source", {}))
        except Exception as exc:
            log(f"query_response_failed query_id={interaction.id} type={type(exc).__name__} error={exc!r}")
            if selected:
                await self._reply_private_text(interaction, "조회 자료를 표시하지 못했습니다. 관리자를 변경하고 다시 시도하세요.")

    async def _execute_readonly_query(self, interaction, query, member):
        if self._send_allowed():
            self._tag_authority_task()
        if query == "help":
            from discord_command_help import COMMAND_HELP
            await self._reply_private_text(interaction, COMMAND_HELP)
            return
        source = (f"응답 담당자: {member.get('name', '담당자')} · {member.get('display_id') or STATUS.client_id[:8]} · "
                  f"{member.get('server', '')} · {member.get('season', '')}차 · 이 PC의 로컬 자료")
        await self._reply_private_text(interaction, source)
        if self._send_allowed() and query in {"schedule", "plain", "image"}:
            channel = await self._resolve_text_channel(getattr(interaction, "guild", None))
            if channel is None:
                await self._reply_private_text(interaction, "안내 채널을 찾지 못했습니다. /보탐채널을 먼저 실행하세요.")
                return
            if query == "schedule":
                await self._send_schedule_text(channel)
            elif query == "plain":
                await self._send_schedule_plain_text(channel)
            else:
                image_path = await self._request_local_schedule_copy_image()
                if not image_path:
                    await self._reply_private_text(interaction, "로컬 스케쥴복사 이미지를 만들지 못했습니다. 프로그램이 실행 중인지 확인하고 다시 요청하세요.")
                    return
                if not self._send_allowed():
                    await self._reply_private_text(interaction, "송출 담당자가 변경되었습니다. 다시 조회하세요.")
                    return
                await channel.send(content="✦ 보탐매니저 스케쥴", file=self.discord.File(image_path, filename="bosstimer_schedule.png"))
                await self._cleanup_bot_text_channel_messages(channel)
            await self._reply_private_text(interaction, f"스케쥴을 {getattr(channel, 'mention', '안내 채널')}에 전송했습니다.")
            return
        if query in {"schedule", "plain"}:
            rows = self.schedule_reader.upcoming_rows(limit=120)
            if query == "schedule":
                body = self._format_schedule_rows_as_ansi(rows)
            else:
                body = "\n".join(
                    f"{scheduled_at.strftime('%H%M')} {str(row.get('name') or '')}"
                    for row in rows
                    if isinstance((scheduled_at := row.get("scheduled_at")), datetime)
                ) or "예정된 스케쥴이 없습니다."
            await self._reply_private_text(interaction, body, ansi=query == "schedule")
        elif query == "voices":
            entries = self._get_discord_voice_command_menu_entries()
            if not entries:
                await self._reply_private_text(interaction, "등록된 음성 명령이 없습니다.")
                return
            if self._send_allowed():
                channel = await self._resolve_voice_panel_channel(getattr(interaction, "guild", None))
                current_id = str(getattr(getattr(interaction, "channel", None), "id", "") or "")
                if channel is None or str(channel.id) != current_id:
                    text = f"사운드보드 명령은 <#{channel.id}> 채팅채널에서만 사용할 수 있습니다." if channel else "전용 채팅채널에서 /음성채널을 먼저 실행하세요."
                    await self._reply_private_text(interaction, text)
                    return
                if self._send_allowed():
                    owner_id = str(getattr(getattr(interaction, "user", None), "id", "") or "")
                    view = self._build_discord_voice_command_menu_view(owner_id=owner_id)
                    await interaction.followup.send(
                        f"등록 음성 {len(entries)}개입니다. 📢 기본 · 🔊 사용자 TTS · 📁 파일 전용\n"
                        "음성 버튼을 누르면 송출합니다. 이전/다음 버튼으로 목록을 넘길 수 있습니다.",
                        view=view, ephemeral=True,
                    )
                    return
            body = (f"등록 음성 {len(entries)}개 (송출 권한이 있으면 재생할 수 있습니다.)\n"
                    + "\n".join(f"• {name}" for _, name, _ in entries))
            await self._reply_private_text(interaction, body)
        elif query == "image":
            image_path = await self._request_local_schedule_copy_image()
            if image_path:
                await interaction.followup.send(
                    file=self.discord.File(image_path, filename="bosstimer_schedule.png"),
                    ephemeral=True, allowed_mentions=self.discord.AllowedMentions.none(),
                )
            else:
                await self._reply_private_text(interaction, "로컬 스케쥴복사 이미지를 만들지 못했습니다. 프로그램이 실행 중인지 확인하고 다시 요청하세요.")

    async def _administrator_command(self, interaction, operation, *, target="", channel=""):
        if not await self._defer_interaction_once(interaction):
            return
        permissions = getattr(getattr(interaction, "user", None), "guild_permissions", None)
        if not (getattr(permissions, "administrator", False) or getattr(permissions, "manage_guild", False)):
            await interaction.followup.send("Discord 서버 관리자 또는 서버 관리 권한이 필요합니다.", ephemeral=True)
            return
        request_id = str(interaction.id)
        if not channel and operation == "administrator_connect":
            voice = getattr(getattr(interaction, "user", None), "voice", None)
            channel = str(getattr(getattr(voice, "channel", None), "id", "") or "")
        payload = dict(operation=operation, request_id=request_id, server_id=self._get_configured_server_id(),
                       runtime_id=STATUS.runtime_id, created_at=time.time(), target_administrator=target,
                       target_client_id=target,
                       voice_channel_id=channel, raw_text="관리자 제어")
        try:
            await self._queue_local_schedule_request(payload)
        except OSError as exc:
            log(f"administrator_request_write_failed operation={operation} type={type(exc).__name__} error={exc!r}")
            await interaction.followup.send("관리자 요청을 프로그램에 전달하지 못했습니다. 프로그램의 연결 상태를 확인하세요.", ephemeral=True)
            return
        result_path = DISCORD_SCHEDULE_REQUEST_DIR / "results" / (request_id + ".json")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                result_path.unlink(missing_ok=True)
                message = str(result.get("message") or "요청 완료")
                for offset in range(0, len(message), 1900):
                    await interaction.followup.send(message[offset:offset+1900], ephemeral=True, allowed_mentions=self.discord.AllowedMentions.none())
                return
            except FileNotFoundError:
                pass
            await asyncio.sleep(.2)
        await interaction.followup.send("프로그램의 요청 응답을 받지 못했습니다. 관리자 목록과 프로그램 상태를 확인하세요.", ephemeral=True)

    def _block_connection_error(self, message):
        policy = getattr(self, "connection_policy", None)
        if policy is not None:
            policy.update(error=message)
        STATUS.update(configuration_error=message, last_error=message)

    def _claim_connection_retry(self):
        return self.connection_policy.claim_voice_retry()

    def _validate_authenticated_application(self, actual_id: Any) -> None:
        expected = str(self.config.get("application_id") or "").strip()
        if expected and expected == str(actual_id or ""):
            return
        message = (
            "봇 토큰과 Application ID가 일치하지 않습니다. 새 봇을 사용하려면 "
            "해당 봇의 토큰과 Application ID를 함께 입력한 뒤 봇을 다시 실행하세요."
        )
        self._block_connection_error(message)
        STATUS.update(online=False, voice_connected=False)
        log("discord_application_identity_rejected")
        raise DiscordConfigurationError(message)

    def _get_configured_server_id(self) -> str:
        server_id = str(self.config.get("server_id") or "").strip()
        return server_id if server_id.isdigit() else ""

    def _is_configured_server_id(self, guild_id: Any) -> bool:
        configured_server_id = self._get_configured_server_id()
        incoming_server_id = str(guild_id or "").strip()
        return bool(
            configured_server_id
            and incoming_server_id.isdigit()
            and incoming_server_id == configured_server_id
        )

    def _is_configured_guild(self, guild: Any) -> bool:
        return self._is_configured_server_id(getattr(guild, "id", ""))

    def _resolve_discord_voice_command(self, value: object) -> tuple[str, str] | None:
        normalized = normalize_discord_voice_command_name(value)
        if not normalized:
            return None
        # A deleted built-in stays hidden even when an old override is still
        # present in the registry file.
        if normalized in set(getattr(self, "disabled_builtin_voice_commands", set()) or set()):
            return None
        # User entries have priority.  This makes /음성추가 with a built-in
        # name a real overwrite instead of an operation that only logs success.
        custom_commands = getattr(self, "custom_voice_commands", {})
        command_data = custom_commands.get(normalized) if isinstance(custom_commands, dict) else None
        if isinstance(command_data, tuple) and len(command_data) == 2:
            display_name, tts_text = command_data
            if str(display_name).strip():
                return str(display_name), str(tts_text)
        if command_data:
            return str(command_data), str(command_data)
        for name, tts_text in BUILTIN_DISCORD_VOICE_COMMANDS.items():
            if normalize_discord_voice_command_name(name) == normalized:
                return name, tts_text
        return None

    def _add_discord_voice_command(
        self,
        value: object,
        *,
        allow_empty_tts: bool = False,
    ) -> tuple[bool, str, str, str]:
        valid, name_or_error, tts_text = parse_discord_voice_command_addition(
            value,
            allow_empty_tts=allow_empty_tts,
        )
        if not valid:
            return False, name_or_error, "", ""
        name = name_or_error
        normalized = normalize_discord_voice_command_name(name)
        builtin_keys = {
            normalize_discord_voice_command_name(command_name)
            for command_name in BUILTIN_DISCORD_VOICE_COMMANDS
        }
        if normalized in builtin_keys:
            disabled_commands = set(getattr(self, "disabled_builtin_voice_commands", set()) or set())
            disabled_commands.discard(normalized)
            commands = dict(getattr(self, "custom_voice_commands", {}) or {})
            commands[normalized] = (name, tts_text)
            save_custom_discord_voice_commands(
                commands,
                disabled_builtin_commands=disabled_commands,
            )
            self.custom_voice_commands = commands
            self.disabled_builtin_voice_commands = disabled_commands
            target_text = tts_text or "파일 전용 (TTS 없음)"
            return True, f"기본 음성 명령을 덮어썼습니다: {name} → {target_text}", name, tts_text
        if self._resolve_discord_voice_command(name) is not None:
            return False, f"이미 등록된 음성 명령입니다: {name}", name, tts_text
        commands = dict(getattr(self, "custom_voice_commands", {}) or {})
        commands[normalized] = (name, tts_text)
        save_custom_discord_voice_commands(
            commands,
            disabled_builtin_commands=set(getattr(self, "disabled_builtin_voice_commands", set()) or set()),
        )
        self.custom_voice_commands = commands
        target_text = tts_text or "파일 전용 (TTS 없음)"
        return True, f"음성 명령을 추가했습니다: {name} → {target_text}", name, tts_text

    def _delete_discord_voice_command(self, value: object) -> tuple[bool, str]:
        valid, name_or_error = validate_discord_voice_command_name(value)
        if not valid:
            return False, name_or_error
        normalized = normalize_discord_voice_command_name(name_or_error)
        builtin_keys = {
            normalize_discord_voice_command_name(name) for name in BUILTIN_DISCORD_VOICE_COMMANDS
        }
        if normalized in builtin_keys:
            disabled_commands = set(getattr(self, "disabled_builtin_voice_commands", set()) or set())
            if normalized in disabled_commands:
                return False, f"이미 삭제된 기본 음성 명령입니다: {name_or_error}"
            disabled_commands.add(normalized)
            commands = dict(getattr(self, "custom_voice_commands", {}) or {})
            # Do not reveal the original phrase after deleting an overridden
            # command; the command must disappear from the panel completely.
            commands.pop(normalized, None)
            save_custom_discord_voice_commands(
                commands,
                disabled_builtin_commands=disabled_commands,
            )
            self.custom_voice_commands = commands
            self.disabled_builtin_voice_commands = disabled_commands
            return True, f"기본 음성 명령을 삭제했습니다: {name_or_error}"
        commands = dict(getattr(self, "custom_voice_commands", {}) or {})
        removed_command = commands.pop(normalized, None)
        if not removed_command:
            return False, f"등록되지 않은 음성 명령입니다: {name_or_error}"
        save_custom_discord_voice_commands(
            commands,
            disabled_builtin_commands=set(getattr(self, "disabled_builtin_voice_commands", set()) or set()),
        )
        self.custom_voice_commands = commands
        removed_name = removed_command[0] if isinstance(removed_command, tuple) else removed_command
        return True, f"음성 명령을 삭제했습니다: {removed_name}"

    def _get_discord_voice_command_menu_entries(self) -> list[tuple[str, str, str]]:
        """Return built-in and this server's custom commands for the click UI."""
        entries: list[tuple[str, str, str]] = []
        seen: set[str] = set()
        disabled_builtin_commands = set(getattr(self, "disabled_builtin_voice_commands", set()) or set())
        custom_commands = getattr(self, "custom_voice_commands", {})
        # User entries go first so an identical built-in name is represented by
        # its saved replacement text in both the panel and /음성목록.
        if isinstance(custom_commands, dict):
            for normalized, command_data in sorted(custom_commands.items(), key=lambda item: str(item[0]).casefold()):
                if normalized in seen or normalized in disabled_builtin_commands:
                    continue
                if not isinstance(command_data, tuple) or len(command_data) != 2:
                    continue
                command_name, tts_text = command_data
                command_name = str(command_name or "").strip()
                if not command_name:
                    continue
                seen.add(normalized)
                entries.append((str(normalized), command_name, str(tts_text or "").strip()))
        for command_name, tts_text in BUILTIN_DISCORD_VOICE_COMMANDS.items():
            normalized = normalize_discord_voice_command_name(command_name)
            if not normalized or normalized in seen or normalized in disabled_builtin_commands:
                continue
            seen.add(normalized)
            entries.append((normalized, str(command_name), str(tts_text or "")))
        return entries

    def _build_discord_voice_command_menu_view(
        self,
        *,
        owner_id: str = "",
        persistent: bool = False,
    ) -> Any:
        """Create a paged button-grid soundboard that enqueues one voice command."""
        entries = self._get_discord_voice_command_menu_entries()
        view = self.discord.ui.View(timeout=None if persistent else 300)
        entry_by_key = {key: (name, tts_text) for key, name, tts_text in entries}
        page_size = DISCORD_VOICE_COMMAND_MENU_PAGE_SIZE
        page_count = max(1, (len(entries) + page_size - 1) // page_size)
        current_page = 0

        async def queue_selected_command(interaction: Any, command_key: str) -> None:
            if not await self._require_soundboard_channel(interaction):
                return
            if owner_id and str(getattr(getattr(interaction, "user", None), "id", "") or "") != owner_id:
                await interaction.response.send_message("이 음성 목록을 연 사용자만 누를 수 있습니다.", ephemeral=True)
                return
            command_data = entry_by_key.get(command_key)
            if command_data is None:
                await interaction.response.send_message("음성 명령을 찾지 못했습니다. 목록을 다시 여세요.", ephemeral=True)
                return
            command_name, tts_text = command_data
            # A component acknowledgement is enough.  Do not create an
            # ephemeral "voice requested" message for every button click.
            await interaction.response.defer()
            request_payload = {
                "operation": "voice_play",
                "voice_command": command_name,
                "tts_text": tts_text,
                "raw_text": f"/음성목록 {command_name}",
                "request_id": uuid.uuid4().hex,
                "received_at": datetime.now().isoformat(timespec="seconds"),
                "channel_id": str(getattr(getattr(interaction, "channel", None), "id", "") or ""),
                "author_id": str(getattr(getattr(interaction, "user", None), "id", "") or ""),
                "author_name": str(
                    getattr(getattr(interaction, "user", None), "display_name", "")
                    or getattr(getattr(interaction, "user", None), "name", "")
                    or "사용자"
                ),
                "server_id": self._get_configured_server_id(),
            }
            try:
                await self._queue_local_schedule_request(request_payload)
            except OSError as exc:
                log(f"voice_menu_request_write_failed command={command_name} error={exc}")
                await interaction.followup.send("로컬 보스 타이머에 송출 요청을 전달하지 못했습니다.", ephemeral=True)
                return
            asyncio.create_task(
                self._cleanup_voice_panel_after_activity(getattr(interaction, "channel", None))
            )

        def render_page(page: int) -> None:
            nonlocal current_page
            current_page = max(0, min(page_count - 1, int(page)))
            view.clear_items()
            page_entries = entries[current_page * page_size:(current_page + 1) * page_size]
            builtin_keys = {
                normalize_discord_voice_command_name(command_name)
                for command_name in BUILTIN_DISCORD_VOICE_COMMANDS
            }
            overridden_builtin_keys = {
                command_key
                for command_key in builtin_keys
                if command_key in set(getattr(self, "custom_voice_commands", {}) or {})
            }
            for index, (command_key, command_name, tts_text) in enumerate(page_entries):
                is_builtin = command_key in builtin_keys and command_key not in overridden_builtin_keys
                emoji = "📢" if is_builtin else ("🔊" if tts_text else "📁")
                button = self.discord.ui.Button(
                    label=command_name[:80],
                    emoji=emoji,
                    style=(
                        self.discord.ButtonStyle.primary
                        if is_builtin
                        else (self.discord.ButtonStyle.success if tts_text else self.discord.ButtonStyle.secondary)
                    ),
                    row=index // 5,
                    custom_id=(
                        f"boss_timer_voice:{current_page}:{index}:{command_key}"[:100]
                        if persistent
                        else None
                    ),
                )

                async def on_button(interaction: Any, selected_key: str = command_key) -> None:
                    await queue_selected_command(interaction, selected_key)

                button.callback = on_button
                view.add_item(button)
            if page_count > 1:
                previous_button = self.discord.ui.Button(
                    label="◀ 이전",
                    style=self.discord.ButtonStyle.secondary,
                    disabled=current_page <= 0,
                    row=4,
                    custom_id=(f"boss_timer_voice_page:{current_page}:previous" if persistent else None),
                )
                next_button = self.discord.ui.Button(
                    label="다음 ▶",
                    style=self.discord.ButtonStyle.secondary,
                    disabled=current_page >= page_count - 1,
                    row=4,
                    custom_id=(f"boss_timer_voice_page:{current_page}:next" if persistent else None),
                )

                async def previous_page(interaction: Any) -> None:
                    if not await self._require_soundboard_channel(interaction):
                        return
                    if owner_id and str(getattr(getattr(interaction, "user", None), "id", "") or "") != owner_id:
                        await interaction.response.send_message("이 음성 목록을 연 사용자만 누를 수 있습니다.", ephemeral=True)
                        return
                    render_page(current_page - 1)
                    await interaction.response.edit_message(view=view)

                async def next_page(interaction: Any) -> None:
                    if not await self._require_soundboard_channel(interaction):
                        return
                    if owner_id and str(getattr(getattr(interaction, "user", None), "id", "") or "") != owner_id:
                        await interaction.response.send_message("이 음성 목록을 연 사용자만 누를 수 있습니다.", ephemeral=True)
                        return
                    render_page(current_page + 1)
                    await interaction.response.edit_message(view=view)

                previous_button.callback = previous_page
                next_button.callback = next_page
                view.add_item(previous_button)
                view.add_item(next_button)

        render_page(0)
        return view

    def _get_discord_voice_channel_menu_entries(self, guild: Any) -> list[tuple[str, str]]:
        channels = list(getattr(guild, "voice_channels", []) or [])
        entries: list[tuple[str, str]] = []
        for channel in sorted(channels, key=lambda item: (str(getattr(item, "name", "")).casefold(), int(getattr(item, "id", 0) or 0))):
            channel_id = str(getattr(channel, "id", "") or "").strip()
            channel_name = str(getattr(channel, "name", "") or "").strip()
            if channel_id.isdigit() and channel_name:
                entries.append((channel_id, channel_name))
        return entries

    async def _queue_discord_reconnect_request_from_interaction(
        self,
        interaction: Any,
        *,
        voice_channel_id: str,
        raw_text: str,
    ) -> None:
        author = getattr(interaction, "user", None)
        text_channel = getattr(interaction, "channel", None)
        await self._queue_local_schedule_request(
            {
                "operation": "discord_reconnect",
                "request_id": uuid.uuid4().hex,
                "received_at": datetime.now().isoformat(timespec="seconds"),
                "channel_id": str(getattr(text_channel, "id", "") or ""),
                "author_id": str(getattr(author, "id", "") or ""),
                "author_name": str(
                    getattr(author, "display_name", "") or getattr(author, "name", "") or "사용자"
                ),
                "server_id": self._get_configured_server_id(),
                "voice_channel_id": voice_channel_id,
                "raw_text": raw_text,
                "target_description": "음성채널 UI에서 지정한 채널",
            }
        )

    def _build_discord_voice_channel_panel_view(self, guild: Any) -> Any:
        """Build the pinned, always-visible button-grid soundboard."""
        del guild  # The entries are server-scoped when the bot instance is started.
        return self._build_discord_voice_command_menu_view(persistent=True)

    def _get_current_voice_channel_name(self) -> str:
        current_channel = getattr(getattr(self, "voice_client", None), "channel", None)
        current_channel_id = str(
            getattr(current_channel, "id", "") or self.config.get("voice_channel_id") or ""
        ).strip()
        current_channel_name = str(getattr(current_channel, "name", "") or "").strip()
        if current_channel_name:
            return current_channel_name
        configured_guild_id = self._get_configured_server_id()
        guild = self.client.get_guild(int(configured_guild_id)) if configured_guild_id.isdigit() else None
        if guild is not None:
            for channel_id, channel_name in self._get_discord_voice_channel_menu_entries(guild):
                if channel_id == current_channel_id:
                    return channel_name
        return "음성채널 미연결"

    def _text_channel_keep_count(self) -> int:
        try:
            count = int(self.config.get("text_channel_keep_count", "2"))
            return count if 0 <= count <= 50 else 2
        except (ValueError, TypeError):
            return 2

    async def _set_text_channel_keep_count(self, count: int, guild: Any) -> str:
        if not 0 <= count <= 50:
            raise ValueError("메시지 보관 개수는 0~50이어야 합니다.")
        if save_config_value("text_channel_keep_count", str(count)) is False:
            return "메시지 보관 설정을 저장하지 못했습니다. 기존 설정을 유지합니다."
        self.config["text_channel_keep_count"] = str(count)
        channel = await self._resolve_text_channel(guild)
        if channel is not None:
            await self._cleanup_bot_text_channel_messages(channel)
        label = "제한 없음" if count == 0 else f"최근 {count}개"
        return f"보탐매니저 메시지 보관을 {label}으로 설정했습니다. 사용자 글과 고정 메시지는 유지합니다."

    async def _cleanup_bot_text_channel_messages(self, channel: Any, *, keep_count: int | None = None) -> None:
        # Commands and announcements can clean up the same channel concurrently.
        # Serialize cleanup with box updates so a newly updated box stays safe.
        async with self.boss_notice_lock:
            await self._cleanup_bot_text_channel_messages_locked(channel, keep_count=keep_count)

    async def _cleanup_bot_text_channel_messages_locked(self, channel: Any, *, keep_count: int | None = None) -> None:
        """Keep the channel compact without ever deleting user or pinned messages."""
        if keep_count is None:
            keep_count = self._text_channel_keep_count()
        if keep_count == 0:
            return  # Unlimited: do not even request message history.
        if channel is None or not hasattr(channel, "history"):
            return
        bot_user_id = str(getattr(getattr(self.client, "user", None), "id", "") or "")
        if not bot_user_id:
            return
        retained = 0
        protected_panel_id = str(getattr(self, "voice_channel_panel_message_id", "") or "")
        now = time.time()
        groups_by_message = {
            str(group.get('message_id') or ''): group
            for group in self.boss_notice_groups.values()
            if float(group.get('expires_at') or 0) > now
        }
        try:
            candidates = []
            from discord_authority_events import is_control_message
            async for message in channel.history(limit=None):
                author_id = str(getattr(getattr(message, "author", None), "id", "") or "")
                message_id = str(getattr(message, "id", "") or "")
                if author_id != bot_user_id or bool(getattr(message, "pinned", False)) or message_id == protected_panel_id:
                    continue
                if is_control_message(message):
                    continue  # Control traffic has its own lifetime, not notice retention.
                group = groups_by_message.get(message_id)
                if group and not group.get('completed') and float(group.get('target_at') or 0) + 15 > now:
                    continue  # Keep an active boss's earlier box for its next stage.
                updated_at = float(group.get('updated_at') or 0) if group else 0
                created_at = getattr(message, 'created_at', None)
                candidates.append((max(updated_at, created_at.timestamp() if isinstance(created_at, datetime) else 0), message))
            # Editing a boss box does not change its Discord creation ID. Count
            # its latest announcement as recent activity when applying retention.
            candidates.sort(key=lambda item: (item[0], int(getattr(item[1], 'id', 0))), reverse=True)
            for _activity_at, message in candidates:
                if retained < max(0, int(keep_count)):
                    retained += 1
                    continue
                try:
                    if not self._send_allowed():
                        return
                    await message.delete()
                    log(f"text_channel_cleanup_deleted channel_id={getattr(channel, 'id', '')} "
                        f"message_id={message.id} at={time.time():.3f}")
                    for key, group in list(self.boss_notice_groups.items()):
                        if str(group.get('message_id') or '') == str(getattr(message, 'id', '')):
                            self.boss_notice_groups.pop(key, None)
                except Exception:
                    continue
        except Exception as exc:
            log(f"text_channel_cleanup_failed channel_id={getattr(channel, 'id', '')} error={exc}")

    async def _cleanup_voice_panel_channel_messages(self, channel: Any) -> None:
        if not self._send_allowed():
            return False
        """Keep a dedicated soundboard channel readable: panel + one newest message."""
        if channel is None or not hasattr(channel, "history"):
            return
        bot_user_id = str(getattr(getattr(self.client, "user", None), "id", "") or "")
        protected_panel_id = str(getattr(self, "voice_channel_panel_message_id", "") or "")
        if not bot_user_id or not protected_panel_id:
            return
        retained_messages = 0
        try:
            async for message in channel.history(limit=80):
                message_id = str(getattr(message, "id", "") or "")
                if message_id == protected_panel_id or bool(getattr(message, "pinned", False)):
                    continue
                if retained_messages < 1:
                    retained_messages += 1
                    continue
                try:
                    await message.delete()
                except Exception:
                    # The panel still works without Manage Messages; Discord simply
                    # keeps the old message in that case.
                    continue
        except Exception as exc:
            log(f"voice_panel_channel_cleanup_failed channel_id={getattr(channel, 'id', '')} error={exc}")

    async def _cleanup_voice_panel_after_activity(self, channel: Any) -> None:
        if not self._send_allowed():
            return False
        """Let Discord post an acknowledgement, then compact the soundboard channel."""
        if channel is None:
            return
        configured_panel_channel_id = str(self.config.get("voice_panel_channel_id") or "").strip()
        if configured_panel_channel_id != str(getattr(channel, "id", "") or ""):
            return
        resolved = await self._resolve_voice_panel_channel(getattr(channel, "guild", None))
        if resolved is None or resolved.id != channel.id:
            return
        # A local request can add its own notice just after the component
        # interaction.  Waiting briefly makes one cleanup cover both messages.
        await asyncio.sleep(1.5)
        await self._cleanup_voice_panel_channel_messages(channel)

    @staticmethod
    async def _delete_interaction_response_after_delay(interaction: Any, delay_seconds: float = 5.0) -> None:
        """Remove a short-lived ephemeral command result without user action."""
        await asyncio.sleep(max(0.0, float(delay_seconds)))
        try:
            await interaction.delete_original_response()
        except Exception:
            # Discord can already have expired or dismissed an ephemeral response.
            pass

    async def _publish_discord_voice_channel_panel(self, channel: Any, guild: Any) -> tuple[bool, str]:
        if not self._send_allowed():
            return False, "관리자 권한이 없습니다."
        target = await self._resolve_voice_panel_channel(guild)
        if target is None or target.id != getattr(channel, "id", None):
            return False, "사운드보드 전용 채팅채널을 먼저 지정하세요."
        view = self._build_discord_voice_channel_panel_view(guild)
        if view is None:
            return False, "선택할 음성채널이 없습니다."
        panel_title = "🔊 보탐매니저 음성 사운드보드"
        current_voice_channel_name = self._get_current_voice_channel_name()
        entry_count = len(self._get_discord_voice_command_menu_entries())
        panel_content = (
            f"{panel_title}\n"
            f"현재 송출 음성채널: **{current_voice_channel_name}**\n"
            f"등록 음성 {entry_count}개 · 버튼을 누르면 즉시 송출합니다. "
            "이 메시지는 고정되어 유지됩니다."
        )
        panel_message = None
        bot_user_id = str(getattr(getattr(self.client, "user", None), "id", "") or "")
        panel_titles = (panel_title, "🔊 보탐매니저 음성채널")
        known_panel_ids: set[str] = set()
        saved_panel_id = str(
            self.voice_channel_panel_message_id or self.config.get("voice_panel_message_id") or ""
        ).strip()
        if saved_panel_id.isdigit() and hasattr(channel, "fetch_message"):
            try:
                candidate = await channel.fetch_message(int(saved_panel_id))
                if (
                    str(getattr(getattr(candidate, "author", None), "id", "") or "") == bot_user_id
                    and str(getattr(candidate, "content", "") or "").startswith(panel_titles)
                ):
                    panel_message = candidate
                    known_panel_ids.add(saved_panel_id)
            except Exception:
                pass
        try:
            pinned_messages = await channel.pins()
        except Exception:
            pinned_messages = []
        for candidate in pinned_messages:
            if str(getattr(getattr(candidate, "author", None), "id", "") or "") != bot_user_id:
                continue
            if str(getattr(candidate, "content", "") or "").startswith(panel_titles):
                candidate_id = str(getattr(candidate, "id", "") or "")
                if panel_message is None:
                    panel_message = candidate
                    known_panel_ids.add(candidate_id)
                elif candidate_id not in known_panel_ids:
                    known_panel_ids.add(candidate_id)
                    try:
                        await candidate.unpin(reason="중복 보탐매니저 음성 사운드보드 정리")
                    except Exception:
                        pass
                    try:
                        await candidate.delete()
                    except Exception:
                        pass
        if hasattr(channel, "history"):
            try:
                async for candidate in channel.history(limit=80):
                    if str(getattr(getattr(candidate, "author", None), "id", "") or "") != bot_user_id:
                        continue
                    if not str(getattr(candidate, "content", "") or "").startswith(panel_titles):
                        continue
                    candidate_id = str(getattr(candidate, "id", "") or "")
                    if panel_message is None:
                        panel_message = candidate
                        known_panel_ids.add(candidate_id)
                    elif candidate_id not in known_panel_ids:
                        known_panel_ids.add(candidate_id)
                        try:
                            await candidate.unpin(reason="중복 보탐매니저 음성 사운드보드 정리")
                        except Exception:
                            pass
                        try:
                            await candidate.delete()
                        except Exception:
                            pass
            except Exception as exc:
                log(f"voice_panel_history_lookup_failed channel_id={getattr(channel, 'id', '')} error={exc}")
        try:
            if panel_message is not None:
                if not self._send_allowed():
                    return False, "관리자 권한이 변경되었습니다."
                await panel_message.edit(content=panel_content, view=view)
            else:
                if not self._send_allowed():
                    return False, "관리자 권한이 변경되었습니다."
                panel_message = await channel.send(panel_content, view=view)
                try:
                    await panel_message.pin(reason="보탐매니저 음성채널 선택 UI")
                except Exception:
                    pass
            self.voice_channel_panel_message_id = str(getattr(panel_message, "id", "") or "")
            self.config["voice_panel_message_id"] = self.voice_channel_panel_message_id
            save_config_value("voice_panel_message_id", self.voice_channel_panel_message_id)
            await self._remove_old_schedule_channel_panel(guild, channel, saved_panel_id)
            configured_panel_channel_id = str(self.config.get("voice_panel_channel_id") or "").strip()
            if configured_panel_channel_id == str(getattr(channel, "id", "") or ""):
                await self._cleanup_voice_panel_channel_messages(channel)
            return True, "음성 사운드보드 버튼 UI를 고정했습니다."
        except Exception as exc:
            log(f"voice_channel_panel_publish_failed channel_id={getattr(channel, 'id', '')} error={exc}")
            return False, "음성채널 UI를 표시하지 못했습니다."

    async def _remove_old_schedule_channel_panel(self, guild: Any, new_channel: Any, old_id: str) -> None:
        """Remove only the exact saved, bot-authored legacy panel after publishing its replacement."""
        if not old_id.isdigit() or old_id == self.voice_channel_panel_message_id:
            return
        channel = await self._resolve_text_channel(guild)
        if channel is None or channel.id == new_channel.id or not hasattr(channel, "fetch_message"):
            return
        try:
            old = await channel.fetch_message(int(old_id))
            if (getattr(getattr(old, "author", None), "id", None) != self.client.user.id
                    or not str(getattr(old, "content", "")).startswith(
                        ("🔊 보탐매니저 음성 사운드보드", "🔊 보탐매니저 음성채널"))):
                return
            await old.delete()
        except Exception as exc:
            log(f"legacy_voice_panel_remove_failed message_id={old_id} error={exc}")

    async def _initialize_voice_panel_after_ready(self) -> None:
        try:
            text_channel = await asyncio.wait_for(self._resolve_voice_panel_channel(), timeout=5.0)
            server_id = self._get_configured_server_id()
            configured_guild = self.client.get_guild(int(server_id)) if server_id else None
            if text_channel is None or configured_guild is None:
                log(
                    f"voice_channel_panel_startup_skipped guild_id={server_id} "
                    f"configured_channel_id={self.config.get('voice_panel_channel_id', '')} "
                    "reason=dedicated_channel_not_found use=/음성채널"
                )
                return
            ok, result = await asyncio.wait_for(
                self._publish_discord_voice_channel_panel(text_channel, configured_guild),
                timeout=12.0,
            )
            log(f"voice_channel_panel_startup_result channel_id={text_channel.id} ok={int(ok)} result={result}")
        except asyncio.TimeoutError:
            log("voice_channel_panel_startup_timeout")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log(f"voice_channel_panel_startup_failed error={exc}")

    def _should_handle_interaction(self, interaction: Any) -> bool:
        guild_id = str(getattr(interaction, "guild_id", "") or "").strip()
        if self._is_configured_server_id(guild_id):
            if not self._send_allowed():
                return False
            self._tag_authority_task()
            return True
        command = getattr(interaction, "command", None)
        command_name = str(getattr(command, "name", "") or "unknown")
        log(
            f"interaction_ignored_wrong_guild command={command_name} guild_id={guild_id or '-'} "
            f"configured_guild_id={self._get_configured_server_id() or '-'}"
        )
        # 동일 봇 토큰의 다른 PC 인스턴스가 이 서버를 담당한다. 여기서
        # 응답하면 담당 인스턴스와 Interaction 응답이 충돌하므로 조용히 무시한다.
        return False

    def _bind_events(self) -> None:
        @self.client.event
        async def on_ready() -> None:
            self.gateway_disconnected_at = None
            STATUS.update(online=True, connected_at=STATUS.connected_at if STATUS.online else time.time(), last_error="",
                          bot_user_id=str(getattr(self.client.user, "id", "")))
            self.connection_policy.success()
            log(f"discord_gateway_ready user={self.client.user}")
            if self.disconnect_only:
                disconnected = await self._disconnect_stale_configured_voice_session(wait_seconds=1.0, force=True)
                self._write_disconnect_only_result(disconnected)
                log(f"discord_disconnect_only_complete disconnected={int(disconnected)}")
                STATUS.update(online=False, voice_connected=False)
                STATUS.shutdown_requested.set()
                await self.client.close()
                return
            # Do not force-disconnect a possibly live administrator's session.
            # Automatic retries have one shared, persistent budget.
            # Gateway login never requests voice or administrator ownership.
            # Start the bridge and shutdown watchers before any Discord message
            # maintenance. Fetching/deleting the persistent soundboard panel can
            # be delayed by Discord rate limits; it must never block heartbeats
            # or graceful shutdown.
            if self.schedule_task is None or self.schedule_task.done():
                self.schedule_task = self.client.loop.create_task(self._schedule_loop())
            if self.play_task is None or self.play_task.done():
                self.play_task = self.client.loop.create_task(self._play_loop())
            if self.shutdown_task is None or self.shutdown_task.done():
                self.shutdown_task = self.client.loop.create_task(self._shutdown_watch_loop())
            if self.authority_watch_task is None or self.authority_watch_task.done():
                self.authority_watch_task = self.client.loop.create_task(self._authority_watch_loop())
            if self.gateway_recovery_task is None or self.gateway_recovery_task.done():
                self.gateway_recovery_task = self.client.loop.create_task(self._gateway_recovery_loop())
            log(f"discord_ready user={self.client.user} edge_tts_gain={EDGE_TTS_PLAYBACK_GAIN:.2f}")
            await self._queue_authority_session_event()


        @self.client.event
        async def on_disconnect() -> None:
            if self.gateway_disconnected_at is None:
                self.gateway_disconnected_at = time.monotonic()
            STATUS.update(online=False, voice_connected=False)
            log("discord_disconnected")
            self.connection_policy.update(authority_needs_sync=True, authority_until=0)

        @self.client.event
        async def on_resumed() -> None:
            self.gateway_disconnected_at = None
            STATUS.update(online=True, connected_at=STATUS.connected_at if STATUS.online else time.time(), last_error="")
            await self._queue_authority_session_event()
            log("discord_gateway_resumed")

        @self.client.event
        async def on_voice_state_update(member: Any, before: Any, after: Any) -> None:
            bot_user_id = str(getattr(getattr(self.client, "user", None), "id", "") or "")
            if not bot_user_id or str(getattr(member, "id", "") or "") != bot_user_id:
                return
            # The same bot receives its voice events from every joined guild.
            # A foreign guild's join/leave must never change this PC's config,
            # health status or voice panel (including channel=None on leave).
            if not self._is_configured_guild(getattr(member, "guild", None)):
                return
            if (self._connection_is_blocked() or self.voice_client is None
                    or not self.voice_client.is_connected()
                    or getattr(self.voice_client, "authority_generation", None)
                    != self._connection_state()["authority_generation"]):
                return
            after_channel = getattr(after, "channel", None)
            if after_channel is not None and not self._is_configured_guild(getattr(after_channel, "guild", None)):
                return
            after_channel_id = str(getattr(after_channel, "id", "") or "").strip()
            if not after_channel_id.isdigit():
                STATUS.update(voice_connected=False)
                return
            # 관리자가 디스코드에서 봇을 직접 다른 채널로 옮긴 경우가
            # 재시작 뒤 이전 채널로 되돌아가지 않도록 실제 위치를 저장한다.
            previous_channel_id = str(self.config.get("voice_channel_id") or "").strip()
            self.config["voice_channel_id"] = after_channel_id
            if previous_channel_id != after_channel_id:
                save_config_value("voice_channel_id", after_channel_id)
                log(f"voice_channel_moved_externally from={previous_channel_id or '-'} to={after_channel_id}")
            STATUS.update(
                guild_id=str(getattr(getattr(after_channel, "guild", None), "id", "") or self._get_configured_server_id()),
                voice_channel_id=after_channel_id,
                voice_connected=True,
                last_error="",
            )
            try:
                text_channel = await self._resolve_voice_panel_channel(getattr(after_channel, "guild", None))
                if text_channel is not None:
                    await self._publish_discord_voice_channel_panel(text_channel, getattr(after_channel, "guild", None))
            except Exception as exc:
                log(f"voice_channel_panel_move_refresh_failed error={exc}")

        @self.client.event
        async def on_raw_message_edit(payload: Any) -> None:
            # Observe only our tracked notices. Never rewrite a message here:
            # compare these server events with the outbound signature to tell
            # content changes from a Discord client-only redraw.
            message_id = str(payload.message_id)
            if not any(str(group.get('message_id') or '') == message_id
                       for group in self.boss_notice_groups.values()):
                return
            data = payload.data
            signature = (self._boss_notice_text_signature(data['embeds'])
                         if 'embeds' in data else '-')
            log(f"text_notice_gateway_edit message_id={message_id} at={time.time():.3f} "
                f"signature={signature} has_content={int('content' in data)} "
                f"content_length={len(str(data.get('content') or ''))}")

        @self.client.event
        async def on_message(message: Any) -> None:
            if await self.authority_events.receive(message):
                return
            if not self._send_allowed():
                return
            self._tag_authority_task()
            asyncio.create_task(
                self._cleanup_voice_panel_after_activity(getattr(message, "channel", None))
            )
            await self._handle_schedule_text_message(message)

    async def _queue_authority_session_event(self):
        await self._queue_local_schedule_request(dict(
            operation="administrator_session", request_id="session_" + uuid.uuid4().hex,
            server_id=self._get_configured_server_id(), runtime_id=STATUS.runtime_id,
            created_at=time.time(), raw_text="봇 접속·연결 복구 확인"))

    def _write_disconnect_only_result(self, disconnected: bool) -> None:
        result_path_text = str(getattr(self, "disconnect_only_result_path", "") or "").strip()
        if not result_path_text:
            return
        result_path = Path(result_path_text)
        temporary_path = result_path.with_suffix(result_path.suffix + ".tmp")
        try:
            result_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps({"ok": bool(disconnected), "pid": os.getpid()}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(temporary_path, result_path)
        except OSError as exc:
            log(f"discord_disconnect_only_result_failed error={exc}")
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _handle_schedule_text_message(self, message: Any) -> None:
        if not self.message_content_enabled or not self._send_allowed():
            return
        author = getattr(message, "author", None)
        if author is None or bool(getattr(author, "bot", False)):
            return
        guild = getattr(message, "guild", None)
        if guild is None:
            return
        if not self._is_configured_guild(guild):
            return
        message_channel = getattr(message, "channel", None)
        if message_channel is None:
            return
        content = str(getattr(message, "content", "") or "").strip()
        voice_command = self._resolve_discord_voice_command(content)
        if voice_command is not None:
            target_channel = await self._resolve_voice_panel_channel(guild)
            if target_channel is None or target_channel.id != message_channel.id:
                return
            command_name, tts_text = voice_command
            parsed: dict[str, Any] = {
                "operation": "voice_play",
                "voice_command": command_name,
                "tts_text": tts_text,
                "raw_text": content,
            }
            reaction = "🔊"
        else:
            target_channel = await self._resolve_text_channel(guild)
            if target_channel is None or target_channel.id != message_channel.id:
                return
            retention_command = re.fullmatch(r"/보탐\s+(.+)", content)
            if retention_command:
                value = retention_command.group(1).strip()
                if value == "?":
                    from discord_command_help import COMMAND_HELP
                    result = COMMAND_HELP
                elif value in {"초읽기", "초읽기해제"}:
                    result = await self._queue_countdown_control(
                        value == "초읽기", author, message_channel, raw_text=content)
                elif re.fullmatch(r"[0-9]{1,2}", value) and int(value) <= 50:
                    result = await self._set_text_channel_keep_count(int(value), guild)
                else:
                    result = "사용법: /보탐 초읽기, /보탐 초읽기해제, /보탐 0~50 (0은 제한 없음). 접속은 슬래시 명령을 사용하세요."
                await message.reply(result, mention_author=False)
                await self._cleanup_bot_text_channel_messages(message_channel)
                return
            parsed = parse_discord_schedule_message(content) or {}
            if not parsed:
                return
            reaction = "✅"
        request_payload = {
            **parsed,
            "request_id": uuid.uuid4().hex,
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "message_id": str(getattr(message, "id", "") or ""),
            "channel_id": str(getattr(message_channel, "id", "") or ""),
            "author_id": str(getattr(author, "id", "") or ""),
            "author_name": str(getattr(author, "display_name", "") or getattr(author, "name", "") or "사용자"),
            "server_id": self._get_configured_server_id(),
        }
        try:
            await self._queue_local_schedule_request(request_payload)
            try:
                await message.add_reaction(reaction)
            except Exception:
                pass
            log(
                f"schedule_text_request_queued operation={parsed.get('operation')} "
                f"message_id={request_payload['message_id']} author_id={request_payload['author_id']}"
            )
        except OSError as exc:
            log(f"schedule_text_request_write_failed error={exc}")
            try:
                await message.reply("로컬 보스타이머에 스케쥴 요청을 전달하지 못했습니다.", mention_author=False)
            except Exception:
                pass

    async def _gateway_recovery_loop(self) -> None:
        """Recover a stuck gateway or voice connection without waiting for a new alert."""
        while not STATUS.shutdown_requested.is_set():
            await asyncio.sleep(2.0)
            if self.client.is_ready():
                if self._connection_is_blocked() or not self._connection_state()["authority_active"]:
                    continue
                self.gateway_disconnected_at = None
                STATUS.update(online=True)
                voice_is_connected = bool(
                    self.voice_client is not None
                    and getattr(self.voice_client, "is_connected", lambda: False)()
                )
                if voice_is_connected:
                    STATUS.update(voice_connected=True)
                    continue
                now_value = time.monotonic()
                if now_value - self.last_voice_reconnect_attempt_at < 5.0:
                    continue
                self.last_voice_reconnect_attempt_at = now_value
                reconnected = await self._connect_configured_voice_channel()
                if reconnected is True:
                    await self._publish_voice_reconnect_recovery_log()
                continue

            disconnected_at = self.gateway_disconnected_at
            if disconnected_at is None:
                self.gateway_disconnected_at = time.monotonic()
                continue
            disconnected_seconds = time.monotonic() - disconnected_at
            if disconnected_seconds < 20.0:
                continue
            STATUS.update(
                online=False,
                voice_connected=False,
                last_error="디스코드 연결이 20초 이상 복구되지 않아 봇을 자동 재시작합니다.",
            )
            log(f"discord_gateway_recovery_restart disconnected_sec={disconnected_seconds:.1f}")
            await self.client.close()
            return

    async def _queue_countdown_control(self, enabled: bool, author: Any, channel: Any, *, raw_text: str) -> str:
        payload = {
            "operation": "countdown_control", "enabled": enabled,
            "request_id": uuid.uuid4().hex,
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "server_id": self._get_configured_server_id(),
            "channel_id": str(getattr(channel, "id", "") or ""),
            "author_id": str(getattr(author, "id", "") or ""),
            "author_name": str(getattr(author, "display_name", "") or getattr(author, "name", "") or "사용자"),
            "raw_text": raw_text,
        }
        try:
            await self._queue_local_schedule_request(payload)
        except OSError as exc:
            log(f"countdown_control_queue_failed error={exc}")
            return "초읽기 설정 요청을 전달하지 못했습니다. 로컬 보탐매니저 상태를 확인하세요."
        label = "켜기" if enabled else "해제"
        log(f"countdown_control_queued request_id={payload['request_id']} enabled={int(enabled)}")
        return f"초읽기 {label} 요청을 보탐매니저에 전달했습니다. 적용 결과는 프로그램의 디스코드 요청 기록에서 확인할 수 있습니다."

    async def _queue_local_schedule_request(self, payload: dict[str, Any]) -> None:
        operation = str(payload.get("operation") or "")
        if not operation.startswith("administrator_") and operation not in {"readonly_query_route", "readonly_query_wait"}:
            if not self._send_allowed():
                return
            payload.update(runtime_id=STATUS.runtime_id,
                           authority_generation=self._connection_state()["authority_generation"])
        request_id = re.sub(r"[^0-9A-Za-z_-]", "", str(payload.get("request_id") or "")) or uuid.uuid4().hex
        timestamp = int(time.time() * 1000)
        async with self.schedule_request_lock:
            DISCORD_SCHEDULE_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
            final_path = DISCORD_SCHEDULE_REQUEST_DIR / f"{timestamp:013d}_{request_id}.json"
            temporary_path = DISCORD_SCHEDULE_REQUEST_DIR / f".{final_path.name}.tmp"
            try:
                with temporary_path.open("w", encoding="utf-8") as request_file:
                    json.dump(payload, request_file, ensure_ascii=False, separators=(",", ":"))
                os.replace(temporary_path, final_path)
            finally:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    async def _publish_voice_reconnect_recovery_log(self) -> None:
        if not self._send_allowed():
            return False
        message = "보탐매니저 음성 연결이 끊어져 자동 재접속했습니다."
        try:
            channel = await self._resolve_text_channel()
        except Exception as exc:
            channel = None
            log(f"voice_reconnect_text_channel_resolve_failed error={exc}")
        if channel is not None:
            try:
                embed = self.discord.Embed(
                    title="⚠ 보탐매니저 연결 로그",
                    description=message,
                    color=0xF59E0B,
                    timestamp=datetime.now(),
                )
                embed.set_footer(text="BossTimer 음성 연결 자동 복구")
                if not self._send_allowed():
                    return
                await channel.send(embed=embed)
                await self._cleanup_bot_text_channel_messages(channel)
                log(
                    f"voice_reconnect_text_notice_sent channel_id={getattr(channel, 'id', '')}"
                )
            except Exception as exc:
                log(f"voice_reconnect_text_notice_failed error={exc}")

        bot_user = getattr(self.client, "user", None)
        request_payload = {
            "operation": "connection_log",
            "request_id": uuid.uuid4().hex,
            "received_at": datetime.now().isoformat(timespec="seconds"),
            "channel_id": str(getattr(channel, "id", "") or self.config.get("text_channel_id") or ""),
            "author_id": str(getattr(bot_user, "id", "") or ""),
            "author_name": "보탐매니저",
            "server_id": self._get_configured_server_id(),
            "voice_channel_id": str(self.config.get("voice_channel_id") or "").strip(),
            "raw_text": message,
        }
        try:
            await self._queue_local_schedule_request(request_payload)
            log(
                f"voice_reconnect_local_log_queued request_id={request_payload['request_id']}"
            )
        except OSError as exc:
            log(f"voice_reconnect_local_log_failed error={exc}")

    def _resolve_invite_voice_channel(
        self,
        interaction: Any,
        requested_target: str,
    ) -> tuple[str, str]:
        target_text = str(requested_target or "").strip()
        normalized_target = re.sub(r"\s+", "", target_text).casefold()
        if normalized_target in {"보탐매니저", "보탐"}:
            user = getattr(interaction, "user", None)
            voice_state = getattr(user, "voice", None)
            caller_channel = getattr(voice_state, "channel", None)
            caller_channel_id = str(getattr(caller_channel, "id", "") or "").strip()
            if caller_channel_id.isdigit():
                return caller_channel_id, "호출한 사용자가 참여 중인 음성채널"
            configured_channel_id = str(self.config.get("default_voice_channel_id") or self.config.get("voice_channel_id") or "").strip()
            if configured_channel_id.isdigit():
                return configured_channel_id, "설정에 저장된 기본 음성채널"
            return "", "호출한 사용자가 음성채널에 없고 기본 음성채널도 설정되지 않았습니다."
        if target_text.isdigit():
            return target_text, "직접 지정한 음성채널"
        return "", "보탐매니저, 보탐 또는 음성채널 ID를 입력하세요."

    async def _allow_connection_control(self, interaction):
        if not self._should_handle_interaction(interaction):
            return False
        permissions = getattr(getattr(interaction, "user", None), "guild_permissions", None)
        if not (getattr(permissions, "administrator", False) or getattr(permissions, "manage_guild", False)):
            await interaction.response.send_message("서버 관리 권한이 있는 관리자만 접속·대기를 변경할 수 있습니다.", ephemeral=True)
            return False
        if self._connection_state().get("handover_hold"):
            await interaction.response.send_message("관리자 인계 진행 중입니다. 완료 후 다시 요청하세요.", ephemeral=True)
            return False
        if STATUS.standby:
            await interaction.response.send_message("이 관리자는 대기 전환 중입니다. 프로그램에서 수동 실행하세요.", ephemeral=True)
            return False
        return True

    async def _join_from_interaction(self, interaction, target="보탐"):
        if not self._should_handle_interaction(interaction):
            return
        channel_id, description = self._resolve_invite_voice_channel(interaction, target)
        if not channel_id:
            await interaction.response.send_message(description, ephemeral=True)
            return
        await self._administrator_command(interaction, "administrator_connect", target=STATUS.client_id, channel=channel_id)

    async def _standby_from_interaction(self, interaction):
        if self._should_handle_interaction(interaction):
            await self._administrator_command(interaction, "administrator_release")

    def _bind_commands(self) -> None:
        @self.tree.command(name="대기", description="송출 권한을 반납하고 음성채널에서 나옵니다. 봇 연결은 유지합니다.")
        async def standby(interaction: Any) -> None:
            await self._standby_from_interaction(interaction)

        @self.tree.command(name="초대", description="보탐매니저를 현재 음성채널로 불러오고 재접속합니다.")
        async def invite(interaction: Any, voice_channel_id: str) -> None:
            await self._join_from_interaction(interaction, voice_channel_id)

        @self.tree.command(name="음성채널", description="음성 사운드보드 버튼을 표시할 텍스트 채널을 지정합니다.")
        async def select_voice_channel(interaction: Any, 채널이름: str = "") -> None:
            if not self._should_handle_interaction(interaction):
                return
            guild = getattr(interaction, "guild", None)
            current_text_channel = getattr(interaction, "channel", None)
            if current_text_channel is None or guild is None or not hasattr(current_text_channel, "send"):
                await interaction.response.send_message("서버의 텍스트 채널에서 실행하세요.", ephemeral=True)
                return
            requested_name = str(채널이름 or "").strip()
            text_channel = (
                self._find_text_channel_by_name(guild, requested_name)
                if requested_name
                else current_text_channel
            )
            if text_channel is None:
                await interaction.response.send_message(
                    f"텍스트 채널 `{requested_name}`을 찾지 못했습니다. 채널 이름, #채널이름 또는 채널 ID를 입력하세요.",
                    ephemeral=True,
                )
                return
            channel_id = str(getattr(text_channel, "id", "") or "").strip()
            notice_channel = await self._resolve_text_channel(guild)
            if notice_channel is not None and notice_channel.id == text_channel.id:
                await interaction.response.send_message(
                    "보탐매니저 안내 채널과 사운드보드 채널은 따로 지정하세요.", ephemeral=True)
                return
            if not channel_id.isdigit():
                await interaction.response.send_message("텍스트 채널 ID를 확인할 수 없습니다.", ephemeral=True)
                return
            self.config["voice_panel_channel_id"] = channel_id
            save_config_value("voice_panel_channel_id", channel_id)
            await interaction.response.defer(ephemeral=True, thinking=True)
            ok, result_text = await self._publish_discord_voice_channel_panel(text_channel, guild)
            await interaction.followup.send(
                (
                    f"{getattr(text_channel, 'mention', '#음성패널')}에 {result_text}\n"
                    "이 채널은 음성 버튼 패널과 가장 최근 메시지 1개만 남기도록 정리합니다."
                    if ok
                    else f"{result_text}\n/음성채널을 다시 실행해 보세요."
                ),
                ephemeral=True,
            )

        @self.tree.command(name="보탐채널", description="현재 텍스트 채널을 보탐매니저 안내 채널로 지정합니다.")
        async def set_bosstimer_text_channel(interaction: Any) -> None:
            if not self._should_handle_interaction(interaction):
                return
            channel = getattr(interaction, "channel", None)
            if channel is None or not hasattr(channel, "send"):
                await interaction.response.send_message("텍스트 채널에서 실행하세요.", ephemeral=True)
                return
            channel_id = str(getattr(channel, "id", "") or "")
            if channel_id == str(self.config.get("voice_panel_channel_id") or ""):
                await interaction.response.send_message(
                    "사운드보드 채널과 보탐매니저 안내 채널은 따로 지정하세요.", ephemeral=True)
                return
            if not channel_id.isdigit():
                await interaction.response.send_message("채널 ID를 확인할 수 없습니다.", ephemeral=True)
                return
            self.config["text_channel_id"] = channel_id
            save_config_value("text_channel_id", channel_id)
            await interaction.response.send_message(
                f"이 채널({getattr(channel, 'mention', '#보탐매니저')})을 보탐매니저 안내 채널로 저장했습니다.",
                ephemeral=True,
            )

        @self.tree.command(name="보탐", description="스케쥴 조회 / 담당자 이름으로 접속 / 초읽기 / 메시지 보관 0~50")
        @self.discord.app_commands.describe(동작="비우면 조회. 관리자(목록), 담당자 이름(예: 나츠), 접속, ?, 초읽기 또는 보관 개수 0~50")
        async def bosstimer_ansi(interaction: Any, 동작: str = "") -> None:
            동작 = 동작.strip()
            if 동작.startswith("관리자"):
                target = 동작[len("관리자"):].strip()
                await self._administrator_command(interaction, "administrator_connect" if target else "administrator_list", target=target)
                return
            if 동작 == "접속":
                await self._join_from_interaction(interaction)
                return
            # Names and bare IDs are administrator control even on standby.
            # Mutations need send authority; explicit queries have a private fallback.
            if 동작 and 동작 not in {"?", "초읽기", "초읽기해제"} and not re.fullmatch(r"[0-9]{1,2}", 동작):
                await self._administrator_command(interaction, "administrator_connect", target=동작)
                return
            if 동작 in {"", "?"}:
                await self._handle_readonly_query(interaction, "help" if 동작 == "?" else "schedule")
                return
            if not self._should_handle_interaction(interaction):
                return
            if 동작 in {"초읽기", "초읽기해제"}:
                await interaction.response.defer(ephemeral=True, thinking=True)
                result = await self._queue_countdown_control(
                    동작 == "초읽기", getattr(interaction, "user", None),
                    getattr(interaction, "channel", None), raw_text=f"/보탐 {동작}")
                await interaction.followup.send(result, ephemeral=True)
                return
            if 동작:
                if not re.fullmatch(r"[0-9]{1,2}", 동작) or int(동작) > 50:
                    await interaction.response.send_message(
                        "사용법: /보탐 (조회), /보탐 관리자 (목록), /보탐 나츠 (담당자 접속), /보탐 접속, /보탐 초읽기, /보탐 초읽기해제, /보탐 0~50 (0은 제한 없음)", ephemeral=True)
                    return
                await interaction.response.defer(ephemeral=True, thinking=True)
                result = await self._set_text_channel_keep_count(int(동작), getattr(interaction, "guild", None))
                await interaction.followup.send(result, ephemeral=True)
                return

        @self.tree.command(name="보탐텍스트", description="시간과 보스명만 있는 복사용 스케쥴 텍스트를 전송합니다.")
        async def bosstimer_plain_text(interaction: Any) -> None:
            await self._handle_readonly_query(interaction, "plain")

        @self.tree.command(name="음성추가", description="이름과 읽을 음성을 입력합니다. 기본 음성 이름은 덮어씁니다.")
        async def add_voice_command(interaction: Any, 이름: str, 음성: str = "") -> None:
            if not await self._require_soundboard_channel(interaction):
                return
            try:
                command_input = str(이름 or "").strip()
                voice_text = str(음성 or "").strip()
                # 새 슬래시 입력은 이름·음성을 별도 칸으로 받는다. 이미
                # 사용하던 "이름, 음성" 한 칸 문법도 그대로 호환한다.
                if voice_text:
                    command_input = f"{command_input}, {voice_text}"
                    added, result_text, command_name, tts_text = self._add_discord_voice_command(command_input)
                elif "," in command_input:
                    added, result_text, command_name, tts_text = self._add_discord_voice_command(command_input)
                else:
                    # 음성 칸을 비운 명령은 같은 이름의 WAV/MP4만 찾는다.
                    added, result_text, command_name, tts_text = self._add_discord_voice_command(
                        f"{command_input}, ",
                        allow_empty_tts=True,
                    )
            except OSError as exc:
                log(f"voice_command_add_save_failed error={exc}")
                await interaction.response.send_message(
                    f"음성 명령 저장에 실패했습니다: {exc}",
                    ephemeral=True,
                )
                asyncio.create_task(self._delete_interaction_response_after_delay(interaction))
                return
            if not added:
                await interaction.response.send_message(result_text, ephemeral=True)
                asyncio.create_task(self._delete_interaction_response_after_delay(interaction))
                return
            author = getattr(interaction, "user", None)
            channel = getattr(interaction, "channel", None)
            request_payload = {
                "operation": "voice_prepare",
                "voice_command": command_name,
                "tts_text": tts_text,
                "raw_text": f"/음성추가 {command_name}, {tts_text}",
                "request_id": uuid.uuid4().hex,
                "received_at": datetime.now().isoformat(timespec="seconds"),
                "channel_id": str(getattr(channel, "id", "") or ""),
                "author_id": str(getattr(author, "id", "") or ""),
                "author_name": str(
                    getattr(author, "display_name", "") or getattr(author, "name", "") or "사용자"
                ),
                "server_id": self._get_configured_server_id(),
            }
            try:
                await self._queue_local_schedule_request(request_payload)
                result_text += " (동일 이름 파일 확인 및 TTS 준비를 시작했습니다.)"
            except OSError as exc:
                log(f"voice_command_prepare_queue_failed error={exc}")
                result_text += " (등록은 완료했지만 로컬 TTS 준비 요청은 실패했습니다.)"
            asyncio.create_task(self._refresh_discord_voice_panel(getattr(interaction, "guild", None)))
            await interaction.response.send_message(result_text, ephemeral=True)
            asyncio.create_task(self._delete_interaction_response_after_delay(interaction))

        @self.tree.command(name="음성삭제", description="보탐매니저 음성 명령을 삭제합니다. 기본 음성도 삭제할 수 있습니다.")
        async def delete_voice_command(interaction: Any, 이름: str) -> None:
            if not await self._require_soundboard_channel(interaction):
                return
            try:
                _deleted, result_text = self._delete_discord_voice_command(이름)
            except OSError as exc:
                log(f"voice_command_delete_save_failed error={exc}")
                result_text = f"음성 명령 저장에 실패했습니다: {exc}"
            asyncio.create_task(self._refresh_discord_voice_panel(getattr(interaction, "guild", None)))
            await interaction.response.send_message(result_text, ephemeral=True)
            asyncio.create_task(self._delete_interaction_response_after_delay(interaction))

        @self.tree.command(name="음성목록", description="클릭해서 재생할 수 있는 보탐매니저 음성 목록을 엽니다.")
        async def voice_command_list(interaction: Any) -> None:
            await self._handle_readonly_query(interaction, "voices")

        @self.tree.command(name="음성", description="사운드보드 채팅채널에서 음성 버튼 목록을 엽니다.")
        async def voice_command_list_alias(interaction: Any) -> None:
            await voice_command_list.callback(interaction)

        @self.tree.command(name="이미지", description="로컬 스케쥴복사 설정 그대로 만든 이미지를 안내 채널에 전송합니다.")
        async def bosstimer_image(interaction: Any) -> None:
            await self._handle_readonly_query(interaction, "image")

    async def _sync_commands(self) -> None:
        try:
            generation = self._connection_state()["authority_generation"]
            server_id = self._get_configured_server_id()
            if not server_id:
                STATUS.update(last_error="서버 ID가 없어 명령어를 등록하지 않았습니다.")
                log("command_sync_skipped server_id_not_configured")
                return
            guild = self.discord.Object(id=int(server_id))
            if not self._connection_state()["command_sync_allowed"]:
                return
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            self.commands_registered = True
            self.command_generation = generation
            log(f"command_sync_complete guild_id={server_id} scope=guild channel_policy=2 commands=보탐[0~50,초읽기,초읽기해제],음성,음성목록")
        except Exception as exc:
            STATUS.update(last_error=f"명령어 동기화 실패: {exc}")
            log(f"command_sync_failed error={exc}")

    async def _require_soundboard_channel(self, interaction: Any) -> bool:
        if not self._should_handle_interaction(interaction):
            return False
        channel = await self._resolve_voice_panel_channel(getattr(interaction, "guild", None))
        current_id = str(getattr(getattr(interaction, "channel", None), "id", "") or "")
        if channel is not None and str(channel.id) == current_id:
            return True
        message = (
            f"사운드보드 명령은 <#{channel.id}> 채팅채널에서만 사용할 수 있습니다."
            if channel is not None else
            "사운드보드 채널이 없습니다. 전용 채팅채널에서 /음성채널을 먼저 실행하세요."
        )
        await interaction.response.send_message(message, ephemeral=True)
        return False

    async def _resolve_voice_panel_channel(self, guild: Any = None) -> Any | None:
        """Get the dedicated text channel that hosts the pinned soundboard."""
        configured_server_id = self._get_configured_server_id()
        if not configured_server_id:
            return None
        if guild is not None and not self._is_configured_guild(guild):
            return None
        target_guild = guild if guild is not None else self.client.get_guild(int(configured_server_id))
        if target_guild is None or not self._is_configured_guild(target_guild):
            return None
        configured_id = str(self.config.get("voice_panel_channel_id") or "").strip()
        notice_channel = await self._resolve_text_channel(target_guild)
        notice_id = str(getattr(notice_channel, "id", "") or "")
        if configured_id.isdigit() and configured_id != notice_id:
            channel = self.client.get_channel(int(configured_id))
            if channel is None:
                try:
                    channel = await self.client.fetch_channel(int(configured_id))
                except Exception:
                    channel = None
            if channel is not None and hasattr(channel, "send"):
                channel_guild_id = str(getattr(getattr(channel, "guild", None), "id", "") or "")
                if channel_guild_id == configured_server_id:
                    return channel
        # Never publish the soundboard in the schedule channel as a fallback.
        channel = self._find_text_channel_by_name(target_guild, "사운드보드")
        if channel is not None and str(channel.id) != notice_id:
            self.config["voice_panel_channel_id"] = str(channel.id)
            save_config_value("voice_panel_channel_id", str(channel.id))
            return channel
        return None

    @staticmethod
    def _find_text_channel_by_name(guild: Any, requested_name: str) -> Any | None:
        """Resolve an exact channel name (or an ID/mention) within one guild."""
        raw = str(requested_name or "").strip()
        if not raw:
            return None
        channel_id = raw.strip("<#>")
        for channel in list(getattr(guild, "text_channels", []) or []):
            candidate_id = str(getattr(channel, "id", "") or "")
            candidate_name = str(getattr(channel, "name", "") or "").strip()
            if channel_id.isdigit() and candidate_id == channel_id:
                return channel
            if candidate_name and candidate_name.casefold() == raw.lstrip("#").casefold():
                return channel
        return None

    async def _refresh_discord_voice_panel(self, guild: Any = None) -> None:
        """Refresh the pinned button list after a custom voice command changes."""
        try:
            target_guild = guild
            if target_guild is None:
                server_id = self._get_configured_server_id()
                target_guild = self.client.get_guild(int(server_id)) if server_id.isdigit() else None
            channel = await self._resolve_voice_panel_channel(target_guild)
            if channel is not None and target_guild is not None:
                await self._publish_discord_voice_channel_panel(channel, target_guild)
        except Exception as exc:
            log(f"voice_panel_refresh_failed error={exc}")

    async def _resolve_text_channel(self, guild: Any = None) -> Any | None:
        """Get the configured text channel, or the default channel named 보탐매니저."""
        configured_server_id = self._get_configured_server_id()
        if not configured_server_id:
            log("text_channel_resolve_skipped server_id_not_configured")
            return None
        if guild is not None and not self._is_configured_guild(guild):
            log(
                f"text_channel_resolve_ignored_wrong_guild guild_id={getattr(guild, 'id', '')} "
                f"configured_guild_id={configured_server_id}"
            )
            return None
        target_guild = guild if guild is not None else self.client.get_guild(int(configured_server_id))
        if target_guild is None or not self._is_configured_guild(target_guild):
            log(f"text_channel_resolve_failed configured_guild_id={configured_server_id}")
            return None
        configured_id = str(self.config.get("text_channel_id") or "").strip()
        if configured_id.isdigit():
            channel = self.client.get_channel(int(configured_id))
            if channel is None:
                try:
                    channel = await self.client.fetch_channel(int(configured_id))
                except Exception:
                    channel = None
            if channel is not None and hasattr(channel, "send"):
                configured_channel_guild_id = str(getattr(getattr(channel, "guild", None), "id", "") or "")
                if configured_channel_guild_id == configured_server_id:
                    return channel
                log(
                    f"text_channel_guild_mismatch configured_channel_id={configured_id} "
                    f"channel_guild_id={configured_channel_guild_id} configured_guild_id={configured_server_id}"
                )
            # An explicit ID must never silently select another channel.
            return None
        desired_name = "보탐매니저"
        for channel in list(getattr(target_guild, "text_channels", []) or []):
            if str(getattr(channel, "name", "") or "").strip().casefold() == desired_name.casefold():
                channel_id = str(getattr(channel, "id", "") or "")
                if channel_id.isdigit():
                    self.config["text_channel_id"] = channel_id
                    save_config_value("text_channel_id", channel_id)
                    log(f"text_channel_auto_selected channel_id={channel_id} name={desired_name}")
                return channel
        return None

    @staticmethod
    def _format_schedule_rows_as_text(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "예정된 스케쥴이 없습니다."
        lines: list[str] = []
        current_date = None
        for row in rows:
            scheduled_at = row.get("scheduled_at")
            if not isinstance(scheduled_at, datetime):
                continue
            if scheduled_at.date() != current_date:
                current_date = scheduled_at.date()
                lines.append(f"=== {scheduled_at.strftime('%Y-%m-%d (%a)')} ===")
            lines.append(f"{scheduled_at.strftime('%H%M')} {str(row.get('name') or '')}")
        return "\n".join(lines)

    @staticmethod
    def _format_schedule_rows_as_ansi(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "예정된 스케쥴이 없습니다."
        tier_codes = {
            "절대자": "1;35",      # bright magenta
            "고정보스": "1;34",    # bright blue
            "니플하임+": "1;36",   # bright cyan
            "침공": "1;31",        # bright red
            "일반": "0;37",        # white
        }
        escape = "\x1b["
        reset = f"{escape}0m"
        lines: list[str] = [f"{escape}1;97m✦ 보탐매니저 | 다음날 08:00까지{reset}"]
        current_date = None
        for row in rows:
            scheduled_at = row.get("scheduled_at")
            if not isinstance(scheduled_at, datetime):
                continue
            if scheduled_at.date() != current_date:
                current_date = scheduled_at.date()
                lines.append(f"{escape}1;33m━━ {scheduled_at.strftime('%Y-%m-%d (%a)')} ━━{reset}")
            tier = str(row.get("tier") or "일반")
            code = tier_codes.get(tier, tier_codes["일반"])
            lines.append(f"{escape}{code}m{scheduled_at.strftime('%H%M')}  {str(row.get('name') or '')}{reset}")
        lines.append(f"{escape}1;35m●절대자 {escape}1;34m●고정 {escape}1;36m●니플하임+ {escape}1;31m●침공{reset}")
        return "\n".join(lines)

    async def _send_schedule_text(self, channel: Any) -> None:
        if not self._send_allowed():
            return False
        rows = self.schedule_reader.upcoming_rows(limit=120)
        body = self._format_schedule_rows_as_ansi(rows)
        wrapped = f"```ansi\n{body}\n```"
        if len(wrapped) <= 2000:
            if not self._send_allowed():
                return
            await channel.send(wrapped)
            await self._cleanup_bot_text_channel_messages(channel)
            return
        plain_lines = body.splitlines()
        chunk: list[str] = []
        for line in plain_lines:
            candidate = "\n".join(chunk + [line])
            if chunk and len(candidate) + 12 > 2000:
                if not self._send_allowed():
                    return
                await channel.send(f"```ansi\n{'\n'.join(chunk)}\n```")
                chunk = [line]
            else:
                chunk.append(line)
        if chunk:
            if not self._send_allowed():
                return
            await channel.send(f"```ansi\n{'\n'.join(chunk)}\n```")
        await self._cleanup_bot_text_channel_messages(channel)

    async def _send_schedule_plain_text(self, channel: Any) -> None:
        if not self._send_allowed():
            return False
        """Post only HHMM and boss name so the result can be copied verbatim."""
        lines = [
            f"{scheduled_at.strftime('%H%M')} {str(row.get('name') or '')}"
            for row in self.schedule_reader.upcoming_rows(limit=120)
            if isinstance((scheduled_at := row.get("scheduled_at")), datetime)
        ] or ["예정된 스케쥴이 없습니다."]
        chunk: list[str] = []
        for line in lines:
            candidate = "\n".join(chunk + [line])
            if chunk and len(candidate) > 2000:
                if not self._send_allowed():
                    return
                await channel.send("\n".join(chunk))
                chunk = [line]
            else:
                chunk.append(line)
        if chunk:
            if not self._send_allowed():
                return
            await channel.send("\n".join(chunk))
        await self._cleanup_bot_text_channel_messages(channel)

    def _find_notice_color(self, message: str, *, fallback: int = 0x2563EB) -> int:
        message_text = str(message or "")
        for row in self.schedule_reader.upcoming_rows(limit=160):
            name = str(row.get("name") or "").strip()
            if name and name in message_text:
                return int(row.get("color") or fallback)
        if "침공" in message_text:
            return 0xDC2626
        if "고정" in message_text or "대전" in message_text:
            return 0x0891B2
        return int(fallback)

    def _format_voice_notice_ansi(self, message: str) -> str:
        """Make important notice words readable by color in Discord's ANSI block.

        Discord text has no native per-word color.  ANSI code blocks are the
        supported way to color individual words, while the embed edge keeps the
        exact boss/fixed-boss color configured in the local program.
        """
        text = str(message or "").replace("```", "''' ").strip()
        if not text:
            return ""

        # Approximate the configured category color in Discord's eight ANSI
        # colors.  The embed itself still uses the exact configured hex color.
        default_code = "37"  # normal: white
        if "침공" in text:
            default_code = "31"
        elif "고정" in text or "대전" in text:
            default_code = "34"
        else:
            for row in self.schedule_reader.upcoming_rows(limit=160):
                name = str(row.get("name") or "").strip()
                if name and name in text:
                    tier = str(row.get("tier") or "")
                    if tier == "절대자":
                        default_code = "35"
                    elif tier in {"고정보스", "니플하임+"}:
                        default_code = "36" if tier == "니플하임+" else "34"
                    elif tier == "침공":
                        default_code = "31"
                    break

        # Apply longer forms first so a short '타임' cannot break its phrase.
        token_patterns = (
            (r"(?:5분\s*(?:전|남(?:았습니다|았어요)?))", "1;33"),
            (r"(?:1분\s*(?:전|남(?:았습니다|았어요)?))", "1;31"),
            (r"(?:젠(?:\s*시간)?|타임(?:입니다|이에요|이다)?)", "1;32"),
        )
        escape = "\x1b["
        reset = f"{escape}0m"
        colored = text
        for pattern, code in token_patterns:
            colored = re.sub(
                pattern,
                lambda match: f"{escape}{code}m{match.group(0)}{reset}{escape}{default_code}m",
                colored,
                flags=re.IGNORECASE,
            )
        return f"```ansi\n{escape}{default_code}m{colored}{reset}\n```"

    @staticmethod
    def _notice_clock_text(at: datetime) -> str:
        hour = at.hour % 12 or 12
        return f"{'오전' if at.hour < 12 else '오후'} {hour}:{at.minute:02d}"

    @staticmethod
    def _boss_notice_group_identity(group_id: str, target: datetime) -> tuple[str, str, float]:
        """Compare actual occurrences, not a voice sequence's first/last time."""
        members = []
        member_targets = []
        for member in group_id.split('||'):
            boss_id, separator, at_text = member.rpartition('|')
            at = parse_datetime(at_text) if separator else None
            if not boss_id or at is None:
                # Fixed/control IDs do not include occurrence times. Their
                # supplied target still separates recurring announcements.
                return group_id, target.replace(microsecond=0).isoformat(), target.timestamp()
            members.append((boss_id, at.isoformat()))
            member_targets.append(at.timestamp())
        # Dynamic IDs already include every exact spawn time. A pre-alert uses
        # the first target while a near-spawn sequence uses the last target;
        # comparing the separate target would split the very same boss group.
        identity = json.dumps(sorted(set(members)), ensure_ascii=False, separators=(',', ':'))
        return identity, '', max(member_targets)

    @staticmethod
    def _boss_notice_text_signature(embeds: list[dict[str, Any]]) -> str:
        # Log a hash, not raw ANSI control codes or full message contents.
        # Ignore server-added embed metadata; only compare displayed text.
        text_parts = [dict(
            title=embed.get('title') or '', description=embed.get('description') or '',
            fields=[dict(name=field.get('name') or '', value=field.get('value') or '')
                    for field in embed.get('fields') or []],
        ) for embed in embeds]
        encoded = json.dumps(text_parts, ensure_ascii=False, sort_keys=True).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()[:16]

    @staticmethod
    def _boss_notice_names(group_id: str, message: str) -> str:
        names = []
        for member in group_id.split('||'):
            raw_key, separator, at_text = member.rpartition('|')
            if separator and parse_datetime(at_text) is not None:
                # Dynamic event IDs use normal/invasion:name|spawn-time.
                candidates = [raw_key]
            else:
                # Fixed groups use name|name; control IDs use type:name.
                candidates = member.removesuffix(':종료').split('|')
            for candidate in candidates:
                name = candidate.rsplit(':', 1)[-1].strip()
                if name and name not in names:
                    names.append(name)
        if not names:
            text = re.sub(r'^곧\s+', '', message.strip())
            name = re.split(r'\s+(?:\d+\s*(?:분|초)|젠\b|타임|등장)', text, maxsplit=1)[0]
            names = [name or '보스']
        return ', '.join(names)

    def _build_boss_notice_embed(
        self, entries: list[dict[str, str]], *, at: datetime, color: int, boss_names: str,
    ) -> Any:
        # One code block keeps adjacent notices as tightly spaced text lines.
        # Preserve each line's ANSI reset so colors never bleed into the next.
        notice_lines = [
            entry['text'].removeprefix('```ansi\n').removesuffix('\n```')
            for entry in entries
        ]
        name = ' '.join(boss_names.split())
        if len(name) > 190:
            name = name[:189] + '…'
        embed = self.discord.Embed(
            title=f"✦ {name} ✦\u2003\u2003\u2003\u2003{self._notice_clock_text(at)}",
            description='```ansi\n' + '\n'.join(notice_lines) + '\n```' if notice_lines else None,
            color=color,
        )
        return embed

    async def _send_voice_bridge_text_notice(self, job: VoiceBridgeJob) -> None:
        if not self._send_allowed():
            return False
        self._tag_authority_task()
        phase = str(job.phase or "").strip().upper()
        message = str(job.fallback_text or "").strip()
        if (
            not message
            or phase == "VOICE_COMMAND"
            or phase.startswith("COUNTDOWN")
            or phase.endswith("_GEN")
            or phase.endswith("_LEAD")
        ):
            return
        dedupe_key = f"{phase}|{message}|{job.scope_id}|{job.created_at.strftime('%Y%m%d%H%M%S')}"
        channel = await self._resolve_text_channel()
        if channel is None:
            return
        try:
            async with self.boss_notice_lock:
                if not self._send_allowed() or dedupe_key in self.text_notice_keys:
                    return
                now = datetime.now()
                now_timestamp = time.time()
                for key, old in list(self.boss_notice_groups.items()):
                    if float(old.get('expires_at') or 0) <= now_timestamp:
                        self.boss_notice_groups.pop(key, None)
                target = parse_datetime(job.target_time)
                group_id = str(job.notice_group_id or '').strip()
                identity, target_key, group_target_at = (
                    self._boss_notice_group_identity(group_id, target)
                    if group_id and target else ('', '', 0.0)
                )
                group_key = (
                    str(getattr(channel, 'id', '')), str(self._connection_state()['authority_generation']),
                    job.scope_id, job.category, identity, target_key,
                ) if group_id and target else None
                group = self.boss_notice_groups.get(group_key) if group_key else None
                # The fresh spawn result is final, including for delayed or
                # repeated jobs. Never edit it into an "edited" message again.
                if group and group.get('completed'):
                    return
                completed = job.offset_sec == 0 and phase.startswith(('SPAWN', 'DUE'))
                previous_group = group
                boss_names = str(group.get('boss_names') or '') if group else ''
                boss_names = boss_names or self._boss_notice_names(group_id, message)
                stage_key = f"{phase.removesuffix('_SEQUENCE')}|{job.offset_sec}|{message}"
                old_entries = list(group.get('entries') or []) if group else []
                if any(entry.get('stage_key') == stage_key for entry in old_entries):
                    return
                colored_message = self._format_voice_notice_ansi(message)
                if len(colored_message) > 1024:
                    colored_message = colored_message.removesuffix('\n```')[:990] + '…\x1b[0m\n```'
                entries = old_entries + [dict(stage_key=stage_key, text=colored_message)]
                color = int(group['color']) if group else self._find_notice_color(message)
                embed = self._build_boss_notice_embed(entries, at=now, color=color, boss_names=boss_names)
                # Start a continuation before Discord's per-embed/message limits.
                if len(embed.description or '') > 4096 or len(embed) > 6000:
                    entries = entries[-1:]
                    group = None
                    embed = self._build_boss_notice_embed(entries, at=now, color=color, boss_names=boss_names)
                signature = self._boss_notice_text_signature([embed.to_dict()])
                sent_message = None
                if completed and previous_group:
                    if not self._send_allowed():
                        return
                    try:
                        await previous_group['message'].delete()
                        log(f"text_notice_spawn_deleted phase={phase} message_id={previous_group['message_id']} "
                            f"at={time.time():.3f}")
                    except self.discord.NotFound:
                        pass  # Already removed manually; still send the fresh result.
                    if group_key:
                        self.boss_notice_groups.pop(group_key, None)
                    group = None
                if group:
                    try:
                        if not self._send_allowed():
                            return
                        sent_message = await group['message'].edit(embed=embed)
                        log(f"text_notice_updated phase={phase} message_id={group['message_id']} "
                            f"boxes={len(entries)} signature={signature} at={time.time():.3f}")
                    except self.discord.NotFound:
                        # A manually removed box must not prevent the next notice.
                        if group_key:
                            self.boss_notice_groups.pop(group_key, None)
                if sent_message is None:
                    if not self._send_allowed():
                        return
                    sent_message = await channel.send(embed=embed)
                    log(f"text_notice_sent phase={phase} channel_id={getattr(channel, 'id', '')} "
                        f"message_id={sent_message.id} boxes={len(entries)} signature={signature} "
                        f"at={time.time():.3f} text={message}")
                if group_key and target:
                    self.boss_notice_groups[group_key] = dict(
                        message=sent_message, message_id=str(sent_message.id), entries=entries, color=color,
                        boss_names=boss_names,
                        updated_at=now_timestamp, target_at=group_target_at,
                        expires_at=max(now_timestamp, group_target_at) + 300,
                        completed=completed,
                    )
                    if len(self.boss_notice_groups) > 200:
                        oldest = min(self.boss_notice_groups, key=lambda key: self.boss_notice_groups[key]['updated_at'])
                        self.boss_notice_groups.pop(oldest, None)
                self.text_notice_keys.add(dedupe_key)
                if len(self.text_notice_keys) > 1000:
                    self.text_notice_keys = set(list(self.text_notice_keys)[-500:])
            await self._cleanup_bot_text_channel_messages(channel)
        except Exception as exc:
            log(f"text_notice_failed phase={phase} error={exc}")

    async def _request_local_schedule_copy_image(self) -> str:
        """Ask the GUI to run its real 스케쥴복사 renderer and wait for its PNG."""
        request_id = uuid.uuid4().hex
        response_path = APP_ROOT / f"{SCHEDULE_SHARE_DISCORD_IMAGE_RESPONSE_PREFIX}{request_id}.json"
        request_payload = {
            "request_id": request_id,
            "requested_at": time.time(),
        }
        try:
            # One GUI renderer is shared by every command.  Do not replace an
            # outstanding request from another Discord interaction.
            if SCHEDULE_SHARE_DISCORD_IMAGE_REQUEST_PATH.exists():
                log("schedule_copy_request_busy")
                return ""
            temporary_path = APP_ROOT / f"{SCHEDULE_SHARE_DISCORD_IMAGE_REQUEST_PATH.name}.{request_id}.tmp"
            with temporary_path.open("w", encoding="utf-8") as request_file:
                json.dump(request_payload, request_file, ensure_ascii=False)
            os.replace(temporary_path, SCHEDULE_SHARE_DISCORD_IMAGE_REQUEST_PATH)
        except OSError as exc:
            log(f"schedule_copy_request_write_failed error={exc}")
            return ""

        deadline = time.monotonic() + 15.0
        try:
            while time.monotonic() < deadline:
                if response_path.is_file():
                    try:
                        response = json.loads(response_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError, TypeError):
                        response = {}
                    try:
                        response_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    if not isinstance(response, dict) or str(response.get("request_id") or "") != request_id:
                        return ""
                    image_path = Path(str(response.get("image_path") or ""))
                    if (
                        bool(response.get("ok"))
                        and image_path.resolve() == SCHEDULE_SHARE_LATEST_IMAGE_PATH.resolve()
                        and image_path.is_file()
                        and image_path.stat().st_size > 0
                    ):
                        return str(image_path)
                    log(f"schedule_copy_request_failed error={response.get('error', '')}")
                    return ""
                await asyncio.sleep(0.15)
            log("schedule_copy_request_timeout")
            return ""
        finally:
            # If the GUI has not picked our request up, remove only our own
            # request; never disturb a later request from another command.
            try:
                if SCHEDULE_SHARE_DISCORD_IMAGE_REQUEST_PATH.is_file():
                    queued = json.loads(SCHEDULE_SHARE_DISCORD_IMAGE_REQUEST_PATH.read_text(encoding="utf-8"))
                    if isinstance(queued, dict) and str(queued.get("request_id") or "") == request_id:
                        SCHEDULE_SHARE_DISCORD_IMAGE_REQUEST_PATH.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError):
                pass

    def _render_schedule_image(self) -> str:
        """Return the actual local schedule-copy bitmap when it is on clipboard.

        The local schedule copy renderer always creates a 520px-wide PNG.  By
        accepting only that size, an unrelated image the user copied cannot be
        posted accidentally.  This is the primary /이미지 path; the durable
        export is only a fallback for a cleared clipboard.
        """
        clipboard_path = self._capture_schedule_copy_clipboard_image()
        if clipboard_path:
            return clipboard_path
        try:
            if SCHEDULE_SHARE_LATEST_IMAGE_PATH.is_file() and SCHEDULE_SHARE_LATEST_IMAGE_PATH.stat().st_size > 0:
                return str(SCHEDULE_SHARE_LATEST_IMAGE_PATH)
        except OSError:
            pass
        rows = self.schedule_reader.upcoming_rows(limit=80)
        if not rows:
            return ""
        cache_dir = get_user_config_dir()
        cache_dir.mkdir(parents=True, exist_ok=True)
        payload_fd, payload_name = tempfile.mkstemp(prefix="bosstimer_schedule_", suffix=".json", dir=str(cache_dir))
        output_path = str(Path(payload_name).with_suffix(".png"))
        try:
            with os.fdopen(payload_fd, "w", encoding="utf-8") as payload_file:
                json.dump({"rows": rows, "output_path": output_path}, payload_file, ensure_ascii=False, default=str)
            payload_literal = json.dumps(payload_name, ensure_ascii=False)
            script = (
                "Add-Type -AssemblyName System.Drawing; "
                f"$p=Get-Content -Raw -Encoding UTF8 -LiteralPath {payload_literal}|ConvertFrom-Json; "
                "$rows=@($p.rows); $w=760; $rh=34; $h=96+($rows.Count*$rh); "
                "$bmp=New-Object System.Drawing.Bitmap $w,$h; $g=[System.Drawing.Graphics]::FromImage($bmp); "
                "$g.Clear([System.Drawing.Color]::FromArgb(248,250,252)); "
                "$title=New-Object System.Drawing.Font('Malgun Gothic',18,[System.Drawing.FontStyle]::Bold); "
                "$head=New-Object System.Drawing.Font('Malgun Gothic',10,[System.Drawing.FontStyle]::Bold); "
                "$body=New-Object System.Drawing.Font('Malgun Gothic',10); "
                "$dark=New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(15,23,42)); "
                "$g.FillRectangle((New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(219,234,254))),0,0,$w,62); "
                "$g.DrawString('보탐매니저 스케쥴',$title,$dark,20,14); "
                "$g.DrawString(('기준 '+(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')),$body,$dark,22,68); "
                "$g.DrawString('시각',$head,$dark,24,68); $g.DrawString('구분',$head,$dark,170,68); $g.DrawString('보스',$head,$dark,250,68); "
                "$y=96; foreach($r in $rows){ $bg=[System.Drawing.Color]::FromArgb(255,255,255); try{if([string]$r.bg_color){$bg=[System.Drawing.ColorTranslator]::FromHtml([string]$r.bg_color)}}catch{}; "
                "$g.FillRectangle((New-Object System.Drawing.SolidBrush($bg)),12,$y,$w-24,$rh-2); "
                "$accent=[System.Drawing.Color]::FromArgb([int]$r.color); $g.FillRectangle((New-Object System.Drawing.SolidBrush($accent)),12,$y,6,$rh-2); "
                "$fg=$dark; try{if([string]$r.text_color){$fg=[System.Drawing.ColorTranslator]::FromHtml([string]$r.text_color)}}catch{}; $brush=New-Object System.Drawing.SolidBrush($fg); "
                "$dt=[datetime]$r.scheduled_at; $g.DrawString($dt.ToString('MM/dd HH:mm'),$body,$brush,24,$y+7); $g.DrawString([string]$r.kind,$body,$brush,170,$y+7); $g.DrawString([string]$r.name,$body,$brush,250,$y+7); $y+=$rh }; "
                "$bmp.Save([string]$p.output_path,[System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            if result.returncode != 0 or not Path(output_path).is_file():
                log(f"schedule_image_render_failed code={result.returncode} error={(result.stderr or result.stdout)[-500:]}")
                return ""
            return output_path
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"schedule_image_render_exception error={exc}")
            return ""
        finally:
            try:
                Path(payload_name).unlink(missing_ok=True)
            except OSError:
                pass

    def _capture_schedule_copy_clipboard_image(self) -> str:
        """Save the current 520px schedule-copy clipboard bitmap to a temp PNG."""
        cache_dir = get_user_config_dir()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            file_descriptor, temporary_name = tempfile.mkstemp(
                prefix="bosstimer_clipboard_schedule_",
                suffix=".png",
                dir=str(cache_dir),
            )
            os.close(file_descriptor)
            temporary_path = Path(temporary_name)
            temporary_path.unlink(missing_ok=True)
        except OSError as exc:
            log(f"schedule_clipboard_capture_prepare_failed error={exc}")
            return ""

        output_literal = json.dumps(str(temporary_path), ensure_ascii=False)
        script = (
            "$ErrorActionPreference='Stop'; "
            "$image=Get-Clipboard -Format Image -ErrorAction SilentlyContinue; "
            "if($null -eq $image -or $image.Width -ne 520){exit 2}; "
            f"$image.Save({output_literal},[System.Drawing.Imaging.ImageFormat]::Png)"
        )
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-STA", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
            )
            if result.returncode == 0 and temporary_path.is_file() and temporary_path.stat().st_size > 0:
                log("schedule_clipboard_capture_ready")
                return str(temporary_path)
            temporary_path.unlink(missing_ok=True)
            return ""
        except (OSError, subprocess.SubprocessError) as exc:
            log(f"schedule_clipboard_capture_failed error={exc}")
            temporary_path.unlink(missing_ok=True)
            return ""

    async def _disconnect_stale_configured_voice_session(self, *, wait_seconds: float = 3.0, force: bool = False) -> bool:
        """Reset the configured guild voice state before a new runtime connects."""
        if self.voice_client is not None and getattr(self.voice_client, "is_connected", lambda: False)():
            return False
        guild_id = self._get_configured_server_id()
        if not guild_id:
            return False
        guild = self.client.get_guild(int(guild_id))
        if guild is None:
            return False
        member = getattr(guild, "me", None)
        voice_state = getattr(member, "voice", None)
        stale_channel = getattr(voice_state, "channel", None)
        stale_channel_id = str(getattr(stale_channel, "id", "") or "")
        if not force and member is not None and stale_channel is None:
            log("voice_session_reset_skipped reason=no_stale_voice_session")
            return False
        log(
            f"voice_session_reset_requested stale_detected={int(stale_channel is not None)} "
            f"channel_id={stale_channel_id}"
        )
        try:
            await guild.change_voice_state(channel=None)
            STATUS.update(voice_connected=False)
            delay_seconds = max(0.0, float(wait_seconds))
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            log(
                f"voice_session_reset_complete channel_id={stale_channel_id} "
                f"delay_sec={delay_seconds:.1f}"
            )
            return True
        except Exception as exc:
            log(f"stale_voice_session_disconnect_failed channel_id={stale_channel_id} error={exc}")
            return False

    async def _connect_configured_voice_channel(self, *, automatic: bool = True, expected_generation=None) -> bool:
        if self._connection_is_blocked():
            return False
        generation = self._connection_state()["authority_generation"] if expected_generation is None else expected_generation
        channel_id = str(self._connection_state().get("voice_channel_id") or self.config.get("voice_channel_id") or "").strip()
        if not channel_id:
            STATUS.update(guild_id=self._get_configured_server_id(), voice_channel_id="", voice_connected=False)
            log("voice_channel_not_configured")
            return False
        lock = getattr(self, "voice_connect_lock", None)
        if lock is None:
            self.voice_connect_lock = lock = asyncio.Lock()
        async with lock:
            if self._connection_is_blocked() or self._connection_state()["authority_generation"] != generation:
                return False
            if self.voice_client is not None and self.voice_client.is_connected():
                return True
            if automatic:
                try:
                    if not self._claim_connection_retry():
                        self._connection_state()
                        return False
                except OSError as exc:
                    log(f"voice_retry_state_failed type={type(exc).__name__} error={exc!r}")
                    return False
            if automatic:
                attempt = self.connection_policy.snapshot()["voice_retries"]
                log(f"voice_reconnect_watchdog_attempt generation={generation} attempt={attempt}/{MAX_RETRIES}")
            connected, _message = await self._connect_voice_channel(channel_id, expected_generation=generation)
            return bool(connected)

    async def _connect_voice_channel(self, channel_id: str, *, expected_generation=None) -> tuple[bool, str]:
        if self._connection_is_blocked():
            return False, STATUS.configuration_error or "대기 상태입니다."
        generation = self._connection_state()["authority_generation"] if expected_generation is None else expected_generation
        if self._connection_state()["authority_generation"] != generation:
            return False, "관리자 권한이 변경되었습니다."
        channel_id = str(channel_id or "").strip()
        configured_guild_id = self._get_configured_server_id()
        if not channel_id.isdigit():
            return False, "음성채널 ID가 올바르지 않습니다."
        if not configured_guild_id:
            STATUS.update(voice_connected=False, last_error="서버 ID가 설정되지 않았습니다.")
            return False, "서버 ID가 설정되지 않았습니다."
        pending_voice = None
        try:
            channel = self.client.get_channel(int(channel_id))
            if channel is None:
                channel = await asyncio.wait_for(self.client.fetch_channel(int(channel_id)), timeout=8.0)
            if channel is None or not hasattr(channel, "connect"):
                raise RuntimeError("음성채널을 찾지 못했습니다.")
            channel_guild_id = str(getattr(getattr(channel, "guild", None), "id", "") or "")
            if channel_guild_id != configured_guild_id:
                message = "서버 ID와 음성채널의 서버가 다릅니다. 봇 설정에서 이 서버의 음성채널 ID를 다시 입력하고 봇을 재실행하세요."
                self.connection_policy.update(voice_error=message)
                raise DiscordConfigurationError(message)
            if self._connection_is_blocked() or self._connection_state()["authority_generation"] != generation:
                return False, "관리자 권한이 변경되었습니다."
            cached_voice = getattr(channel.guild, "voice_client", None)
            if cached_voice is not None:
                if (cached_voice.is_connected()
                        and getattr(cached_voice, "authority_generation", None) == generation):
                    self.voice_client = cached_voice
                else:
                    await self._close_voice_client(cached_voice, reason="before_reconnect")
                    if self._connection_is_blocked() or self._connection_state()["authority_generation"] != generation:
                        return False, "관리자 권한이 변경되었습니다."
            if self.voice_client is not None and getattr(self.voice_client, "is_connected", lambda: False)():
                if getattr(self.voice_client, "channel", None) and self.voice_client.channel.id == int(channel_id):
                    STATUS.update(voice_channel_id=channel_id, voice_connected=True)
                    return True, "이미 해당 음성채널에 연결되어 있습니다."
                await asyncio.wait_for(self.voice_client.move_to(channel), timeout=8.0)
            else:
                voice_type = self._authority_voice_client_type(generation)
                def create_voice(client, voice_channel):
                    nonlocal pending_voice
                    pending_voice = voice_type(client, voice_channel)
                    self.voice_client = pending_voice
                    return pending_voice
                self.voice_client = await asyncio.wait_for(
                    channel.connect(cls=create_voice, timeout=10.0, self_deaf=True, reconnect=False), timeout=12.0)
            if self._connection_is_blocked() or self._connection_state()["authority_generation"] != generation:
                await self._close_voice_client(self.voice_client, reason="authority_changed_during_join")
                return False, "대기 전환으로 접속을 중단했습니다."
            if getattr(self, "connection_policy", None) is not None:
                self.connection_policy.update(voice_retries=0, voice_next_retry=0)
                self._connection_state()
            guild_id = str(getattr(getattr(channel, "guild", None), "id", self.config.get("server_id", "")) or "")
            STATUS.update(guild_id=guild_id, voice_channel_id=channel_id, voice_connected=True,
                          voice_authority_generation=generation, last_error="")
            log(f"voice_connected guild_id={guild_id} channel_id={channel_id}")
            return True, "음성채널에 연결했습니다."
        except asyncio.CancelledError:
            await self._close_voice_client(pending_voice, reason="voice_join_cancelled")
            raise
        except Exception as exc:
            await self._close_voice_client(pending_voice, reason="voice_join_failed")
            if self._connection_state()["authority_generation"] != generation:
                return False, "관리자 권한이 변경되었습니다."
            if is_access_error(exc):
                self.connection_policy.update(voice_error=ACCESS_ERROR_TEXT)
            detail = "음성 연결 응답시간 초과" if isinstance(exc, TimeoutError) else str(exc) or type(exc).__name__
            message = f"음성채널 연결 실패: {detail}"
            STATUS.update(voice_channel_id=channel_id, voice_connected=False, last_error=message)
            log(f"voice_connect_failed channel_id={channel_id} generation={generation} type={type(exc).__name__} error={exc!r}")
            return False, message

    async def _schedule_loop(self) -> None:
        schedule_task = asyncio.current_task()
        while not STATUS.shutdown_requested.is_set():
            try:
                state = self._connection_state()
                schedule_task.boss_authority_generation = state["authority_generation"]
                if state.get("handover_hold"):
                    if not getattr(self, "_handover_audio_stopped", False):
                        self._handover_audio_stopped = True
                        async with self.voice_transition_lock:
                            await self._stop_current_voice_playback_and_wait(reason="handover")
                    # Discard stale announcements produced while synchronizing.
                    self.voice_bridge_reader.read_new_jobs()
                    await asyncio.sleep(.1)
                    continue
                self._handover_audio_stopped = False
                if not self._send_allowed():
                    self.voice_bridge_reader.read_new_jobs()  # Drop standby backlog.
                    await asyncio.sleep(.1)
                    continue
                if config_bool(self.config, "voice_bridge_enabled", True):
                    STATUS.update(voice_bridge_enabled=True)
                    generation = state["authority_generation"]
                    for job in self.voice_bridge_reader.read_new_jobs():
                        if not self._send_allowed() or self._connection_state()["authority_generation"] != generation:
                            break
                        if isinstance(job, VoiceBridgeControl):
                            await self._handle_voice_bridge_control(job)
                        else:
                            self.play_queue.put((generation, job))
                            # 채팅 전송 지연이 음성 시작 시각에 영향을 주지 않도록
                            # 별도 작업으로 보낸다. 초읽기 숫자/분리 젠 조각은 메서드에서
                            # 걸러내고 실제 안내 문장만 남긴다.
                            notice_task = self.client.loop.create_task(self._send_voice_bridge_text_notice(job))
                            notice_task.boss_authority_generation = generation
                else:
                    STATUS.update(voice_bridge_enabled=False)
                    now = datetime.now()
                    for job in self.schedule_reader.due_jobs(now, self.announced):
                        self.announced.add(job.key)
                        self.play_queue.put((state["authority_generation"], job))
                if len(self.announced) > 5000:
                    self.announced = set(list(self.announced)[-2500:])
            except Exception as exc:
                STATUS.update(last_error=f"스케쥴 확인 실패: {exc}")
                log(f"schedule_loop_failed error={exc}")
            finally:
                if schedule_task is not None and hasattr(schedule_task, "boss_authority_generation"):
                    del schedule_task.boss_authority_generation
            await asyncio.sleep(VOICE_BRIDGE_POLL_INTERVAL_SEC if config_bool(self.config, "voice_bridge_enabled", True) else 0.25)

    async def _play_loop(self) -> None:
        playback_task = asyncio.current_task()
        while not STATUS.shutdown_requested.is_set():
            job = None
            try:
                # An indefinitely blocked executor queue.get prevents Client.run
                # from returning during standby or Gateway recovery.
                try:
                    queued = self.play_queue.get_nowait()
                except queue.Empty:
                    await asyncio.sleep(.05)
                    continue
                if queued is None:
                    continue
                generation, job = queued
                playback_task.boss_authority_generation = generation
                if not self._send_allowed():
                    continue
                self.regular_voice_job_active = True
                await self._ensure_voice_connection()
                if self.voice_client is None or not self.voice_client.is_connected():
                    STATUS.update(last_error="음성채널에 연결되어 있지 않습니다.", voice_connected=False)
                    if isinstance(job, VoiceBridgeJob):
                        STATUS.record_bridge_result(job.id, "failed", job.scope_id)
                    continue
                if isinstance(job, VoiceBridgeJob):
                    if self._is_voice_bridge_scope_cancelled(job.scope_id):
                        STATUS.record_bridge_result(job.id, "cancelled", job.scope_id)
                        log(f"bridge_play_skipped_cancelled id={job.id} scope={job.scope_id} phase={job.phase}")
                        continue
                    clips = list(job.clip_paths)
                    timed_clips = list(job.timed_clips)
                    if not clips and not timed_clips:
                        STATUS.record_bridge_result(job.id, "failed", job.scope_id)
                        continue
                    STATUS.record_bridge_result(job.id, "timed_untracked" if timed_clips else "started", job.scope_id)
                    STATUS.update(last_played=f"BRIDGE {job.phase} {job.fallback_text}", last_error="", voice_bridge_last_id=job.id)
                    latency_ms = int(round((datetime.now() - job.created_at).total_seconds() * 1000.0))
                    log(f"bridge_play_start id={job.id} phase={job.phase} clips={len(clips)} timed_clips={len(timed_clips)} latency_ms={latency_ms} text={job.fallback_text}")
                    if timed_clips:
                        timed_task = self.client.loop.create_task(self._play_timed_bridge_clips(job))
                        timed_task.boss_authority_generation = generation
                        self.timed_bridge_tasks[job.id] = (job.scope_id, timed_task)
                        timed_task.add_done_callback(
                            lambda completed_task, job_id=job.id: self._finish_timed_bridge_task(job_id, completed_task)
                        )
                        continue
                    completed = True
                    for clip_path in clips:
                        if self._is_voice_bridge_scope_cancelled(job.scope_id):
                            completed = False
                            break
                        if not await self._play_clip(clip_path, volume=job.volume, scope_id=job.scope_id, confirm_eof=True):
                            completed = False
                    cancelled = self._is_voice_bridge_scope_cancelled(job.scope_id) or STATUS.shutdown_requested.is_set()
                    STATUS.record_bridge_result(job.id, "cancelled" if cancelled else "completed" if completed else "failed", job.scope_id)
                    if completed and not cancelled:
                        STATUS.record_notice_opportunity(job)
                    continue
                clips = self._build_clips_for_job(job)
                if not clips:
                    STATUS.update(last_error=f"재생할 음성 파일이 없습니다: {', '.join(job.names)}")
                    continue
                STATUS.update(last_played=f"{job.phase} {', '.join(job.names)}", last_error="")
                log(f"play_start phase={job.phase} names={','.join(job.names)} clips={len(clips)}")
                for clip_path in clips:
                    await self._play_clip(clip_path)
            except Exception as exc:
                if isinstance(job, VoiceBridgeJob):
                    STATUS.record_bridge_result(job.id, "failed", job.scope_id)
                STATUS.update(last_error=f"재생 실패: {exc}")
                log(f"play_loop_failed type={type(exc).__name__} error={exc!r}")
            finally:
                self.regular_voice_job_active = False
                if playback_task is not None and hasattr(playback_task, "boss_authority_generation"):
                    del playback_task.boss_authority_generation

    async def _play_timed_bridge_clips(self, job: VoiceBridgeJob) -> None:
        confirm_notice = job.offset_sec == 60 and job.phase in {'PRE_ALERT_SEQUENCE', 'FIXED_PRE_ALERT_SEQUENCE'}
        all_completed = bool(job.timed_clips)
        try:
            # 초읽기 중 0초 젠(침공 포함)도 같은 Discord PCM 스트림으로
            # 합성해야 한다. 별도 timed clip으로 재생하면 뒤 요청이 현재
            # 초읽기를 교체해 이름/15초가 사라진다.
            if job.phase in {
                "COUNTDOWN_SEQUENCE",
                "SPAWN_CONFIRMED_SEQUENCE",
                "SPAWN_CONFIRMED_NEAR_SEQUENCE",
            }:
                try:
                    if await self._play_timed_bridge_composite(job):
                        return
                except Exception as exc:
                    log(
                        f"bridge_composite_failed id={job.id} scope={job.scope_id} "
                        f"phase={job.phase} type={type(exc).__name__} error={exc!r} fallback=individual"
                    )
            for play_at, clip_path in job.timed_clips:
                if self._is_voice_bridge_scope_cancelled(job.scope_id):
                    return
                prepared_source = None
                try:
                    if job.phase in TIMED_REPLACE_CURRENT_AUDIO_PHASES:
                        try:
                            prepared_source = self._create_playback_source(
                                clip_path,
                                volume=job.volume,
                                trim_silence=True,
                            )
                        except Exception as exc:
                            log(f"timed_clip_prepare_failed id={job.id} phase={job.phase} path={clip_path} error={exc}")
                            continue
                    while not STATUS.shutdown_requested.is_set():
                        if self._is_voice_bridge_scope_cancelled(job.scope_id):
                            return
                        delay_seconds = (play_at - datetime.now()).total_seconds()
                        if delay_seconds <= 0:
                            break
                        max_sleep = 0.02 if job.phase in TIMED_REPLACE_CURRENT_AUDIO_PHASES else 0.2
                        await asyncio.sleep(min(max_sleep, max(0.0, delay_seconds)))
                    if STATUS.shutdown_requested.is_set() or self._is_voice_bridge_scope_cancelled(job.scope_id):
                        return
                    late_ms = int(round((datetime.now() - play_at).total_seconds() * 1000.0))
                    log(f"bridge_timed_clip_play id={job.id} phase={job.phase} late_ms={late_ms} path={clip_path}")
                    if job.phase in TIMED_REPLACE_CURRENT_AUDIO_PHASES:
                        source_to_play = prepared_source
                        prepared_source = None
                        await self._play_timed_clip_replacing_current(
                            clip_path,
                            volume=job.volume,
                            trim_silence=True,
                            source=source_to_play,
                            scope_id=job.scope_id,
                        )
                    else:
                        completed = await self._play_clip(
                            clip_path,
                            volume=job.volume,
                            gap_sec=0.0,
                            trim_silence=True,
                            scope_id=job.scope_id,
                            confirm_eof=confirm_notice,
                        )
                        if confirm_notice and completed is not True:
                            all_completed = False
                finally:
                    self._cleanup_audio_source(prepared_source)
            if confirm_notice and all_completed and not self._is_voice_bridge_scope_cancelled(job.scope_id):
                STATUS.record_bridge_result(job.id, 'completed', job.scope_id)
                STATUS.record_notice_opportunity(job)
        except asyncio.CancelledError:
            log(f"bridge_timed_task_cancelled id={job.id} scope={job.scope_id} phase={job.phase}")
            raise

    def _is_voice_bridge_scope_cancelled(self, scope_id: str) -> bool:
        if not self._send_allowed():
            return True
        scope_text = str(scope_id or "").strip()
        return bool(scope_text and scope_text in self.cancelled_voice_bridge_scopes)

    def _finish_timed_bridge_task(self, job_id: str, completed_task: asyncio.Task[Any]) -> None:
        self.timed_bridge_tasks.pop(job_id, None)
        try:
            completed_task.exception()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            log(f"bridge_timed_task_failed id={job_id} error={exc}")

    def _clear_current_voice_playback(self, token: object) -> None:
        if self.current_voice_playback_token is token:
            self.current_voice_playback_token = None
            self.current_voice_player_thread = None
            self.current_voice_scope_id = ""
            self.current_timed_composite_source = None
            self.current_timed_composite_scope_id = ""

    async def _install_prepared_notice_output(self, source, *, scope_id: str, deadline: float) -> bool:
        if not self._send_allowed():
            return False
        """Reserve a silent notice output; caller owns source if rejected.

        Future module bridge must revalidate its delivery token before play().
        Installing a source alone never starts audio or changes notice records.
        """
        async with self.voice_transition_lock:
            previous = getattr(self, "notice_output", None)
            if previous is not None and not previous.released:
                return False
            self.notice_output = DiscordNoticeOutput(self, source, scope_id=scope_id, deadline=deadline)
            return True

    async def _stop_current_voice_playback_and_wait(
        self,
        *,
        expected_scope_id: str = "",
        reason: str = "replace",
        timeout_sec: float = 1.5,
    ) -> tuple[bool, int, bool]:
        voice_client = self.voice_client
        if voice_client is None:
            return False, 0, True
        expected_scope = str(expected_scope_id or "").strip()
        active_scope = str(self.current_voice_scope_id or "").strip()
        playback_token = self.current_voice_playback_token
        if expected_scope and active_scope != expected_scope:
            return False, 0, True

        player = self.current_voice_player_thread or getattr(voice_client, "_player", None)
        if playback_token is None and player is None:
            return False, 0, True
        started_at = time.monotonic()
        notice_output = getattr(self, "notice_output", None)
        if notice_output is not None:
            # Freeze only this notice's reader before freeing the voice slot.
            # Regular/timed boss outputs keep their existing replacement path.
            notice_output.before_stop(playback_token, reason)
        composite_source = self.current_timed_composite_source
        cancel_source = getattr(composite_source, "cancel", None)
        if callable(cancel_source):
            try:
                cancel_source()
            except Exception as exc:
                log(f"voice_transition_source_cancel_failed reason={reason} error={exc}")
        try:
            voice_client.stop()
        except Exception as exc:
            log(f"voice_transition_stop_failed reason={reason} error={exc}")

        joined = True
        if player is not None and hasattr(player, "join"):
            try:
                await asyncio.to_thread(player.join, max(0.1, float(timeout_sec)))
                joined = not bool(getattr(player, "is_alive", lambda: False)())
            except Exception as exc:
                joined = False
                log(f"voice_transition_join_failed reason={reason} error={exc}")

        if playback_token is not None:
            deadline = time.monotonic() + max(0.1, float(timeout_sec))
            while self.current_voice_playback_token is playback_token and time.monotonic() < deadline:
                await asyncio.sleep(0.005)
            if joined and self.current_voice_playback_token is playback_token:
                self._clear_current_voice_playback(playback_token)
        waited_ms = int(round((time.monotonic() - started_at) * 1000.0))
        log(
            f"voice_transition_idle reason={reason} scope={active_scope or '-'} "
            f"waited_ms={waited_ms} joined={int(joined)} token={int(playback_token is not None)}"
        )
        return True, waited_ms, joined

    async def _handle_voice_bridge_control(self, control: VoiceBridgeControl) -> None:
        if not self._send_allowed():
            return False
        if control.action == "heartbeat":
            STATUS.update(voice_bridge_last_heartbeat_id=control.id)
            return
        if control.action == "text_notice":
            channel = await self._resolve_text_channel()
            if channel is None:
                log(f"system_text_notice_skipped id={control.id} reason=text_channel_not_found")
                return
            try:
                embed = self.discord.Embed(
                    title="⚠ 보탐매니저 연결 로그",
                    description=control.message,
                    color=0xF59E0B,
                    timestamp=datetime.now(),
                )
                embed.set_footer(text="BossTimer 자동 복구")
                if not self._send_allowed():
                    return
                await channel.send(embed=embed)
                await self._cleanup_bot_text_channel_messages(channel)
                log(
                    f"system_text_notice_sent id={control.id} "
                    f"channel_id={getattr(channel, 'id', '')} text={control.message}"
                )
            except Exception as exc:
                log(f"system_text_notice_failed id={control.id} error={exc}")
            return
        if control.action != "cancel_scope" or not control.scope_id:
            return
        scope_id = control.scope_id
        self.cancelled_voice_bridge_scopes[scope_id] = time.monotonic()
        if len(self.cancelled_voice_bridge_scopes) > 256:
            cutoff = time.monotonic() - 3600.0
            self.cancelled_voice_bridge_scopes = {
                key: cancelled_at
                for key, cancelled_at in self.cancelled_voice_bridge_scopes.items()
                if cancelled_at >= cutoff
            }
        pending_tasks = sum(
            1
            for task_scope_id, task in self.timed_bridge_tasks.values()
            if task_scope_id == scope_id and not task.done()
        )
        composite_cancelled = False
        playback_stopped = False
        playback_waited_ms = 0
        playback_joined = True
        cancel_mode = "drain"
        async with self.voice_transition_lock:
            if not self._send_allowed():
                return
            composite_source = self.current_timed_composite_source
            cancel_scope = getattr(composite_source, "cancel_scope", None)
            if self.current_timed_composite_scope_id == scope_id and callable(cancel_scope):
                try:
                    composite_cancelled = bool(cancel_scope(scope_id))
                except Exception as exc:
                    log(f"bridge_composite_scope_cancel_failed scope={scope_id} error={exc}")
                if composite_cancelled:
                    cancel_mode = "persistent"
                    if self.current_voice_scope_id == scope_id:
                        self.current_voice_scope_id = ""
                    if self.current_timed_composite_scope_id == scope_id:
                        self.current_timed_composite_scope_id = ""
            if not composite_cancelled:
                playback_stopped, playback_waited_ms, playback_joined = await self._stop_current_voice_playback_and_wait(
                    expected_scope_id=scope_id,
                    reason="cancel_scope",
                )
        log(
            f"bridge_scope_cancelled id={control.id} scope={scope_id} "
            f"tasks={pending_tasks} composite_cancelled={int(composite_cancelled)} "
            f"playback_stopped={int(playback_stopped)} waited_ms={playback_waited_ms} "
            f"joined={int(playback_joined)} mode={cancel_mode}"
        )

    async def _shutdown_watch_loop(self) -> None:
        while not STATUS.shutdown_requested.is_set():
            await asyncio.sleep(0.25)
        log("shutdown_requested")
        notice_output = getattr(self, "notice_output", None)
        if notice_output is not None:
            try:
                await notice_output.stop()
            except Exception as exc:
                log(f"notice_shutdown_stop_failed error={exc}")
        try:
            if self.voice_client is not None and self.voice_client.is_connected():
                await self.voice_client.disconnect(force=True)
        except Exception:
            pass
        await self.client.close()

    async def _ensure_voice_connection(self) -> None:
        if self._connection_is_blocked():
            return
        state = self._connection_state()
        if getattr(asyncio.current_task(), "boss_authority_generation", state["authority_generation"]) != state["authority_generation"]:
            return
        if self.voice_client is not None and self.voice_client.is_connected():
            STATUS.update(voice_connected=True)
            return
        await self._connect_configured_voice_channel()

    def _create_playback_source(
        self,
        clip_path: str,
        *,
        volume: float = 1.0,
        trim_silence: bool = False,
    ) -> Any:
        source = self._create_audio_source(clip_path, trim_silence=trim_silence)
        playback_volume = self._get_clip_playback_volume(clip_path, volume)
        if abs(playback_volume - 1.0) > 0.001 and hasattr(self.discord, "PCMVolumeTransformer"):
            source = self.discord.PCMVolumeTransformer(source, volume=playback_volume)
        return source

    def _is_edge_tts_cache_clip_path(self, clip_path: str) -> bool:
        try:
            path_parts = {part.casefold() for part in Path(clip_path).parts}
        except (OSError, TypeError, ValueError):
            return False
        return "tts_캐쉬".casefold() in path_parts or "tts_cache" in path_parts

    def _get_clip_playback_volume(self, clip_path: str, volume: float) -> float:
        try:
            playback_volume = max(0.0, min(2.0, float(volume)))
        except (TypeError, ValueError):
            playback_volume = 1.0
        if self._is_edge_tts_cache_clip_path(clip_path):
            playback_volume *= EDGE_TTS_PLAYBACK_GAIN
        return max(0.0, min(2.0, playback_volume))

    @staticmethod
    def _get_timed_job_playback_volume(job: VoiceBridgeJob) -> float:
        try:
            playback_volume = float(job.volume)
        except (TypeError, ValueError):
            playback_volume = 1.0
        if (
            str(job.phase or "").strip().upper() == "SPAWN_CONFIRMED_SEQUENCE"
            and str(job.fallback_text or "").lstrip().startswith("침공")
        ):
            playback_volume *= DISCORD_INVASION_SEQUENCE_GAIN
        return max(0.0, min(2.0, playback_volume))

    def _cleanup_audio_source(self, source: Any) -> None:
        if source is None:
            return
        try:
            source.cleanup()
        except Exception:
            pass

    @staticmethod
    def _apply_timed_pcm_lane(pcm: bytes, lane: str) -> bytes:
        normalized_lane = str(lane or "center").strip().lower()
        if normalized_lane not in {"left", "right"} or not pcm:
            return pcm
        if audioop is not None:
            mono = audioop.tomono(pcm, 2, 0.5, 0.5)
            return (
                audioop.tostereo(mono, 2, 1.0, 0.0)
                if normalized_lane == "left"
                else audioop.tostereo(mono, 2, 0.0, 1.0)
            )
        # discord.py consumes stereo signed 16-bit little-endian PCM.  Keep
        # the requested channel and silence the opposite channel when the
        # stdlib audioop compatibility module is unavailable.
        balanced = bytearray(pcm)
        silent_offset = 2 if normalized_lane == "left" else 0
        for frame_offset in range(0, len(balanced) - 3, 4):
            balanced[frame_offset + silent_offset:frame_offset + silent_offset + 2] = b"\x00\x00"
        return bytes(balanced)

    def _read_timed_clip_pcm(self, clip_path: str, volume: float, lane: str = "center") -> bytes:
        path = Path(clip_path)
        stat = path.stat()
        playback_volume = self._get_clip_playback_volume(str(path), volume)
        volume_key = int(round(playback_volume * 1000.0))
        normalized_lane = str(lane or "center").strip().lower()
        cache_key = (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size), volume_key, normalized_lane)
        with self.timed_pcm_cache_lock:
            cached = self.timed_pcm_cache.get(cache_key)
        if cached is not None:
            return cached

        source = self._create_playback_source(str(path), volume=volume, trim_silence=True)
        chunks: list[bytes] = []
        try:
            while True:
                chunk = source.read()
                if not chunk:
                    break
                chunks.append(bytes(chunk))
        finally:
            self._cleanup_audio_source(source)
        pcm = self._apply_timed_pcm_lane(b"".join(chunks), normalized_lane)
        with self.timed_pcm_cache_lock:
            if len(self.timed_pcm_cache) >= 512:
                oldest_key = next(iter(self.timed_pcm_cache), None)
                if oldest_key is not None:
                    self.timed_pcm_cache.pop(oldest_key, None)
            self.timed_pcm_cache[cache_key] = pcm
        return pcm

    def _create_timed_composite_source(self, job: VoiceBridgeJob) -> tuple[Any, datetime, int]:
        timed_clips = sorted(job.timed_clips, key=lambda item: item[0])
        if not timed_clips:
            raise RuntimeError("timed clips are empty")
        stream_start_at = timed_clips[0][0] - timedelta(seconds=DISCORD_COUNTDOWN_COMPOSITE_WARMUP_SEC)
        timeline = bytearray()
        job_playback_volume = self._get_timed_job_playback_volume(job)
        for play_at, clip_path in timed_clips:
            pcm = self._read_timed_clip_pcm(clip_path, job_playback_volume, job.lane)
            offset_frames = max(
                0,
                int(round((play_at - stream_start_at).total_seconds() * 48000.0)),
            )
            offset_bytes = offset_frames * 4
            if offset_bytes > len(timeline):
                timeline.extend(b"\x00" * (offset_bytes - len(timeline)))
            if offset_bytes < len(timeline):
                overlap_bytes = min(len(pcm), len(timeline) - offset_bytes)
                existing_pcm = bytes(timeline[offset_bytes:offset_bytes + overlap_bytes])
                overlay_pcm = pcm[:overlap_bytes]
                if audioop is not None:
                    mixed_pcm = audioop.add(existing_pcm, overlay_pcm, 2)
                else:
                    mixed_pcm = bytearray(overlap_bytes)
                    for sample_offset in range(0, overlap_bytes - 1, 2):
                        existing_sample = int.from_bytes(
                            existing_pcm[sample_offset:sample_offset + 2],
                            "little",
                            signed=True,
                        )
                        overlay_sample = int.from_bytes(
                            overlay_pcm[sample_offset:sample_offset + 2],
                            "little",
                            signed=True,
                        )
                        mixed_sample = max(-32768, min(32767, existing_sample + overlay_sample))
                        mixed_pcm[sample_offset:sample_offset + 2] = int(mixed_sample).to_bytes(
                            2,
                            "little",
                            signed=True,
                        )
                    mixed_pcm = bytes(mixed_pcm)
                timeline[offset_bytes:offset_bytes + overlap_bytes] = mixed_pcm
                if overlap_bytes < len(pcm):
                    timeline.extend(pcm[overlap_bytes:])
            else:
                timeline.extend(pcm)
        timeline.extend(b"\x00" * (DISCORD_PCM_FRAME_BYTES * 5))
        pcm_data = bytes(timeline)
        discord_module = self.discord
        event_loop = self.client.loop

        class BossTimerTimedCompositeSource(discord_module.AudioSource):
            def __init__(self, data: bytes) -> None:
                self.template_data = data
                self.data = b""
                self.offset = 0
                self.start_monotonic = 0.0
                self.scope_id = ""
                self.stream_done: asyncio.Event | None = None
                self.stream_started = False
                self.idle_until = time.monotonic() + DISCORD_COUNTDOWN_COMPOSITE_IDLE_KEEPALIVE_SEC
                self.closed = False
                self.lock = threading.Lock()

            def _signal_done(self, done: asyncio.Event | None) -> None:
                if done is not None:
                    try:
                        event_loop.call_soon_threadsafe(done.set)
                    except RuntimeError:
                        pass

            def configure_stream(
                self,
                *,
                data: bytes | None = None,
                start_monotonic: float,
                scope_id: str,
                done: asyncio.Event,
            ) -> None:
                previous_done = None
                with self.lock:
                    previous_done = self.stream_done
                    stream_data = bytes(self.template_data if data is None else data)
                    self.data = stream_data
                    self.offset = 0
                    self.start_monotonic = float(start_monotonic)
                    self.scope_id = str(scope_id or "").strip()
                    self.stream_done = done
                    self.stream_started = False
                    self.closed = False
                    duration_sec = len(stream_data) / float(DISCORD_PCM_BYTES_PER_SECOND)
                    self.idle_until = max(
                        time.monotonic() + DISCORD_COUNTDOWN_COMPOSITE_IDLE_KEEPALIVE_SEC,
                        self.start_monotonic + duration_sec + DISCORD_COUNTDOWN_COMPOSITE_IDLE_KEEPALIVE_SEC,
                    )
                if previous_done is not done:
                    self._signal_done(previous_done)

            def merge_stream(
                self,
                *,
                data: bytes,
                start_monotonic: float,
                scope_id: str,
                done: asyncio.Event,
            ) -> None:
                """현재 출력 중인 시간축에 새 예약 음성을 겹쳐 넣는다.

                Discord 음성 클라이언트는 실제 재생 스트림을 하나만 가질 수
                있다. 따라서 2초 차이 초읽기나 침공 젠을 별도 ``play``로
                보내면 뒤 요청이 앞의 초읽기를 교체한다. 현재 재생 위치를
                기준점으로 남은 PCM과 새 PCM을 하나의 시간축으로 합성한다.
                """
                previous_done = None
                incoming_data = bytes(data or b"")
                if not incoming_data:
                    self._signal_done(done)
                    return
                with self.lock:
                    if not self.data or self.closed:
                        # 재생할 스트림이 없으면 일반 설정과 동일하다.
                        self.data = incoming_data
                        self.offset = 0
                        self.start_monotonic = float(start_monotonic)
                        self.scope_id = str(scope_id or "").strip()
                        self.stream_done = done
                        self.stream_started = False
                        self.closed = False
                        duration_sec = len(incoming_data) / float(DISCORD_PCM_BYTES_PER_SECOND)
                        self.idle_until = max(
                            time.monotonic() + DISCORD_COUNTDOWN_COMPOSITE_IDLE_KEEPALIVE_SEC,
                            self.start_monotonic + duration_sec + DISCORD_COUNTDOWN_COMPOSITE_IDLE_KEEPALIVE_SEC,
                        )
                        return

                    previous_done = self.stream_done
                    now_monotonic = time.monotonic()
                    if self.stream_started:
                        # read()가 이미 지나간 PCM은 버리고, 지금부터 남은
                        # 시간축을 새 기준점으로 삼는다.
                        base_start = now_monotonic
                        existing_data = bytes(self.data[self.offset:])
                        existing_offset = 0
                    else:
                        base_start = min(float(self.start_monotonic), float(start_monotonic))
                        existing_data = bytes(self.data)
                        existing_offset = max(
                            0,
                            int(round((float(self.start_monotonic) - base_start) * DISCORD_PCM_BYTES_PER_SECOND)),
                        )

                    incoming_start = float(start_monotonic)
                    if incoming_start < base_start:
                        # Timed sources contain a 1.2-second silent warm-up.
                        # When such a source is merged into an already running
                        # countdown, its nominal start is normally in the past.
                        # Keeping those elapsed bytes delayed invasion/name
                        # audio by about 1.5 seconds and made it collide with
                        # the next countdown clips.  Mirror read()'s late-start
                        # behaviour and discard the elapsed, frame-aligned PCM.
                        elapsed_bytes = int(round(
                            (base_start - incoming_start) * DISCORD_PCM_BYTES_PER_SECOND
                        ))
                        elapsed_bytes = max(0, (elapsed_bytes // 4) * 4)
                        incoming_data = incoming_data[min(len(incoming_data), elapsed_bytes):]
                        incoming_offset = 0
                    else:
                        incoming_offset = max(
                            0,
                            int(round((incoming_start - base_start) * DISCORD_PCM_BYTES_PER_SECOND)),
                        )
                    merged_size = max(
                        existing_offset + len(existing_data),
                        incoming_offset + len(incoming_data),
                    )
                    merged = bytearray(merged_size)
                    merged[existing_offset:existing_offset + len(existing_data)] = existing_data
                    overlap_start = max(existing_offset, incoming_offset)
                    overlap_end = min(
                        existing_offset + len(existing_data),
                        incoming_offset + len(incoming_data),
                    )
                    if overlap_end > overlap_start:
                        existing_pcm = bytes(merged[overlap_start:overlap_end])
                        incoming_start = overlap_start - incoming_offset
                        incoming_pcm = incoming_data[incoming_start:incoming_start + (overlap_end - overlap_start)]
                        if audioop is not None:
                            # This runs in C.  Mixing several seconds sample by
                            # sample in Python blocked Discord's event loop long
                            # enough to skip a countdown number when an invasion
                            # spawn arrived during a countdown.
                            mixed_pcm = audioop.add(existing_pcm, incoming_pcm, 2)
                        else:
                            mixed_buffer = bytearray(len(existing_pcm))
                            for sample_offset in range(0, len(existing_pcm) - 1, 2):
                                existing_sample = int.from_bytes(
                                    existing_pcm[sample_offset:sample_offset + 2], "little", signed=True
                                )
                                incoming_sample = int.from_bytes(
                                    incoming_pcm[sample_offset:sample_offset + 2], "little", signed=True
                                )
                                mixed_sample = max(-32768, min(32767, existing_sample + incoming_sample))
                                mixed_buffer[sample_offset:sample_offset + 2] = int(mixed_sample).to_bytes(
                                    2, "little", signed=True
                                )
                            mixed_pcm = bytes(mixed_buffer)
                        merged[overlap_start:overlap_end] = mixed_pcm
                    # 겹치지 않는 새 구간은 그대로 채운다. 겹치는 구간은 위에서
                    # 합성했으므로 제외한다.
                    if incoming_offset < overlap_start:
                        merged[incoming_offset:overlap_start] = incoming_data[:overlap_start - incoming_offset]
                    if overlap_end < incoming_offset + len(incoming_data):
                        source_start = overlap_end - incoming_offset
                        merged[overlap_end:incoming_offset + len(incoming_data)] = incoming_data[source_start:]

                    self.data = bytes(merged)
                    self.offset = 0
                    self.start_monotonic = base_start
                    self.scope_id = str(scope_id or "").strip()
                    self.stream_done = done
                    self.stream_started = False
                    self.closed = False
                    duration_sec = len(self.data) / float(DISCORD_PCM_BYTES_PER_SECOND)
                    self.idle_until = max(
                        now_monotonic + DISCORD_COUNTDOWN_COMPOSITE_IDLE_KEEPALIVE_SEC,
                        base_start + duration_sec + DISCORD_COUNTDOWN_COMPOSITE_IDLE_KEEPALIVE_SEC,
                    )
                if previous_done is not done:
                    self._signal_done(previous_done)

            def export_data(self) -> bytes:
                with self.lock:
                    return bytes(self.template_data)

            def cancel_scope(self, scope_id: str) -> bool:
                done = None
                with self.lock:
                    if str(scope_id or "").strip() != self.scope_id:
                        return False
                    done = self.stream_done
                    self.data = b""
                    self.offset = 0
                    self.scope_id = ""
                    self.stream_done = None
                    self.stream_started = False
                    self.idle_until = time.monotonic() + DISCORD_COUNTDOWN_COMPOSITE_IDLE_KEEPALIVE_SEC
                self._signal_done(done)
                return True

            def is_stream_active(self) -> bool:
                with self.lock:
                    return bool(self.data and self.stream_done is not None)

            def can_accept_stream(self) -> bool:
                with self.lock:
                    return bool(
                        not self.closed
                        and self.idle_until - time.monotonic() > 0.1
                    )

            def read(self) -> bytes:
                done = None
                with self.lock:
                    if self.closed:
                        return b""
                    now_monotonic = time.monotonic()
                    if not self.data:
                        if now_monotonic >= self.idle_until:
                            self.closed = True
                            return b""
                        return b"\x00" * DISCORD_PCM_FRAME_BYTES
                    if now_monotonic < self.start_monotonic:
                        return b"\x00" * DISCORD_PCM_FRAME_BYTES
                    if not self.stream_started:
                        late_seconds = max(0.0, now_monotonic - self.start_monotonic)
                        late_frames = int(late_seconds * 50.0)
                        self.offset = min(len(self.data), late_frames * DISCORD_PCM_FRAME_BYTES)
                        self.stream_started = True
                    if self.offset >= len(self.data):
                        done = self.stream_done
                        self.data = b""
                        self.offset = 0
                        self.scope_id = ""
                        self.stream_done = None
                        self.stream_started = False
                        self.idle_until = now_monotonic + DISCORD_COUNTDOWN_COMPOSITE_IDLE_KEEPALIVE_SEC
                        chunk = b"\x00" * DISCORD_PCM_FRAME_BYTES
                    else:
                        end = min(len(self.data), self.offset + DISCORD_PCM_FRAME_BYTES)
                        chunk = self.data[self.offset:end]
                        self.offset = end
                self._signal_done(done)
                if len(chunk) < DISCORD_PCM_FRAME_BYTES:
                    chunk += b"\x00" * (DISCORD_PCM_FRAME_BYTES - len(chunk))
                return chunk

            def is_opus(self) -> bool:
                return False

            def cancel(self) -> None:
                done = None
                with self.lock:
                    done = self.stream_done
                    self.closed = True
                    self.data = b""
                    self.stream_done = None
                    self.scope_id = ""
                self._signal_done(done)

            def cleanup(self) -> None:
                self.cancel()
                with self.lock:
                    self.template_data = b""

        duration_ms = int(round(len(pcm_data) * 1000.0 / DISCORD_PCM_BYTES_PER_SECOND))
        return BossTimerTimedCompositeSource(pcm_data), stream_start_at, duration_ms

    async def _play_timed_bridge_composite(self, job: VoiceBridgeJob) -> bool:
        source, stream_start_at, duration_ms = await asyncio.to_thread(
            self._create_timed_composite_source,
            job,
        )
        playback_start_at = stream_start_at - timedelta(
            milliseconds=DISCORD_COUNTDOWN_COMPOSITE_OUTPUT_LEAD_MS
        )
        log(
            f"bridge_composite_prepared id={job.id} scope={job.scope_id} "
            f"clips={len(job.timed_clips)} duration_ms={duration_ms} "
            f"output_lead_ms={DISCORD_COUNTDOWN_COMPOSITE_OUTPUT_LEAD_MS} "
            f"lane={job.lane} source_volume={job.volume:.2f} "
            f"playback_volume={self._get_timed_job_playback_volume(job):.2f}"
        )
        stream_done = asyncio.Event()
        temporary_source = source
        active_source = None
        reused_player = False
        try:
            if STATUS.shutdown_requested.is_set() or self._is_voice_bridge_scope_cancelled(job.scope_id):
                return True
            if self.voice_client is None or not self.voice_client.is_connected():
                return True

            start_monotonic = time.monotonic() + (playback_start_at - datetime.now()).total_seconds()
            async with self.voice_transition_lock:
                if self._is_voice_bridge_scope_cancelled(job.scope_id):
                    return True
                existing_source = self.current_timed_composite_source
                configure_existing = getattr(existing_source, "configure_stream", None)
                merge_existing = getattr(existing_source, "merge_stream", None)
                can_accept_existing = getattr(existing_source, "can_accept_stream", None)
                player = self.current_voice_player_thread or getattr(self.voice_client, "_player", None)
                player_alive = bool(player is not None and getattr(player, "is_alive", lambda: False)())
                can_reuse = bool(
                    callable(configure_existing)
                    and callable(can_accept_existing)
                    and can_accept_existing()
                    and self.current_voice_playback_token is not None
                    and player_alive
                    and (self.voice_client.is_playing() or self.voice_client.is_paused())
                )
                if can_reuse:
                    if callable(merge_existing):
                        merge_existing(
                            data=source.export_data(),
                            start_monotonic=start_monotonic,
                            scope_id=job.scope_id,
                            done=stream_done,
                        )
                    else:
                        configure_existing(
                            data=source.export_data(),
                            start_monotonic=start_monotonic,
                            scope_id=job.scope_id,
                            done=stream_done,
                        )
                    active_source = existing_source
                    reused_player = True
                    self.current_voice_scope_id = str(job.scope_id or "").strip()
                    self.current_timed_composite_scope_id = str(job.scope_id or "").strip()
                else:
                    playback_stopped, _waited_ms, playback_joined = await self._stop_current_voice_playback_and_wait(
                        reason="countdown_composite_replace",
                    )
                    if playback_stopped and not playback_joined:
                        log(f"bridge_composite_skipped_busy id={job.id} scope={job.scope_id}")
                        return True
                    if self._is_voice_bridge_scope_cancelled(job.scope_id):
                        return True
                    playback_token = object()

                    def after_play(error: Exception | None) -> None:
                        if error:
                            log(f"bridge_composite_play_error id={job.id} error={error}")
                        self.client.loop.call_soon_threadsafe(self._clear_current_voice_playback, playback_token)
                        self.client.loop.call_soon_threadsafe(stream_done.set)

                    source.configure_stream(
                        start_monotonic=start_monotonic,
                        scope_id=job.scope_id,
                        done=stream_done,
                    )
                    active_source = source
                    temporary_source = None
                    self.current_voice_scope_id = str(job.scope_id or "").strip()
                    self.current_voice_playback_token = playback_token
                    self.current_timed_composite_source = source
                    self.current_timed_composite_scope_id = str(job.scope_id or "").strip()
                    try:
                        self.voice_client.play(source, after=after_play)
                    except Exception:
                        self._clear_current_voice_playback(playback_token)
                        self._cleanup_audio_source(source)
                        active_source = None
                        raise
                    self.current_voice_player_thread = getattr(self.voice_client, "_player", None)
            late_ms = int(round((datetime.now() - playback_start_at).total_seconds() * 1000.0))
            log(
                f"bridge_composite_stream id={job.id} scope={job.scope_id} "
                f"clips={len(job.timed_clips)} late_ms={late_ms} reused={int(reused_player)}"
            )
            if temporary_source is not None:
                self._cleanup_audio_source(temporary_source)
                temporary_source = None
            await stream_done.wait()
            if (
                self.current_timed_composite_source is active_source
                and self.current_timed_composite_scope_id == str(job.scope_id or "").strip()
            ):
                self.current_timed_composite_scope_id = ""
                if self.current_voice_scope_id == str(job.scope_id or "").strip():
                    self.current_voice_scope_id = ""
            return True
        finally:
            self._cleanup_audio_source(temporary_source)

    async def _play_timed_clip_replacing_current(
        self,
        clip_path: str,
        *,
        volume: float = 1.0,
        trim_silence: bool = False,
        source: Any = None,
        scope_id: str = "",
    ) -> None:
        if self.voice_client is None:
            self._cleanup_audio_source(source)
            return
        if self._is_voice_bridge_scope_cancelled(scope_id):
            self._cleanup_audio_source(source)
            return
        if source is None:
            source = self._create_playback_source(clip_path, volume=volume, trim_silence=trim_silence)

        def after_play(error: Exception | None) -> None:
            if error:
                log(f"timed_clip_play_error path={clip_path} error={error}")
            self.client.loop.call_soon_threadsafe(self._clear_current_voice_playback, playback_token)

        playback_token = object()
        try:
            async with self.voice_transition_lock:
                playback_stopped, _waited_ms, playback_joined = await self._stop_current_voice_playback_and_wait(
                    reason="timed_clip_replace",
                )
                if playback_stopped and not playback_joined:
                    self._cleanup_audio_source(source)
                    log(f"timed_clip_skipped_busy path={clip_path}")
                    return
                if self._is_voice_bridge_scope_cancelled(scope_id):
                    self._cleanup_audio_source(source)
                    return
                self.current_voice_scope_id = str(scope_id or "").strip()
                self.current_voice_playback_token = playback_token
                self.voice_client.play(source, after=after_play)
                self.current_voice_player_thread = getattr(self.voice_client, "_player", None)
        except Exception as exc:
            self._clear_current_voice_playback(playback_token)
            self._cleanup_audio_source(source)
            log(f"timed_clip_play_failed path={clip_path} type={type(exc).__name__} error={exc!r}")

    def _build_clips_for_job(self, job: AlertJob) -> list[str]:
        self.alarm_settings = load_json(ALARM_SETTINGS_PATH)
        clips: list[str] = []
        primary_name, additional_count, all_invasion = compact_alert_names(job.names)
        unique_names = list(dict.fromkeys(str(name or "").strip() for name in job.names if str(name or "").strip()))
        speak_every_name = len(unique_names) <= 2
        if speak_every_name:
            additional_count = 0
        count_clip = voice_file("count", "다수" if additional_count >= 10 else f"{additional_count}개") if additional_count else ""
        extra_clip = voice_file("info", "외") if additional_count else ""
        spoken_names = (
            tuple(re.sub(r"^\s*침공\s*", "", name).strip() for name in unique_names)
            if speak_every_name
            else ((primary_name,) if primary_name else ())
        )

        def append_spoken_names() -> None:
            for index, name in enumerate(spoken_names):
                if (
                    speak_every_name
                    and not all_invasion
                    and index < len(unique_names)
                    and bool(re.match(r"^\s*침공\s*", unique_names[index]))
                ):
                    clips.append(voice_file("info", "침공"))
                path = voice_file("boss", name)
                if path:
                    clips.append(path)

        if job.phase in {"PRE_ALERT", "FIXED_PRE_ALERT"}:
            chime = chime_file(job.kind, self.alarm_settings)
            if chime:
                clips.append(chime)
            if all_invasion:
                clips.append(voice_file("info", "침공"))
            append_spoken_names()
            if additional_count and extra_clip and count_clip:
                clips.extend((extra_clip, count_clip))
            offset_clip = build_offset_clip(job.offset_seconds)
            if offset_clip:
                clips.append(offset_clip)
            return [path for path in clips if path]
        if job.phase == "SPAWN" and job.second_confirmed:
            if all_invasion:
                clips.append(voice_file("info", "침공"))
            append_spoken_names()
            if additional_count and extra_clip and count_clip:
                clips.extend((extra_clip, count_clip))
            clips.append(voice_file("info", "젠"))
            return [path for path in clips if path]
        chime = chime_file(job.kind, self.alarm_settings)
        if chime:
            clips.append(chime)
        if all_invasion:
            clips.append(voice_file("info", "침공"))
        else:
            clips.append(voice_file("info", "곧"))
        append_spoken_names()
        if additional_count and extra_clip and count_clip:
            clips.extend((extra_clip, count_clip))
        clips.append(voice_file("info", "타임입니다"))
        return [path for path in clips if path]

    async def _play_clip(
        self,
        clip_path: str,
        *,
        volume: float = 1.0,
        gap_sec: float = VOICE_CLIP_GAP_SEC,
        trim_silence: bool = False,
        scope_id: str = "",
        confirm_eof: bool = False,
    ) -> bool | None:
        if self.voice_client is None:
            return
        if self._is_voice_bridge_scope_cancelled(scope_id):
            return
        async with self.voice_play_lock:
            if self._is_voice_bridge_scope_cancelled(scope_id):
                return
            notice_output = getattr(self, "notice_output", None)
            if notice_output is not None and notice_output.owns(self.current_voice_playback_token):
                async with self.voice_transition_lock:
                    if notice_output.owns(self.current_voice_playback_token):
                        _, _, joined = await self._stop_current_voice_playback_and_wait(reason="notice_preempt")
                        if not joined:
                            return  # Never mix boss output with an unconfirmed old reader.
            while self.voice_client.is_playing() or self.voice_client.is_paused():
                if self._is_voice_bridge_scope_cancelled(scope_id):
                    return
                composite_source = self.current_timed_composite_source
                is_stream_active = getattr(composite_source, "is_stream_active", None)
                if composite_source is not None and callable(is_stream_active) and not is_stream_active():
                    async with self.voice_transition_lock:
                        current_source = self.current_timed_composite_source
                        current_active = getattr(current_source, "is_stream_active", None)
                        if current_source is composite_source and callable(current_active) and not current_active():
                            await self._stop_current_voice_playback_and_wait(
                                reason="idle_composite_release",
                            )
                    continue
                await asyncio.sleep(0.05)
            if self._is_voice_bridge_scope_cancelled(scope_id):
                return
            source = self._create_playback_source(
                clip_path,
                volume=volume,
                trim_silence=trim_silence,
            )
            if confirm_eof:
                source = observe_audio_source(self.discord.AudioSource, source)
            done = asyncio.Event()
            playback_token = object()
            playback_errors = []

            def after_play(error: Exception | None) -> None:
                if error:
                    playback_errors.append(error)
                    log(f"clip_play_error path={clip_path} error={error}")
                self.client.loop.call_soon_threadsafe(self._clear_current_voice_playback, playback_token)
                self.client.loop.call_soon_threadsafe(done.set)

            try:
                while True:
                    if self._is_voice_bridge_scope_cancelled(scope_id):
                        self._cleanup_audio_source(source)
                        return
                    async with self.voice_transition_lock:
                        if not self.voice_client.is_playing() and not self.voice_client.is_paused():
                            self.current_voice_scope_id = str(scope_id or "").strip()
                            self.current_voice_playback_token = playback_token
                            self.voice_client.play(source, after=after_play)
                            self.current_voice_player_thread = getattr(self.voice_client, "_player", None)
                            break
                    await asyncio.sleep(0.01)
                await done.wait()
            except asyncio.CancelledError:
                if self.current_voice_playback_token is playback_token:
                    try:
                        async with self.voice_transition_lock:
                            await asyncio.shield(
                                self._stop_current_voice_playback_and_wait(
                                    expected_scope_id=scope_id,
                                    reason="clip_task_cancelled",
                                )
                            )
                    except Exception:
                        pass
                raise
            except Exception:
                self._clear_current_voice_playback(playback_token)
                self._cleanup_audio_source(source)
                raise
            gap_seconds = max(0.0, float(gap_sec))
            if gap_seconds > 0:
                await asyncio.sleep(gap_seconds)
            if confirm_eof:
                return bool(source.reached_eof and source.frames_read > 0 and not playback_errors
                            and not self._is_voice_bridge_scope_cancelled(scope_id)
                            and self.voice_client is not None and self.voice_client.is_connected())

    def _create_audio_source(self, clip_path: str, *, trim_silence: bool = False) -> Any:
        if Path(clip_path).suffix.casefold() == ".wav":
            try:
                return self._create_wav_audio_source(clip_path, trim_silence=trim_silence)
            except Exception as exc:
                log(f"wav_source_failed path={clip_path} error={exc}")
        return self.discord.FFmpegPCMAudio(
            clip_path,
            executable=self._get_ffmpeg_executable(),
        )

    @staticmethod
    def _get_ffmpeg_executable() -> str:
        candidates: list[Path] = []
        resource_root = str(getattr(sys, "_MEIPASS", "") or "").strip()
        if resource_root:
            candidates.append(Path(resource_root) / "ffmpeg.exe")
        candidates.append(APP_ROOT / "ffmpeg.exe")
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return str(candidate)
            except OSError:
                continue
        return str(shutil.which("ffmpeg") or "ffmpeg")

    def _create_wav_audio_source(self, clip_path: str, *, trim_silence: bool = False) -> Any:
        if audioop is None:
            raise RuntimeError("audioop 모듈을 사용할 수 없습니다.")
        discord_module = self.discord

        class BossTimerWavAudioSource(discord_module.AudioSource):
            def __init__(self, path: str, *, trim: bool = False) -> None:
                self.path = path
                self.wave_file = wave.open(path, "rb")
                self.source_channels = self.wave_file.getnchannels()
                self.source_sample_width = self.wave_file.getsampwidth()
                self.source_frame_rate = self.wave_file.getframerate()
                self.source_frame_width = self.source_sample_width * self.source_channels
                self.raw_audio: bytes | None = None
                self.raw_offset = 0
                self.rate_state = None
                self.pending = b""
                self.finished = False
                if trim:
                    raw_audio = self.wave_file.readframes(self.wave_file.getnframes())
                    self.raw_audio = self._trim_source_silence(raw_audio)

            def read(self) -> bytes:
                if self.finished and not self.pending:
                    return b""
                target_size = 3840
                while len(self.pending) < target_size and not self.finished:
                    raw = self._read_source_frames(2048)
                    if not raw:
                        self.finished = True
                        break
                    self.pending += self._convert(raw)
                if not self.pending:
                    return b""
                chunk = self.pending[:target_size]
                self.pending = self.pending[target_size:]
                if len(chunk) < target_size:
                    self.finished = True
                    return chunk + (b"\x00" * (target_size - len(chunk)))
                return chunk

            def _read_source_frames(self, frame_count: int) -> bytes:
                if self.raw_audio is None:
                    return self.wave_file.readframes(frame_count)
                read_bytes = max(0, int(frame_count)) * self.source_frame_width
                if read_bytes <= 0 or self.raw_offset >= len(self.raw_audio):
                    return b""
                chunk = self.raw_audio[self.raw_offset:self.raw_offset + read_bytes]
                self.raw_offset += len(chunk)
                return chunk

            def _trim_source_silence(self, raw: bytes) -> bytes:
                if not raw:
                    return raw
                frame_width = self.source_frame_width
                if frame_width <= 0:
                    return raw
                step_bytes = max(frame_width, int(self.source_frame_rate * 0.01) * frame_width)
                threshold = 280
                first: int | None = None
                last = 0
                for offset in range(0, len(raw), step_bytes):
                    chunk = raw[offset:offset + step_bytes]
                    if not chunk:
                        continue
                    try:
                        rms = audioop.rms(chunk, self.source_sample_width)
                    except Exception:
                        return raw
                    if rms > threshold:
                        if first is None:
                            first = offset
                        last = offset + len(chunk)
                if first is None:
                    return raw
                pad_bytes = int(self.source_frame_rate * 0.025) * frame_width
                start = max(0, first - pad_bytes)
                end = min(len(raw), last + pad_bytes)
                return raw[start:end]

            def _convert(self, raw: bytes) -> bytes:
                data = raw
                sample_width = self.source_sample_width
                if sample_width != 2:
                    data = audioop.lin2lin(data, sample_width, 2)
                    sample_width = 2
                channels = self.source_channels
                if channels == 1:
                    data = audioop.tostereo(data, sample_width, 1, 1)
                    channels = 2
                elif channels != 2:
                    raise RuntimeError(f"지원하지 않는 WAV 채널 수: {channels}")
                if self.source_frame_rate != 48000:
                    data, self.rate_state = audioop.ratecv(
                        data,
                        sample_width,
                        channels,
                        self.source_frame_rate,
                        48000,
                        self.rate_state,
                    )
                return data

            def is_opus(self) -> bool:
                return False

            def cleanup(self) -> None:
                try:
                    self.wave_file.close()
                except Exception:
                    pass

        return BossTimerWavAudioSource(clip_path, trim=trim_silence)

    def run(self) -> bool:
        runtime_mode = "exe" if getattr(sys, "frozen", False) else "source"
        runtime_path = sys.executable if runtime_mode == "exe" else str(Path(__file__).resolve())
        log(f"discord_runtime_loaded mode={runtime_mode} path={runtime_path} channel_policy=2")
        token = str(self.config.get("bot_token") or "").strip()
        if not token:
            STATUS.update(last_error="봇 토큰이 비어 있습니다.")
            log("missing_bot_token")
            while not STATUS.shutdown_requested.is_set():
                time.sleep(0.25)
            return self.message_content_enabled
        try:
            self.client.run(token, log_handler=None, reconnect=False)
        except DiscordConfigurationError:
            # Keep the status endpoint available, without reconnecting with
            # the wrong identity. The GUI can stop/restart after correction.
            STATUS.shutdown_requested.wait(1)
        except Exception as exc:
            if is_access_error(exc):
                self._block_connection_error(ACCESS_ERROR_TEXT)
                STATUS.update(online=False, voice_connected=False)
                STATUS.shutdown_requested.wait(1)
                return self.message_content_enabled
            privileged_intents_error = getattr(self.discord, "PrivilegedIntentsRequired", ())
            if (
                self.message_content_enabled
                and isinstance(exc, privileged_intents_error)
                and not STATUS.shutdown_requested.is_set()
            ):
                STATUS.update(
                    online=False,
                    voice_connected=False,
                    last_error="Message Content Intent가 꺼져 있어 일반 채팅 명령 없이 다시 연결합니다.",
                    text_commands_enabled=False,
                )
                log("message_content_intent_unavailable fallback=voice_and_slash_commands")
                return False
            STATUS.update(online=False, voice_connected=False, last_error=f"디스코드 실행 실패: {exc}")
            log(f"discord_run_failed error={exc}")
        return self.message_content_enabled


def main() -> int:
    server = start_status_server()
    if server is None:
        return 1
    parent_pid_text = str(os.environ.get("BOSS_TIMER_PARENT_PID") or "").strip()
    try:
        parent_pid = int(parent_pid_text)
    except ValueError:
        parent_pid = 0

    if parent_pid > 0 and parent_pid != os.getpid():
        def parent_process_is_alive(pid: int) -> bool:
            if os.name == "nt":
                try:
                    import ctypes
                    from ctypes import wintypes

                    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                    open_process = kernel32.OpenProcess
                    open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
                    open_process.restype = wintypes.HANDLE
                    close_handle = kernel32.CloseHandle
                    close_handle.argtypes = (wintypes.HANDLE,)
                    close_handle.restype = wintypes.BOOL
                    handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
                    if not handle:
                        return False
                    close_handle(handle)
                    return True
                except Exception:
                    return False
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return False
            except PermissionError:
                # A live process may reject inspection by a non-elevated app.
                return True
            except OSError:
                return False
            return True

        def watch_parent_process() -> None:
            while not STATUS.shutdown_requested.wait(1.0):
                if not parent_process_is_alive(parent_pid):
                    log(f"parent_process_exited pid={parent_pid}")
                    STATUS.shutdown_requested.set()
                    force_process_exit_after_shutdown(0.75)
                    return

        threading.Thread(target=watch_parent_process, name="boss-timer-parent-watch", daemon=True).start()
    try:
        import nacl.secret  # type: ignore[import-not-found]
        import nacl.utils  # type: ignore[import-not-found]
    except Exception as exc:
        STATUS.update(nacl_import_error=f"{type(exc).__name__}: {exc}")
        log(f"pynacl_import_failed type={type(exc).__name__} error={exc}")
    else:
        log("pynacl_import_preload_ok")
    try:
        import discord  # type: ignore[import-not-found]
    except Exception as exc:
        STATUS.update(last_error=f"discord.py 또는 PyNaCl 설치가 필요합니다: {exc}")
        log(f"discord_import_failed error={exc}")
        while not STATUS.shutdown_requested.is_set():
            time.sleep(0.25)
        server.shutdown()
        server.server_close()
        return 1
    try:
        if not discord.opus.is_loaded() and not discord.opus._load_default():
            raise RuntimeError("Discord Opus 인코더 DLL을 불러오지 못했습니다.")
    except Exception as exc:
        STATUS.update(last_error=f"Discord Opus 인코더를 초기화하지 못했습니다: {exc}")
        log(f"discord_opus_load_failed type={type(exc).__name__} error={exc!r}")
    else:
        log("discord_opus_load_ok")
    nacl_available = bool(getattr(getattr(discord, "voice_client", None), "has_nacl", False))
    STATUS.update(nacl_available=nacl_available)
    log(f"pynacl_available={int(nacl_available)}")
    disconnect_only = str(os.environ.get("BOSS_TIMER_DISCORD_DISCONNECT_ONLY") or "").strip() == "1"
    enable_message_content = not disconnect_only
    policy = ConnectionPolicy(CONFIG_PATH)
    needs_retry = False
    reserved_launch = os.environ.get("BOSS_TIMER_DISCORD_RETRY_RESERVED") == "1"
    while not STATUS.shutdown_requested.is_set():
        state = policy.snapshot()
        STATUS.update(standby=state["standby"], reconnect_attempts=state["retries"],
                      configuration_error=state["error"])
        if state["error"] or (state["retries"] >= MAX_RETRIES and not reserved_launch):
            STATUS.update(last_error=state["error"] or "자동 재접속 10회 제한에 도달했습니다.")
            STATUS.shutdown_requested.wait(1)
            continue
        if needs_retry and not policy.claim_retry():
            STATUS.shutdown_requested.wait(1.0)
            continue
        bot = DiscordScheduleBot(discord, enable_message_content=enable_message_content)
        reserved_launch = False
        enable_message_content = bot.run()
        needs_retry = True
        if STATUS.shutdown_requested.is_set():
            break
        if disconnect_only:
            break
        STATUS.update(online=False, voice_connected=False)
        log(
            f"discord_runtime_restart_scheduled delay_sec=2 "
            f"message_content={int(enable_message_content)}"
        )
        time.sleep(2.0)
    server.shutdown()
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
