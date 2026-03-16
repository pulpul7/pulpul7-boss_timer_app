# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
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

datas = [
    ("assets\\기본배경.png", "assets"),
    ("assets\\벽지.png", "assets"),
    ("assets\\장원영.png", "assets"),
]
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
