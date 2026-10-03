"""Persistent user data; executable/resources deliberately remain portable.

Migration copies only missing files once, before defaults are seeded. Original
files are left intact. An existing AppData file (even empty) is never replaced.
"""
from pathlib import Path
from contextlib import contextmanager
import json
import os
import shutil
import tempfile
import uuid


LEGACY_FILES = (
    "boss_timer_settings.ini", "season_history.json", "schedule_state.json",
    "schedule_alarm_settings.json", "schedule_delete_history.json",
    "schedule_ocr_corrections.json", "boss_capture_records.json",
    "record_book_avg_cache.json", "schedule_github_versions.json",
    "schedule_github_servers.json", "background_music_settings.json",
    "background_music_video_cache.json",
    "schedule_alarm_voice_test_original.json", "schedule_alarm_voice_test_runtime.json",
)
LEGACY_DIRS = (
    "init", "archive_logs", "season_prestart_logs", "shared_schedules",
    "boss_capture_records", "cache/github_data", "notice_data", "update_ai",
)
MIGRATION_MARKER = "portable_data_migration_v1.json"


@contextmanager
def temporary_data_directory(parent, *, prefix):
    """Stage persistent data with its parent's Windows ACL, not a private ACL.

    tempfile's private directory ACL survives rename and hard-link publication.
    A directory under the managed data parent must inherit that parent's ACL
    so elevated creation does not lock out a later ordinary-user launch.
    """
    parent = Path(parent).resolve()
    if not prefix or Path(prefix).name != prefix or "/" in prefix or "\\" in prefix:
        raise ValueError("잘못된 임시 데이터 폴더 이름입니다.")
    temporary = parent / (prefix + uuid.uuid4().hex)
    os.mkdir(temporary, mode=0o777 if os.name == "nt" else 0o700)
    try:
        yield temporary
    finally:
        if temporary.is_symlink() or not temporary.resolve().is_relative_to(parent):
            raise ValueError("임시 데이터 폴더 밖의 경로는 정리하지 않습니다.")
        shutil.rmtree(temporary)


def copy_missing(source: Path, target: Path) -> bool:
    """Publish a complete file atomically, without replacing another writer."""
    if target.exists() or not source.is_file() or source.is_symlink():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".migrate-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as original:
            shutil.copyfileobj(original, output)
            output.flush()
            os.fsync(output.fileno())
        shutil.copystat(source, temporary)
        try:
            os.link(temporary, target)
        except FileExistsError:
            return False
        return True
    finally:
        os.unlink(temporary)


def migrate_legacy_data(app_root, data_root) -> list[str]:
    """Fail visibly on I/O errors; never continue startup with half-migrated data."""
    source, destination = Path(app_root).resolve(), Path(data_root).resolve()
    marker = destination / MIGRATION_MARKER
    if source == destination or marker.exists():
        return []
    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    for relative in LEGACY_FILES:
        if copy_missing(source / relative, destination / relative):
            copied.append(relative)
    for relative in LEGACY_DIRS:
        directory = source / relative
        if not directory.is_dir() or directory.is_symlink():
            continue
        for current, dirs, files in os.walk(directory, followlinks=False):
            dirs[:] = [d for d in dirs if d != "__pycache__" and not (Path(current) / d).is_symlink()]
            # Keep empty season directories too; history pruning relies on them.
            (destination / Path(current).relative_to(source)).mkdir(parents=True, exist_ok=True)
            for name in files:
                origin = Path(current) / name
                relative_file = origin.relative_to(source)
                if copy_missing(origin, destination / relative_file):
                    copied.append(relative_file.as_posix())
    atomic_write(marker, (json.dumps({"source": str(source), "copied": copied},
                                   ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return copied


def atomic_write(path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".save-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
