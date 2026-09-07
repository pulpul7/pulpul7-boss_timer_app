"""Build the separately downloadable Edge TTS module ZIP.

Run this on the same Python major/minor version used to make the BossTimer EXE:
    python build_edge_tts_module.py

Upload the resulting ZIP to the GitHub Release tag printed by this script.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from edge_tts_module import EDGE_TTS_MODULE_ASSET_NAME, EDGE_TTS_MODULE_VERSION


PROJECT_ROOT = Path(__file__).resolve().parent
REQUIREMENTS_PATH = PROJECT_ROOT / "requirements-tts-module.txt"
DIST_DIR = PROJECT_ROOT / "dist" / "tts_module"


def main() -> int:
    work_dir = Path(tempfile.mkdtemp(prefix="boss_timer_tts_module_"))
    module_root = work_dir / "boss_timer_edge_tts_module"
    package_root = module_root / "packages"
    try:
        package_root.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "--no-compile",
                "--target",
                str(package_root),
                "-r",
                str(REQUIREMENTS_PATH),
            ],
            check=True,
        )
        manifest = {
            "module": "boss_timer_edge_tts",
            "version": EDGE_TTS_MODULE_VERSION,
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "asset": EDGE_TTS_MODULE_ASSET_NAME,
        }
        (module_root / "module.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        output_path = DIST_DIR / EDGE_TTS_MODULE_ASSET_NAME
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in module_root.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(module_root).as_posix())
        print(output_path)
        print(f"GitHub Release tag: tts-module-v{EDGE_TTS_MODULE_VERSION}")
        return 0
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
