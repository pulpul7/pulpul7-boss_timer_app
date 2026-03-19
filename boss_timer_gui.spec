# -*- mode: python ; coding: utf-8 -*-

import json
from datetime import datetime
from pathlib import Path
import subprocess
import sys


def collect_tree(src_dir: Path, dest_root: str) -> list[tuple[str, str]]:
    if not src_dir.exists():
        return []
    collected: list[tuple[str, str]] = []
    for item in src_dir.rglob("*"):
        if item.is_file():
            relative_parent = item.relative_to(src_dir).parent
            target_dir = Path(dest_root) / relative_parent
            collected.append((str(item), str(target_dir)))
    return collected


python_root = Path(sys.executable).resolve().parent
dll_dir = python_root / "DLLs"
tcl_root = python_root / "tcl"
project_root = Path(globals().get("__file__", "boss_timer_gui.spec")).resolve().parent


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
    tag_lines = read_git_text(["tag", "--sort=-creatordate"]).splitlines()
    last_updated = read_git_text(["log", "-1", "--format=%cs"])
    detail_version = read_git_text(["describe", "--tags", "--always", "--dirty"])
    metadata = {
        "author": "나츠",
        "version": tag_lines[0].strip() if tag_lines else "v 2.0.0.Beta",
        "last_updated": last_updated or "2026-03-17",
        "build_detail_version": detail_version or "unknown",
        "build_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    metadata_path = project_root / "build_metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata_path


build_metadata_path = write_build_metadata()

datas = [
    (str(build_metadata_path), "."),
    ("assets\\기본배경.png", "assets"),
    ("assets\\벽지.png", "assets"),
    ("assets\\장원영.png", "assets"),
]
datas += collect_tree(Path("icons"), "icons")
datas += collect_tree(tcl_root / "tcl8.6", "tcl8.6")
datas += collect_tree(tcl_root / "tk8.6", "tk8.6")

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
    hookspath=[],
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
)
