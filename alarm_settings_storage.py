"""Immutable shipped defaults and ordered, atomic per-profile alarm saves."""
from copy import deepcopy
import json
from pathlib import Path
import threading
from runtime_storage import atomic_write

_lock = threading.RLock()
_revisions = {}


def load_defaults(resource_init, fallback):
    result = deepcopy(fallback)
    path = Path(resource_init) / 'default_schedule_alarm_settings.json'
    try:
        loaded = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return result
    if not isinstance(loaded, dict):
        return result
    for key in result:
        if key in loaded:
            if isinstance(result[key], dict):
                if isinstance(loaded[key], dict):
                    result[key].update(loaded[key])
            else:
                result[key] = loaded[key]
    return result


def reserve_save(path):
    key = str(Path(path).resolve()).casefold()
    with _lock:
        revision = _revisions.get(key, 0) + 1
        _revisions[key] = revision
    return key, revision


def write_settings(path, payload, ticket):
    key = str(Path(path).resolve()).casefold()
    with _lock:
        if ticket[0] != key or _revisions.get(key) != ticket[1]:
            return False  # A newer save superseded this worker's snapshot.
        atomic_write(path, (json.dumps(payload, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    return True
