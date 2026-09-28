"""Manual, scoped promotion of current environment/chimes (never a build).

No schedule, boss definition, Discord credential, or other server is changed.
Run only when the user explicitly requests replacing defaults/rollback.
"""
import ast
import configparser
from datetime import datetime
from io import StringIO
import json
import os
from pathlib import Path
import shutil

from runtime_storage import atomic_write, migrate_legacy_data


def ini_bytes(config):
    stream = StringIO()
    config.write(stream)
    return stream.getvalue().encode("utf-8")


def main():
    root = Path(__file__).resolve().parent
    data = Path(os.environ["APPDATA"]) / "BossTimer"
    migrate_legacy_data(root, data)
    selection_path = data / "active_server_profile.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8-sig"))
    parts = [str(selection[key]) for key in ("season_key", "server_id")]
    if any(not part or part in {".", ".."} or any(c in part for c in '/\\:') for part in parts):
        raise ValueError("Invalid active profile")
    profile = data / "server_profiles" / parts[0] / parts[1]
    baseline = profile / "settings_rollback/baseline"
    alarm_path = profile / "schedule_alarm_settings.json"
    settings_path = data / "boss_timer_settings.ini"
    inputs = {path: path.read_bytes() for path in (
        alarm_path, settings_path, baseline / "manifest.json", baseline / "boss_timer_settings.ini")}
    alarm = json.loads(inputs[alarm_path].decode("utf-8-sig"))
    for key in ("general", "fixed", "rapid_chain"):
        raw = str(alarm.get("chime_settings", {}).get(key) or "")
        if not raw:
            raise ValueError(f"Current chime is empty: {key}")
        path = (root / raw).resolve()
        relative = path.relative_to(root)
        if relative.parts[0] != "wave" or not path.is_file():
            raise ValueError(f"Selected chime not found in wave: {key}")
        alarm["chime_settings"][key] = relative.as_posix()
    alarm_bytes = (json.dumps(alarm, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    tree = ast.parse((root / "boss_timer_gui.py").read_text(encoding="utf-8-sig"))
    constants = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"DEFAULT_SETTINGS_SEED_KEYS", "GITHUB_TOKEN_RUNTIME_SETTING_KEYS"}:
                    constants[target.id] = ast.literal_eval(node.value)
    allowed = set(constants["DEFAULT_SETTINGS_SEED_KEYS"]) - set(constants["GITHUB_TOKEN_RUNTIME_SETTING_KEYS"])
    current = configparser.ConfigParser()
    current.read_string(inputs[settings_path].decode("utf-8-sig"))
    seed = configparser.ConfigParser()
    seed["settings"] = {key: value for key, value in current["settings"].items() if key in allowed}
    background = seed["settings"].get("background_path", "")
    if Path(background).is_absolute():
        try:
            seed["settings"]["background_path"] = Path(background).resolve().relative_to(root).as_posix()
        except ValueError:
            # A personal external asset must not become a broken release default.
            seed["settings"].pop("background_path", None)
    saved = configparser.ConfigParser()
    saved.read_string(inputs[baseline / "boss_timer_settings.ini"].decode("utf-8-sig"))
    if not saved.has_section("settings"):
        saved.add_section("settings")
    saved["settings"].update(seed["settings"])
    manifest = json.loads(inputs[baseline / "manifest.json"].decode("utf-8-sig"))
    alarm_entries = manifest.setdefault("groups", {}).setdefault("alarm", [])
    alarm_entries[:] = [entry for entry in alarm_entries
                       if str(entry.get("snapshot", "")).replace("\\", "/") != "schedule_alarm_settings.json"]
    alarm_entries.insert(0, dict(source=str(alarm_path), snapshot="schedule_alarm_settings.json", exists=True))
    manifest.update(refreshed_at=datetime.now().isoformat(), refreshed_groups=["environment", "alarm"],
                    config_snapshot="boss_timer_settings.ini")
    writes = {
        root / "init/default_settings.ini": ini_bytes(seed),
        data / "init/default_settings.ini": ini_bytes(seed),
        root / "init/default_schedule_alarm_settings.json": alarm_bytes,
        data / "init/default_schedule_alarm_settings.json": alarm_bytes,
        root / "schedule_alarm_settings.json": alarm_bytes,
        data / "schedule_alarm_settings.json": alarm_bytes,
        baseline / "schedule_alarm_settings.json": alarm_bytes,
        baseline / "boss_timer_settings.ini": ini_bytes(saved),
        baseline / "manifest.json": (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    }
    backup = root / "settings_seed_backups" / ("environment_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    for target in writes:
        if target.exists():
            relative = (Path("appdata") / target.relative_to(data) if target.is_relative_to(data)
                        else Path("project") / target.relative_to(root))
            destination = backup / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, destination)
    if json.loads(selection_path.read_text(encoding="utf-8-sig")) != selection:
        raise RuntimeError("Server changed; promotion cancelled")
    if any(path.read_bytes() != value for path, value in inputs.items()):
        raise RuntimeError("Settings changed; promotion cancelled")
    for target, contents in writes.items():
        atomic_write(target, contents)
    if any(target.read_bytes() != contents for target, contents in writes.items()):
        raise RuntimeError("Verification failed")
    print(json.dumps(dict(profile=str(profile), backup=str(backup), updated_files=len(writes),
                          chimes=alarm["chime_settings"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
