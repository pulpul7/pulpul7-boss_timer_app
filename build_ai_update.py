"""Manual AI-only packaging. Nothing is generated merely by importing this file.

Run explicitly: python build_ai_update.py
Never invoked by the GUI, updater, or normal application build.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

from ai_module_updater import _safe_member, inspect_package


def package_bytes(source: Path, python_version=None):
    source = Path(source)
    manifest = json.loads((source / "metadata.json").read_text(encoding="utf-8"))
    manifest["python_version"] = python_version or f"{sys.version_info.major}.{sys.version_info.minor}"
    files = {}
    for path in sorted((source / "payload").rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc" or not path.is_file():
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError(f"모듈 소스 밖의 링크는 패키징할 수 없습니다: {path}")
        name = path.relative_to(source).as_posix()
        _safe_member(name)
        files[name] = path.read_bytes()
    manifest["files"] = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("module.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for name, data in files.items():
            archive.writestr(name, data)
    data = stream.getvalue()
    row = {"version": manifest["version"], "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    # Validate against the running interpreter: use the matching Python when
    # building for another executable/runtime, rather than spoofing its version.
    inspect_package(data, row, manifest["min_app_version"])
    return data, manifest


def main():
    parser = argparse.ArgumentParser(description="공지 모듈 ZIP 수동 생성 (본체 EXE 빌드 안 함)")
    parser.add_argument("--output-directory", type=Path, default=Path(__file__).resolve().parent / "dist_AI")
    args = parser.parse_args()
    data, manifest = package_bytes(Path(__file__).resolve().parent / "notice_module")
    directory = args.output_directory.resolve()
    target = directory / f"Update_AI_v{manifest['version']}.zip"
    directory.mkdir(parents=True, exist_ok=True)
    # Existing releases are never silently overwritten.
    with target.open("xb") as file:
        file.write(data)
    print(f"Created: {target}")
    print(f"SHA-256: {hashlib.sha256(data).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
