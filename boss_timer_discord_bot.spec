# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path
import shutil
from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs


def collect_tree(src_dir: Path, dest_root: str) -> list[tuple[str, str]]:
    if not src_dir.exists():
        return []
    collected: list[tuple[str, str]] = []
    for item in src_dir.rglob("*"):
        if item.is_file():
            if item.suffix.lower() in {".log", ".jsonl", ".tmp", ".pyc"} or item.name == "precision_capture_latest.json":
                continue
            target_dir = Path(dest_root) / item.relative_to(src_dir).parent
            collected.append((str(item), str(target_dir)))
    return collected


project_root = Path(globals().get("__file__", "boss_timer_discord_bot.spec")).resolve().parent
bundled_ffmpeg_path = project_root / "ffmpeg.exe"
ffmpeg_path_text = str(os.environ.get("BOSS_TIMER_FFMPEG") or shutil.which("ffmpeg") or "").strip()
ffmpeg_path = Path(ffmpeg_path_text) if ffmpeg_path_text else bundled_ffmpeg_path
if ffmpeg_path is None or not ffmpeg_path.is_file():
    raise RuntimeError(
        "ffmpeg.exe를 찾지 못했습니다. PATH에 FFmpeg를 추가하거나 "
        "BOSS_TIMER_FFMPEG 환경변수로 ffmpeg.exe 경로를 지정하세요."
    )

# Bundle the base clips as well.  That keeps direct Discord soundboard commands
# usable even when the GUI resource temporary directory is no longer available.
datas = []
datas.extend(collect_tree(project_root / "voice", "voice"))
datas.extend(collect_tree(project_root / "wave", "wave"))
nacl_datas, nacl_binaries, nacl_hiddenimports = collect_all("nacl")
datas.extend(nacl_datas)
discord_binaries = collect_dynamic_libs("discord")

a = Analysis(
    ["boss_timer_discord_bot.py"],
    pathex=[],
    binaries=[(str(ffmpeg_path), "."), *nacl_binaries, *discord_binaries],
    datas=datas,
    hiddenimports=[
        "discord",
        "discord.voice_client",
        "discord.opus",
        "nacl",
        "nacl.secret",
        "nacl.utils",
        "cffi",
        "_cffi_backend",
        *nacl_hiddenimports,
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    name="boss_timer_discord_bot",
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
)
