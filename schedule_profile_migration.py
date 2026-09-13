"""Carry settings, not schedules, across seasons for the same server."""
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


SETTINGS_FILES = (
    "init/schedule_area_definitions.txt", "init/schedule_boss_definitions.txt",
    "init/schedule_fixed_bosses.txt", "init/schedule_boss_metrics.json",
    "init/schedule_break_rules.json", "schedule_alarm_settings.json",
    "schedule_ocr_corrections.json", "discord_bot.ini", "discord_voice_commands.json",
)

DEFAULT_GROUPS = {
    "boss": ("init/schedule_boss_definitions.txt", "init/schedule_area_definitions.txt"),
    "fixed": ("init/schedule_fixed_bosses.txt",),
    "metrics": ("init/schedule_boss_metrics.json",),
    "breaks": ("init/schedule_break_rules.json",),
    "alarm": ("schedule_alarm_settings.json",),
}


def distribution_source(resource_init, relative):
    relative = relative.replace("\\", "/")
    if relative == "schedule_alarm_settings.json":
        return Path(resource_init) / "default_schedule_alarm_settings.json"
    if relative == "global/edge_tts.ini":
        return Path(resource_init) / "default_edge_tts.ini"
    if relative in SETTINGS_FILES and relative.startswith("init/"):
        return Path(resource_init) / Path(relative).name
    raise ValueError(f"배포 기본설정에 없는 파일: {relative}")


def build_distribution_baseline(resource_init, destination, groups, *, server_id="", season_key="season_unset"):
    """Snapshot shipped defaults, never whatever happens to be live on first use."""
    destination = Path(destination)
    plan = []
    for group, entries in groups.items():
        for target, relative in entries:
            original = distribution_source(resource_init, relative)
            if not original.is_file():
                raise ValueError(f"배포 기본설정 파일이 없습니다: {original.name}")
            plan.append((group, str(target), relative, original))
    manifest = {"schema_version": 1, "server_id": server_id, "season_key": season_key,
                "source_type": "distribution_defaults", "groups": {}, "config_snapshot": ""}
    destination.mkdir(parents=True, exist_ok=True)
    for group, target, relative, original in plan:
        copied = destination / relative
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, copied)
        manifest["groups"].setdefault(group, []).append({"source": target, "snapshot": relative, "exists": True})
    config = Path(resource_init) / "default_settings.ini"
    if config.is_file():
        shutil.copy2(config, destination / "boss_timer_settings.ini")
        manifest["config_snapshot"] = "boss_timer_settings.ini"
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def ensure_profile_defaults(profile, resource_init):
    """Fill absent settings only, independently of whether any editor was opened."""
    profile = Path(profile)
    groups = {group: [(str(profile / relative), relative) for relative in files]
              for group, files in DEFAULT_GROUPS.items()}
    # Validate the full distribution before publishing any missing setting.
    for entries in groups.values():
        for _, relative in entries:
            if not distribution_source(resource_init, relative).is_file():
                raise ValueError(f"배포 기본설정 파일이 없습니다: {relative}")
    for entries in groups.values():
        for target, relative in entries:
            target = Path(target)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".setting-seed-", dir=target.parent) as temporary:
                staged = Path(temporary) / "setting"
                shutil.copy2(distribution_source(resource_init, relative), staged)
                try:
                    # Publish without replacing an existing file (including races).
                    os.link(staged, target)
                except FileExistsError:
                    pass
    baseline = profile / "settings_rollback/baseline"
    if not baseline.exists():
        baseline.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".baseline-seed-", dir=baseline.parent) as temporary:
            stage = Path(temporary) / "baseline"
            build_distribution_baseline(resource_init, stage, groups, server_id=profile.name, season_key=profile.parent.name)
            stage.rename(baseline)


def rebound_manifest(source_profile, target_profile):
    source_profile, target_profile = map(Path, (source_profile, target_profile))
    baseline = source_profile / "settings_rollback" / "baseline"
    manifest = json.loads((baseline / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("groups"), dict):
        raise ValueError("롤백 기준점 형식을 확인할 수 없습니다.")
    if str(manifest.get("server_id")) != source_profile.name or source_profile.name != target_profile.name:
        raise ValueError("다른 서버의 롤백 기준점은 이어받지 않습니다.")
    for path in baseline.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(baseline.resolve()):
            raise ValueError("롤백 폴더 밖을 가리키는 경로입니다.")
    for entries in manifest["groups"].values():
        for entry in entries:
            original = Path(entry.get("source") or "")
            if original.is_absolute() and original.is_relative_to(source_profile):
                entry["source"] = str(target_profile / original.relative_to(source_profile))
    manifest["server_id"] = target_profile.name
    manifest["season_key"] = target_profile.parent.name
    manifest["inherited_from"] = str(source_profile)
    return manifest


def copy_baseline(source_profile, target_profile, destination):
    """Copy a fixed baseline, rebinding its destinations to the new profile."""
    source_profile, target_profile, destination = map(Path, (source_profile, target_profile, destination))
    baseline = source_profile / "settings_rollback" / "baseline"
    if not baseline.is_dir():
        return False
    manifest = rebound_manifest(source_profile, target_profile)
    shutil.copytree(baseline, destination)
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def seed_profile(profiles_root, season_key, server_id, *, preferred_season_key=None):
    """Publish a complete new profile atomically; existing profiles are untouched.

    Legacy flat data is used only for the first migration of that server. Once
    season profiles exist, it must never resurrect that old snapshot again.
    """
    root = Path(profiles_root).resolve()
    if not re.fullmatch(r"season_(?:\d+|unset)", season_key) or not re.fullmatch(r"[\w-]+", server_id, re.ASCII):
        raise ValueError("잘못된 서버/시즌 경로입니다.")
    target = root / season_key / server_id
    if not target.resolve().is_relative_to(root):
        raise ValueError("서버 폴더 밖에는 설정을 복사하지 않습니다.")
    if target.exists():
        return None
    candidates = []
    has_season_profile = False
    season_no = int(season_key[7:]) if season_key != "season_unset" else 0
    for folder in root.glob("season_*"):
        match = re.fullmatch(r"season_(\d+)", folder.name)
        profile = folder / server_id
        if not profile.is_dir():
            continue
        has_season_profile = True
        if match and int(match[1]) < season_no:
            candidates.append((int(match[1]), profile))
    candidates.sort(reverse=True)
    source = candidates[0][1] if candidates else None
    for _, candidate in candidates:
        if candidate.parent.name == preferred_season_key:
            source = candidate
            break
    legacy = source is None and not has_season_profile
    if legacy:
        source = root / server_id
    if source is None or not source.is_dir():
        return None
    if not source.resolve().is_relative_to(root):
        raise ValueError("서버 폴더 밖의 설정은 이어받지 않습니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    # TemporaryDirectory owns only this unique staging directory, never a profile.
    with tempfile.TemporaryDirectory(prefix=".settings-seed-", dir=target.parent) as temporary:
        stage = Path(temporary) / "profile"
        if legacy:
            shutil.copytree(source, stage)
            baseline_manifest = stage / "settings_rollback/baseline/manifest.json"
            if baseline_manifest.is_file():
                manifest = rebound_manifest(source, target)
                baseline_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            stage.mkdir()
            for relative in SETTINGS_FILES:
                original = source / relative
                if original.is_file():
                    if not original.resolve().is_relative_to(source.resolve()):
                        raise ValueError("서버 폴더 밖의 설정 파일입니다.")
                    copied = stage / relative
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, copied)
            copy_baseline(source, target, stage / "settings_rollback" / "baseline")
        stage.rename(target)
    return str(source)
