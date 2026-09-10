# -*- mode: python ; coding: utf-8 -*-

import ast
import configparser
import json
import os
import shutil
from datetime import datetime
from pathlib import Path
import subprocess
import sys


def collect_tree(
    src_dir: Path,
    dest_root: str,
    *,
    excluded_relative_paths: set[str] | None = None,
    excluded_relative_prefixes: set[str] | None = None,
) -> list[tuple[str, str]]:
    if not src_dir.exists():
        return []
    excluded = {str(path).replace("\\", "/") for path in (excluded_relative_paths or set())}
    excluded_prefixes = {
        str(path).replace("\\", "/").strip("/")
        for path in (excluded_relative_prefixes or set())
    }
    collected: list[tuple[str, str]] = []
    for item in src_dir.rglob("*"):
        if item.is_file():
            relative_path = item.relative_to(src_dir)
            relative_text = relative_path.as_posix()
            if relative_text in excluded or any(
                relative_text == prefix or relative_text.startswith(f"{prefix}/")
                for prefix in excluded_prefixes
            ):
                continue
            relative_parent = relative_path.parent
            target_dir = Path(dest_root) / relative_parent
            collected.append((str(item), str(target_dir)))
    return collected


python_root = Path(sys.executable).resolve().parent
dll_dir = python_root / "DLLs"
tcl_root = python_root / "tcl"
project_root = Path(globals().get("__file__", "boss_timer_gui.spec")).resolve().parent
BUILD_VERSION = "v5.0.0"
BUILD_LAST_UPDATED = "2026-09-02"
DISTRIBUTION_DEFAULT_SETTING_OVERRIDES = {
    "schedule_share_exclude_elapsed": "True",
}
DISTRIBUTION_DEFAULT_ALARM_OVERRIDES = {
    "ai_recording_preferred": True,
    "second_precision_expire_hours": 168,
}
# 배포본은 음성 캐시만 기본 데이터로 포함한다. 아래 파일은 사용자의 서버,
# 봇 채널, 스케쥴을 담을 수 있으므로 어떤 경우에도 패키지에 들어가면 안 된다.
DISTRIBUTION_PRIVATE_RUNTIME_FILENAMES = {
    "discord_bot.ini",
    "discord_voice_commands.json",
    "active_server_profile.json",
    "schedule_state.json",
    "schedule_delete_history.json",
    "discord_voice_queue.jsonl",
}


def read_distribution_default_setting_keys() -> tuple[str, ...]:
    source_path = project_root / "boss_timer_gui.py"
    try:
        syntax_tree = ast.parse(source_path.read_text(encoding="utf-8-sig"), filename=str(source_path))
    except (OSError, SyntaxError):
        return ()
    for node in syntax_tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "DEFAULT_SETTINGS_SEED_KEYS" for target in node.targets):
            continue
        try:
            values = ast.literal_eval(node.value)
        except (TypeError, ValueError, SyntaxError):
            return ()
        if isinstance(values, (list, tuple)):
            return tuple(str(value) for value in values if str(value).strip())
    return ()


