"""Build the GUI and its Discord companion into one distributable folder.

Run this file instead of invoking the GUI PyInstaller spec on its own:

    python build_release.py

Both executables are required at runtime and are also bundled into a ZIP in
``dist`` for GitHub Releases.
"""

from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
GUI_SPEC = ROOT / "boss_timer_gui.spec"
BOT_SPEC = ROOT / "boss_timer_discord_bot.spec"
GUI_EXE = DIST_DIR / "boss_timer_gui.exe"
BOT_EXE = DIST_DIR / "boss_timer_discord_bot.exe"


def build_version() -> str:
    try:
        spec_text = GUI_SPEC.read_text(encoding="utf-8")
    except OSError:
        return "v5.0.0"
    match = re.search(r'^BUILD_VERSION\s*=\s*["\']([^"\']+)["\']', spec_text, re.MULTILINE)
    return match.group(1).strip() if match else "v5.0.0"


def run_pyinstaller(spec_path: Path, work_name: str) -> None:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(ROOT / work_name),
        str(spec_path),
    ]
    subprocess.run(command, cwd=ROOT, check=True)


def create_release_zip(version: str) -> Path:
    archive_path = DIST_DIR / f"BossTimer-{version}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(GUI_EXE, GUI_EXE.name)
        archive.write(BOT_EXE, BOT_EXE.name)
    return archive_path


def main() -> int:
    if not (ROOT / "ffmpeg.exe").is_file():
        raise FileNotFoundError("ffmpeg.exe가 프로젝트 루트에 필요합니다.")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    run_pyinstaller(BOT_SPEC, "build_release_discord_bot")
    run_pyinstaller(GUI_SPEC, "build_release_gui")
    missing = [path.name for path in (GUI_EXE, BOT_EXE) if not path.is_file()]
    if missing:
        raise RuntimeError(f"빌드 결과가 없습니다: {', '.join(missing)}")
    archive_path = create_release_zip(build_version())
    print(f"Release ready: {archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
