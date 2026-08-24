# -*- mode: python ; coding: utf-8 -*-

import ast
import configparser
import json
from datetime import datetime
from pathlib import Path
import subprocess
import sys


def collect_tree(
    src_dir: Path,
    dest_root: str,
    *,
    excluded_relative_paths: set[str] | None = None,
) -> list[tuple[str, str]]:
    if not src_dir.exists():
        return []
    excluded = {str(path).replace("\\", "/") for path in (excluded_relative_paths or set())}
    collected: list[tuple[str, str]] = []
    for item in src_dir.rglob("*"):
        if item.is_file():
            relative_path = item.relative_to(src_dir)
            if relative_path.as_posix() in excluded:
                continue
            relative_parent = relative_path.parent
            target_dir = Path(dest_root) / relative_parent
            collected.append((str(item), str(target_dir)))
    return collected


python_root = Path(sys.executable).resolve().parent
dll_dir = python_root / "DLLs"
tcl_root = python_root / "tcl"
project_root = Path(globals().get("__file__", "boss_timer_gui.spec")).resolve().parent
BUILD_VERSION = "v3.0.0"
BUILD_LAST_UPDATED = "2026-04-17"
DISTRIBUTION_DEFAULT_SETTING_OVERRIDES = {
    "schedule_share_exclude_elapsed": "True",
}
DISTRIBUTION_DEFAULT_ALARM_OVERRIDES = {
    "countdown_ai_voice_enabled": True,
    "boss_ai_voice_enabled": True,
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
            alarm_seed_path = staging_dir / "default_schedule_alarm_settings.json"
            alarm_seed_path.write_text(
                json.dumps(alarm_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            generated_datas.append((str(alarm_seed_path), "init"))
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
    latest_tag = read_git_text(["tag", "--sort=-creatordate"])
    last_updated = read_git_text(["log", "-1", "--format=%cs"])
    detail_version = read_git_text(["describe", "--tags", "--always", "--dirty"])
    working_tree_dirty = bool(read_git_text(["status", "--porcelain"]))
    build_datetime = datetime.now()
    resolved_version = latest_tag.splitlines()[0].strip() if latest_tag else ""
    if not resolved_version:
        resolved_version = BUILD_VERSION
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
datas += collect_tree(tcl_root / "tcl8.6", "_tcl_data")
datas += collect_tree(tcl_root / "tk8.6", "_tk_data")

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
    hiddenimports=["tkinter", "_tkinter"],
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