def build_distribution_default_seed_datas() -> list[tuple[str, str]]:
    staging_dir = project_root / "build" / "distribution_defaults"
    staging_dir.mkdir(parents=True, exist_ok=True)
    generated_datas: list[tuple[str, str]] = []

    runtime_settings_path = project_root / "boss_timer_settings.ini"
    setting_keys = read_distribution_default_setting_keys()
    if runtime_settings_path.exists() and setting_keys:
        runtime_config = configparser.ConfigParser()
        try:
            runtime_config.read(runtime_settings_path, encoding="utf-8")
            runtime_settings = runtime_config["settings"]
        except (OSError, KeyError, configparser.Error):
            runtime_settings = None
        if runtime_settings is not None:
            seed_config = configparser.ConfigParser()
            seed_config["settings"] = {
                key: str(runtime_settings[key])
                for key in setting_keys
                if key in runtime_settings
            }
            seed_config["settings"].update(DISTRIBUTION_DEFAULT_SETTING_OVERRIDES)
            background_path = str(seed_config["settings"].get("background_path", "") or "").strip()
            if background_path:
                try:
                    relative_background_path = Path(background_path).resolve().relative_to(project_root)
                except (OSError, ValueError):
                    pass
                else:
                    seed_config["settings"]["background_path"] = relative_background_path.as_posix()
            settings_seed_path = staging_dir / "default_settings.ini"
            with settings_seed_path.open("w", encoding="utf-8") as file:
                seed_config.write(file)
            generated_datas.append((str(settings_seed_path), "init"))

    runtime_alarm_path = project_root / "schedule_alarm_settings.json"
    if runtime_alarm_path.exists():
        try:
            alarm_payload = json.loads(runtime_alarm_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            alarm_payload = None
        if isinstance(alarm_payload, dict):
            alarm_payload.update(DISTRIBUTION_DEFAULT_ALARM_OVERRIDES)
            alarm_payload["version"] = BUILD_VERSION
            alarm_seed_path = staging_dir / "default_schedule_alarm_settings.json"
            alarm_seed_path.write_text(
                json.dumps(alarm_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            generated_datas.append((str(alarm_seed_path), "init"))

    # These are shared gameplay/display defaults, unlike schedules and
    # Discord/server credentials.  Seed them from the currently active local
    # server profile when available so a fresh distribution starts with the
    # verified boss and break-time configuration.
    appdata_root = Path(os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or "") / "BossTimer"
    active_profile_path = appdata_root / "active_server_profile.json"
    profile_init_dir: Path | None = None
    try:
        active_profile = json.loads(active_profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        active_profile = None
    if isinstance(active_profile, dict):
        server_id = str(active_profile.get("server_id") or "").strip()
        season_key = str(active_profile.get("season_key") or "").strip()
        candidate_dirs = []
        if server_id and season_key:
            candidate_dirs.append(appdata_root / "server_profiles" / season_key / server_id / "init")
        if server_id:
            candidate_dirs.append(appdata_root / "server_profiles" / server_id / "init")
        for candidate_dir in candidate_dirs:
            if candidate_dir.is_dir():
                profile_init_dir = candidate_dir
                break
    if profile_init_dir is not None:
        for filename in (
            "schedule_boss_metrics.json",
            "schedule_break_rules.json",
            "schedule_boss_definitions.txt",
            "schedule_area_definitions.txt",
            "schedule_fixed_bosses.txt",
        ):
            source_path = profile_init_dir / filename
            if not source_path.is_file():
                continue
            seed_path = staging_dir / filename
            shutil.copy2(source_path, seed_path)
            generated_datas.append((str(seed_path), "init"))
    return generated_datas


def read_git_text(args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except Exception:
        return ""
    return completed.stdout.strip()


def write_build_metadata() -> Path:
    last_updated = read_git_text(["log", "-1", "--format=%cs"])
    working_tree_dirty = bool(read_git_text(["status", "--porcelain"]))
    build_datetime = datetime.now()
    resolved_version = BUILD_VERSION
    detail_version = resolved_version
    metadata = {
        "author": "\ub098\uce20",
        "version": resolved_version,
        "last_updated": build_datetime.strftime("%Y-%m-%d") if working_tree_dirty else (last_updated or BUILD_LAST_UPDATED),
        "build_detail_version": detail_version or resolved_version,
        "build_timestamp": build_datetime.strftime("%Y-%m-%d %H:%M:%S"),
    }
    metadata_path = project_root / "build_metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8")
    return metadata_path


def assert_distribution_has_no_private_runtime_data(datas: list[tuple[str, str]]) -> None:
    """Fail the build instead of accidentally shipping a developer's server data."""
    for source_path, _destination in datas:
        source = Path(source_path)
        normalized_parts = {part.casefold() for part in source.parts}
        if source.name.casefold() in DISTRIBUTION_PRIVATE_RUNTIME_FILENAMES:
            raise RuntimeError(f"Private runtime file must not be packaged: {source}")
        if "server_profiles" in normalized_parts:
            raise RuntimeError(f"Server profile data must not be packaged: {source}")


distribution_default_datas = build_distribution_default_seed_datas()
build_metadata_path = write_build_metadata()

datas = [
    (str(build_metadata_path), "."),
]
datas += collect_tree(project_root / "assets", "assets")
datas += collect_tree(
    project_root / "init",
    "init",
    excluded_relative_paths={Path(source_path).name for source_path, _destination in distribution_default_datas},
)
datas += distribution_default_datas
datas += collect_tree(project_root / "icons", "icons")
datas += collect_tree(project_root / "voice", "voice")
datas += collect_tree(project_root / "wave", "wave")
datas += collect_tree(project_root / "user_voice", "user_voice")
# The command/ subtree is produced by Discord soundboard TTS requests at
# runtime.  It is server/user activity, not a distribution cache seed.
datas += collect_tree(
    project_root / "tts_캐쉬",
    "tts_캐쉬",
    excluded_relative_prefixes={"command"},
)
datas += collect_tree(tcl_root / "tcl8.6", "_tcl_data")
datas += collect_tree(tcl_root / "tk8.6", "_tk_data")
assert_distribution_has_no_private_runtime_data(datas)

binaries = []
for dll_name in ("tcl86t.dll", "tk86t.dll"):
    dll_path = dll_dir / dll_name
    if dll_path.exists():
        binaries.append((str(dll_path), "."))


a = Analysis(
    ["boss_timer_gui.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    # edge-tts itself remains in the optional module ZIP.  aiohttp inside that
    # ZIP imports these stdlib modules dynamically, so PyInstaller cannot see
    # them while analysing the main program.
    hiddenimports=[
        "tkinter",
        "_tkinter",
        "http.cookies",
        "http.client",
        "email",
        "email.feedparser",
        "email.message",
        "email.parser",
        "mimetypes",
        "netrc",
        "ssl",
    ],
    hookspath=["pyinstaller_hooks"],
    hooksconfig={},
    runtime_hooks=["pyi_rth_tkinter_fix.py"],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="boss_timer_gui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    uac_admin=True,
)
