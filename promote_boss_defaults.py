"""Promote only the active profile's boss/fixed settings. Never builds."""
import argparse
import configparser
from datetime import datetime
import json
import os
from pathlib import Path
import shutil

from promote_current_defaults import atomic_write, config_bytes


FILES = {
    "boss": ("schedule_boss_definitions.txt", "schedule_area_definitions.txt"),
    "fixed": ("schedule_fixed_bosses.txt",),
}
CONFIG_KEYS = {
    "schedule_color_data_enabled", "schedule_share_use_boss_colors",
    "schedule_invasion_last_text_color", "schedule_invasion_last_bg_color",
    "fixed_boss_color_data_enabled", "schedule_share_use_fixed_boss_colors",
    "fixed_boss_last_text_color", "fixed_boss_last_bg_color",
}


def promote(root, data, *, apply=False, include_spectrum=False):
    root, data = Path(root).resolve(), Path(data).resolve()
    inputs, writes, counts = {}, {}, {}

    def read(path):
        path = Path(path)
        value = path.read_bytes()
        inputs[path] = value
        return value

    active_path = data / "active_server_profile.json"
    active = json.loads(read(active_path).decode("utf-8-sig"))
    season, server = (str(active[key]) for key in ("season_key", "server_id"))
    if any(not part or part in {".", ".."} or any(c in part for c in '/\\:') for part in (season, server)):
        raise ValueError("Invalid active profile")
    profile = data / "server_profiles" / season / server
    if not profile.resolve().is_relative_to(data / "server_profiles"):
        raise ValueError("Profile path escapes the data directory")
    baseline = profile / "settings_rollback/baseline"
    manifest = json.loads(read(baseline / "manifest.json").decode("utf-8-sig"))
    groups = manifest["groups"]
    if not isinstance(groups, dict):
        raise ValueError("Invalid rollback manifest")
    for group, names in FILES.items():
        entries = []
        for name in names:
            source = profile / "init" / name
            value = read(source)
            rows = [line for line in value.decode("utf-8-sig").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
            minimum = 5 if "area" in name else 8
            if not rows or any(len(line.split("|")) < minimum for line in rows):
                raise ValueError("Incomplete boss settings: " + name)
            counts[name] = len(rows)
            for target in (root / "init" / name, data / "init" / name, baseline / "init" / name):
                writes[target] = value  # Preserve every field, order, color and flag verbatim.
            entries.append(dict(source=str(source), snapshot="init/" + name, exists=True))
        groups[group] = entries

    live_config = data / "boss_timer_settings.ini"
    if not live_config.is_file():
        live_config = root / "boss_timer_settings.ini"
    current = configparser.ConfigParser()
    current.read_string(read(live_config).decode("utf-8-sig"))
    for target in (root / "init/default_settings.ini", data / "init/default_settings.ini",
                   baseline / "boss_timer_settings.ini"):
        config = configparser.ConfigParser()
        config.read_string(read(target).decode("utf-8-sig"))
        if not config.has_section("settings"):
            config.add_section("settings")
        for key in CONFIG_KEYS:
            if key in current["settings"]:
                config["settings"][key] = current["settings"][key]
            else:
                config["settings"].pop(key, None)
        writes[target] = config_bytes(config)
    if include_spectrum:
        from fixed_boss_aliases import migrate_spectrum_numbers
        from web_boss_spectrum import read_numbers
        spectrum = data / "web_service/boss_spectrum.json"
        if not spectrum.is_file():
            spectrum = root / "web_assets/boss_spectrum.json"
        read(spectrum)
        numbers = migrate_spectrum_numbers(read_numbers(spectrum))
        value = (json.dumps(dict(schema=2, target="background", numbers=numbers), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        writes[root / "web_assets/boss_spectrum.json"] = value
        writes[data / "web_service/boss_spectrum.json"] = value
    manifest.update(refreshed_at=datetime.now().astimezone().isoformat(),
                    refreshed_groups=["boss", "fixed"], source_type="current_boss_settings")
    writes[baseline / "manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    result = dict(profile=str(profile), counts=counts, updated_files=len(writes), applied=False)
    if not apply:
        return result

    previous = {target: target.read_bytes() if target.exists() else None for target in writes}
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup = root / "settings_seed_backups" / ("boss_defaults_" + stamp)
    for target, value in previous.items():
        if value is None:
            continue
        scope, relative = ("project", target.relative_to(root)) if target.is_relative_to(root) else ("appdata", target.relative_to(data))
        saved = backup / scope / relative
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, saved)
    if any(path.read_bytes() != value for path, value in inputs.items()):
        raise RuntimeError("Settings changed during backup; defaults were not updated")
    if any((target.read_bytes() if target.exists() else None) != value for target, value in previous.items()):
        raise RuntimeError("Destination changed during backup; defaults were not updated")
    committed = []
    try:
        for target, value in writes.items():
            atomic_write(target, value)
            committed.append(target)
        if any(target.read_bytes() != value for target, value in writes.items()):
            raise RuntimeError("Promoted defaults verification failed")
    except Exception:
        for target in reversed(committed):
            if previous[target] is None:
                target.unlink()
            else:
                atomic_write(target, previous[target])
        raise
    return dict(result, applied=True, backup=str(backup))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-spectrum", action="store_true")
    args = parser.parse_args()
    print(json.dumps(promote(Path(__file__).resolve().parent, Path(os.environ["APPDATA"]) / "BossTimer",
                             apply=args.apply, include_spectrum=args.include_spectrum), ensure_ascii=False))
