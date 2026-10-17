"""Explicit fixed-event aliases: display text stays separate from identity."""
import hashlib
import re

GROUPS = (
    ("길던", ("길던", "길드던전")),
    ("쟁탈전", ("쟁탈전", "길드쟁탈전")),
    ("방어전", ("방어전", "길드방어전")),
    ("점령전", ("점령전", "길드점령전")),
    ("알브닻", ("알브 닻", "알브하임 닻")),
    ("니다벨닻", ("니다 닻", "니다벨 닻", "니다벨리르 닻")),
)


def name_key(value):
    return re.sub(r"\s+", "", re.sub(r"\s*★\s*$", "", str(value or "")))


def canonical_fixed_name(value):
    key = name_key(value)
    return _NAME_ALIASES.get(key, key)


def voice_name_candidates(value):
    """Try the written name first, then whitespace variants and explicit aliases."""
    raw = str(value or "").strip()
    names = [raw, name_key(raw)]
    canonical = canonical_fixed_name(raw)
    for key, aliases in GROUPS:
        if key == canonical:
            for alias in aliases:
                names.extend((alias, name_key(alias)))
            break
    return list(dict.fromkeys(name for name in names if name))


def _hash(kind, name):
    return hashlib.sha256((kind + "|" + name_key(name)).encode("utf-8")).hexdigest()[:24]


def catalog_id(kind, name):
    if kind == "fixed":
        name = canonical_fixed_name(name)
        if name == "길던":
            kind = "boss"
    return _hash(kind, name)


def legacy_catalog_ids(identity):
    return list(_LEGACY_IDS.get(identity, ()))


def canonical_catalog_id(identity):
    return _ID_ALIASES.get(identity, identity)


def fixed_entry_id(item, resolve=None):
    """An edited entry keeps its stored identity, independent of display name."""
    value = item.get("web_identity")
    if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{24}", value):
        return canonical_catalog_id(value)
    name = item.get("boss_name")
    key = canonical_fixed_name(name)
    regional_anchor = key.endswith("닻") and key.startswith(("니다벨", "알브", "무스펠"))
    if resolve is not None and not regional_anchor:
        name = resolve(name) or name
    return catalog_id("fixed", name)


def migrate_spectrum_numbers(numbers):
    """Canonical entries win conflicts; retain every unrelated boss/color."""
    result = dict(numbers)
    for canonical, _ in GROUPS:
        identity = catalog_id("fixed", canonical)
        for old in legacy_catalog_ids(identity):
            if old in result:
                result.setdefault(identity, result.pop(old))
    return result


# Build once: snapshot validation can process thousands of events per refresh.
_NAME_ALIASES = {name_key(alias): canonical for canonical, aliases in GROUPS for alias in aliases}
_LEGACY_IDS = {}
for _canonical, _aliases in GROUPS:
    _identity = catalog_id("fixed", _canonical)
    _kinds = ("fixed", "boss") if _canonical == "길던" else ("fixed",)
    _LEGACY_IDS[_identity] = tuple(dict.fromkeys(
        _hash(kind, alias) for kind in _kinds for alias in _aliases if _hash(kind, alias) != _identity))
_ID_ALIASES = {old: current for current, old_ids in _LEGACY_IDS.items() for old in old_ids}
