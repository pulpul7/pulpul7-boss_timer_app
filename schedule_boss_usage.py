"""Input-only boss switches. Definitions, existing events and OCR grid order stay intact."""
import re
from boss_view_labels import strip_last_hit_marker


def _key(value):
    return re.sub(r'\s+', '', re.sub(r'^침공\s*', '', strip_last_hit_marker(value))).casefold()


def input_enabled(definitions, item_or_name):
    if isinstance(item_or_name, dict):
        if item_or_name.get('state') == 'control' or item_or_name.get('control_type'):
            return True
        names = [item_or_name.get(key) for key in ('boss_name', 'display_name', 'raw_name')]
    else:
        names = [item_or_name]
    names = list(dict.fromkeys(_key(name) for name in names if name))
    for candidate in names:
        # A canonical name wins over another boss's colliding alias.
        for alias in (False, True):
            for name, item in definitions.items():
                if not isinstance(item, dict):
                    continue
                keys = {_key(item.get('alias'))} if alias else {_key(name), _key(item.get('boss_name'))}
                if candidate in keys:
                    value = item.get('enabled', True)
                    return value is not False and str(value).strip().casefold() not in {'0', 'false', 'off', 'no'}
    # Uncatalogued manual events and fixed events keep their existing behavior.
    return True
