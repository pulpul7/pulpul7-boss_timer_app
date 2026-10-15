"""Read-only schedule adapter and validation. Never imports or starts the GUI."""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import math
import re

from web_service_common import timestamp

SCHEMA = 1
KINDS = {"boss", "fixed", "invasion", "break", "maintenance", "server_open"}
HIDDEN_FIXED = {"방어전신청", "점령전신청", "방어전신청마감", "점령전신청마감",
                "공성전공격대상선택", "공성전입찰시작"}
# Registration deadlines are not selectable bosses. Keep their source schedules.
HIDDEN_CUSTOM_FIXED = HIDDEN_FIXED | {"길드쟁탈전신청마감", "쟁탈전신청마감"}
CONFIGURED_FIXED = {"월드보스", "닻", "공성전", "쟁탈전", "방어전", "점령전"}
MAJOR_FIXED_PREFIXES = ("공성전", "쟁탈전", "길던", "길드던전", "트리니티리그", "점령전", "방어전")


def _name_key(value):
    return re.sub(r"\s+", "", re.sub(r"\s*★\s*$", "", str(value or "")))


def _last_hit(app, item):
    name = app._get_schedule_boss_definition_name(item)
    return bool(getattr(app, "schedule_boss_definitions", {}).get(name, {}).get("last_hit", False))


def _major(kind, name, important, fixed_item=None):
    key = _name_key((fixed_item or {}).get("boss_name") or name)
    if kind == "fixed" and key == "지옥성채정예":
        return False
    return bool(important or (kind == "fixed" and
        (key == "지옥성채" or key.startswith(MAJOR_FIXED_PREFIXES))))


def _web_colors(app, kind, name, text_color, bg_color, fixed_item=None):
    """Web-only palette. Desktop definitions and display switches are untouched."""
    if kind == "invasion":
        # Read the invasion colors from Boss Settings, independently of cached row styles.
        configured_text, configured_bg = app._get_schedule_invasion_effective_colors()
        return _color(configured_text) or text_color, _color(configured_bg) or bg_color
    if kind == "fixed":
        key = _name_key((fixed_item or {}).get("boss_name") or name)
        if key in CONFIGURED_FIXED or (key.endswith("닻") and key.startswith(("니다벨", "알브", "무스펠"))):
            return text_color or "#87ceeb", bg_color
        if key in {"핏빛고블린", "빛빚고블린", "빛빛고블린", "황금고블린"}:
            return "#ffe066", ""
        if key == "지옥성채정예":
            return "#c9a2ff", ""
        return "#87ceeb", ""
    if kind != "boss":
        return text_color, ""
    boss_name = app._get_schedule_boss_definition_name(name)
    definition = getattr(app, "schedule_boss_definitions", {}).get(boss_name, {})
    if _name_key(boss_name or name) in {"최하층강글", "최하층스네르", "최하층굴베"}:
        return "#fffff0", "#8b692f"
    area = str(definition.get("area") or "")
    if definition.get("absolute") or area in {"니플하임", "바나하임", "절대자"}:
        return text_color, bg_color
    palette = {"요툰하임": "#ffffff", "니다벨리르": "#ffffff", "알브하임": "#ffffff",
               "무스펠하임": "#d7b58b", "아스가르드": "#91a8cd", "던전": "#fffff0"}
    return palette.get(area, text_color), ""


def _iso(value):
    # GUI times are local naive datetimes. An explicit offset avoids interpreting
    # the same clock as the visitor's timezone and preserves fractional seconds.
    return value.astimezone().isoformat(timespec="microseconds")


def _catalog_id(kind, name):
    return hashlib.sha256((kind + "|" + _name_key(name)).encode("utf-8")).hexdigest()[:24]


def _fixed_catalog_name(app, raw):
    key = _name_key(raw)
    # These entries may share the display alias "닻"; keep configured regions distinct.
    if key.endswith("닻") and key.startswith(("니다벨", "알브", "무스펠")):
        return key
    return app._get_canonical_boss_name(raw) or raw


def _special_category(name, kind, definition=""):
    key = _name_key(name)
    if key.startswith(("길던", "길드던전")):
        return "길던"
    if key.startswith("사용자이벤트") or (kind in {"boss", "invasion"} and not definition):
        return "사용자이벤트"
    return ""


def _collect_catalog(app, *, boss_orders=None, slot_grid=None):
    """Public boss settings only; no profile paths, credentials or file reads."""
    defaults = {"요툰하임": "2챕", "니다벨리르": "3챕", "알브하임": "4챕", "무스펠하임": "5챕",
                "아스가르드": "6챕", "니플하임": "7챕", "바나하임": "8챕", "던전": "던전"}
    result = []
    boss_orders, slot_grid = boss_orders or {}, slot_grid or {}
    for order, item in enumerate(app._get_schedule_boss_definition_items()):
        name = str(item.get("boss_name") or "").strip()
        if not name or app._is_schedule_boss_header_name(name):
            continue
        if _special_category(name, "boss", name):
            continue
        area = str(item.get("area") or "")
        label = str(getattr(app, "schedule_area_definitions", {}).get(area, {}).get("label") or defaults.get(area, area))
        chapter = re.search(r"(\d+)\s*(?:챕|장|chapter)", label, re.I)
        group = f"{int(chapter[1])}챕" if chapter else "던전" if area == "던전" else label or "기타"
        group_order = int(chapter[1]) if chapter else 10000 if group == "던전" else 9000
        names, grid = boss_orders.get(area, []), slot_grid.get(area, {})
        top = int(grid.get("top") or 0)
        bottom = int(grid.get("bottom") or 0)
        columns = max(top, bottom)
        slot = names.index(name) if name in names and columns else -1
        if slot >= top + bottom:
            slot = -1
        grid_row = (1 if slot < top else 2) if slot >= 0 else 0
        grid_column = (slot + 1 if slot < top else slot - top + 1) if slot >= 0 else 0
        result.append(dict(id=_catalog_id("boss", name), kind="boss", name=name[:200],
                           label=str(item.get("alias") or app._get_display_boss_name(name) or name)[:200],
                           group=group[:100], group_order=group_order, order=order,
                           grid_columns=columns, grid_row=grid_row, grid_column=grid_column,
                           absolute=bool(item.get("absolute")), important=bool(item.get("star")),
                           major=bool(item.get("star")), last_hit=bool(item.get("last_hit"))))
    fixed = {}
    for order, item in enumerate(getattr(app, "fixed_boss_entries", [])):
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        raw = str(item.get("boss_name") or "").strip()
        name = _fixed_catalog_name(app, raw)
        if not name or any(_name_key(value) in HIDDEN_CUSTOM_FIXED for value in (raw, name)):
            continue
        if _special_category(raw, "fixed"):
            continue
        identity = _catalog_id("fixed", name)
        important = bool(app._schedule_boss_has_star(name))
        major = _major("fixed", name, important, item)
        row = dict(id=identity, kind="fixed", name=name[:200],
                   label=(name[:-1] + " 닻" if name.endswith("닻") and name != "닻"
                          else str(app._get_display_boss_name(name) or name))[:200], group="고정보스",
                   group_order=11000, order=order, absolute=False, important=important, major=major)
        if identity in fixed:
            fixed[identity]["major"] |= major
        else:
            fixed[identity] = row
        if name.endswith("닻"):
            time_text = str(item.get("time_text") or "00:00:00")
            fixed[identity]["_anchor_time"] = min(fixed[identity].get("_anchor_time", time_text), time_text)
    anchors = [row for row in fixed.values() if "_anchor_time" in row]
    for order, row in zip(sorted(row["order"] for row in anchors), sorted(anchors, key=lambda row: row["_anchor_time"])):
        row["order"] = order
    for row in fixed.values():
        row.pop("_anchor_time", None)
    result.extend(fixed.values())
    for order, name in enumerate(("길던", "사용자이벤트", "침공")):
        result.append(dict(id=_catalog_id("boss", name), kind="boss", name=name, label=name,
                           group="특수 이벤트", group_order=10500, order=order, absolute=False,
                           important=False, major=name == "길던"))
    return sorted(result, key=lambda row: (row["group_order"], not row["major"] if row["kind"] == "fixed" else False,
                                           0 if row.get("grid_row") else 1, row.get("grid_row", 0),
                                           row.get("grid_column", 0), row["order"]))


def collect_snapshot(app, *, boss_orders=None, slot_grid=None):
    """Call only on the Tk thread. No refresh, save, OCR or network operations."""
    now = app._get_schedule_reference_datetime()
    start = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = (now + timedelta(days=14)).replace(hour=23, minute=59, second=59, microsecond=0)
    cutoff = app._get_schedule_visible_cutoff_datetime()
    if isinstance(cutoff, datetime):
        end = min(end, max(now, cutoff))
    rows, start, end = app._collect_schedule_share_rows(
        start, end, include_break_rows=bool(app.schedule_break_rows_enabled_var.get()),
        exclude_elapsed=False, sort_by_time=True,
        use_boss_colors=True, use_fixed_boss_colors=True,
    )
    # Read configured fixed-boss colors even when the desktop's
    # "fixed boss colors" display switch is off. Never change that switch.
    fixed_candidates = {}
    for item in getattr(app, "fixed_boss_entries", []):
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        name = app._get_schedule_boss_display_name({"boss_name": str(item.get("boss_name") or "")},
                                                  prefer_alias=True, include_star=True)
        name = re.sub(r"\s*★\s*$", "", str(name)).strip()
        time_text = app._normalize_fixed_boss_time_text(str(item.get("time_text") or "00:00:00"))
        fixed_candidates.setdefault((name, time_text), []).append(item)
    events = []
    for row in rows:
        when = row.get("scheduled_at")
        if not isinstance(when, datetime):  # Sharing also contains date headers.
            continue
        kind = str(row.get("kind") or "")
        if kind == "event":
            kind = "fixed" if row.get("is_fixed") else "invasion" if row.get("is_invasion") else "boss"
        if kind not in KINDS:
            continue
        finish = row.get("effective_end_at")
        name = str(row.get("boss_text") or "").strip()
        text_color, bg_color = _color(row.get("text_color")), _color(row.get("bg_color"))
        fixed_item = None
        if kind == "fixed":
            weekday = ("월", "화", "수", "목", "금", "토", "일")[when.weekday()]
            for item in fixed_candidates.get((str(row.get("boss_text") or "").strip(), when.strftime("%H:%M:%S")), []):
                if (weekday in item.get("days", []) and app._is_fixed_boss_entry_active_on_date(
                        item, when.date(), fallback_reference_datetime=now)):
                    text_color = _color(item.get("text_color")) or text_color
                    bg_color = _color(item.get("bg_color")) or bg_color
                    fixed_item = item
                    break
            if _name_key((fixed_item or {}).get("boss_name") or name) in HIDDEN_FIXED:
                continue
            if fixed_item:
                anchor = _fixed_catalog_name(app, str(fixed_item.get("boss_name") or ""))
                if anchor.endswith("닻") and anchor != "닻":
                    name = anchor[:-1] + " 닻"
        text_color, bg_color = _web_colors(app, kind, name, text_color, bg_color, fixed_item)
        # The GUI resolver strips the invasion prefix and resolves configured aliases.
        # Use the current home-server star setting instead of a carried-over row flag.
        important = bool(app._schedule_boss_has_star(name)) if kind == "invasion" else bool(row.get("has_star"))
        event = dict(kind=kind, name=name[:200],
                           scheduled_at=_iso(when),
                           end_at=_iso(finish if isinstance(finish, datetime) and finish >= when else when),
                           important=important, last_hit=_last_hit(app, name) if kind in {"boss", "invasion"} else False,
                           major=_major(kind, name, important, fixed_item),
                           text_color=text_color,
                           bg_color=bg_color,
                           note=str(row.get("break_display_text") or "")[:200])
        if kind in {"boss", "fixed", "invasion"}:
            raw = str((fixed_item or {}).get("boss_name") or name)
            definition = app._get_schedule_boss_definition_name(name)
            special = _special_category(raw, kind, definition)
            canonical = _fixed_catalog_name(app, raw) if kind == "fixed" else definition or name
            event["catalog_id"] = _catalog_id("boss", special) if special else _catalog_id("fixed" if kind == "fixed" else "boss", canonical)
        if kind == "break":
            event["row_scale"] = float(row.get("row_scale") or 1.0)
        events.append(event)
    for item in app.schedule_control_events:
        if not isinstance(item, dict) or item.get("historical_only"):
            continue
        when, control = item.get("scheduled_at"), item.get("control_type")
        if not isinstance(when, datetime) or not start <= when <= end:
            continue
        names = {"server_open": ("server_open", "서버 오픈"),
                 "temporary_maintenance": ("maintenance", "임시점검")}
        if control not in names:
            continue
        kind, name = names[control]
        events.append(dict(kind=kind, name=name, scheduled_at=_iso(when), end_at=_iso(when),
                           important=False, note=""))
    unique = {}
    for event in events:
        identity = "|".join((event["kind"], event["name"], event["scheduled_at"], event["end_at"]))
        event["id"] = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        unique[event["id"]] = event
    events = sorted(unique.values(), key=lambda item: (item["scheduled_at"], item["name"]))
    active = []
    for item in app.schedule_active_entries:
        if not isinstance(item, dict):
            continue
        name = app._get_schedule_boss_display_name(item, prefer_alias=True)
        if (_name_key(name) in HIDDEN_FIXED or _name_key(item.get("boss_name")) in HIDDEN_FIXED):
            continue
        if name:
            important = app._schedule_boss_has_star(item)
            invasion = bool(getattr(app, '_is_schedule_invasion_item', lambda row: row.get('is_invasion', False))(item))
            kind = "fixed" if item.get("is_fixed") else "invasion" if invasion else "boss"
            raw = str(item.get("boss_name") or name)
            definition = app._get_schedule_boss_definition_name(item)
            special = _special_category(raw, kind, definition)
            canonical = _fixed_catalog_name(app, raw) if kind == "fixed" else definition or name
            if kind == "fixed" and canonical.endswith("닻") and canonical != "닻":
                name = canonical[:-1] + " 닻"
            active.append(dict(kind=kind, catalog_id=_catalog_id("boss", special) if special else _catalog_id("boss" if kind == "invasion" else kind, canonical), name=name[:200], status="출현 중" if item.get("state") == "active" else "시간 미확정",
                               important=important, last_hit=_last_hit(app, item) if kind != "fixed" else False,
                               major=_major("fixed" if item.get("is_fixed") else "boss", name, important)))
    season_entry = getattr(app, "season_history_map", {}).get(str(app.current_season_no or ""), {})
    guild_name = str(season_entry.get("guild_name") or "").strip()[:200] if isinstance(season_entry, dict) else ""
    return dict(schema=SCHEMA, server_name=app._get_schedule_share_server_name()[:200], guild_name=guild_name,
                season=str(app.current_season_no or "")[:20], window_start=_iso(start), window_end=_iso(end),
                events=events, active=active, boss_catalog=_collect_catalog(app, boss_orders=boss_orders, slot_grid=slot_grid), captured_at=timestamp())


def _color(value):
    return value.lower() if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value) else ""


def _date(value):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("invalid schedule timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("schedule timestamp needs an offset")
    return parsed


def validate_snapshot(data):
    """Return a strict public allowlist; private envelope fields cannot leak."""
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise ValueError("invalid schedule schema")
    events, active = data.get("events"), data.get("active")
    if not isinstance(events, list) or len(events) > 20000 or not isinstance(active, list) or len(active) > 2000:
        raise ValueError("invalid schedule rows")
    result = {}
    for key, limit in (("server_name", 200), ("season", 20)):
        value = data.get(key)
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError("invalid schedule metadata")
        result[key] = value
    guild_name = data.get("guild_name", "")
    if not isinstance(guild_name, str) or len(guild_name) > 200:
        raise ValueError("invalid schedule guild")
    result["guild_name"] = guild_name
    for key in ("window_start", "window_end", "captured_at", "updated_at"):
        _date(data.get(key))
        result[key] = data[key]
    revision = data.get("revision")
    if type(revision) is not int or revision < 1:
        raise ValueError("invalid schedule revision")
    result.update(schema=SCHEMA, revision=revision, events=[], active=[])
    if "boss_catalog" in data:
        catalog = data["boss_catalog"]
        if not isinstance(catalog, list) or len(catalog) > 4000:
            raise ValueError("invalid boss catalog")
        result["boss_catalog"] = []
        seen = set()
        for entry in catalog:
            if not isinstance(entry, dict) or entry.get("kind") not in {"boss", "fixed"}:
                raise ValueError("invalid boss catalog entry")
            identity = entry.get("id")
            if not isinstance(identity, str) or not re.fullmatch(r"[0-9a-f]{24}", identity) or identity in seen:
                raise ValueError("invalid boss catalog identity")
            seen.add(identity)
            clean = dict(id=identity, kind=entry["kind"])
            for key, limit in (("name", 200), ("label", 200), ("group", 100)):
                if not isinstance(entry.get(key), str) or not 0 < len(entry[key]) <= limit:
                    raise ValueError("invalid boss catalog text")
                clean[key] = entry[key]
            for key in ("group_order", "order"):
                if type(entry.get(key)) is not int or not 0 <= entry[key] <= 100000:
                    raise ValueError("invalid boss catalog order")
                clean[key] = entry[key]
            for key in ("absolute", "important", "major", "last_hit"):
                clean[key] = entry.get(key) is True
            for key in ("grid_columns", "grid_row", "grid_column"):
                value = entry.get(key, 0)
                if type(value) is not int or not 0 <= value <= 32:
                    raise ValueError("invalid boss catalog grid")
                clean[key] = value
            if ((clean["grid_row"] > 0 or clean["grid_column"] > 0)
                    and not (clean["grid_columns"] > 0 and clean["grid_row"] > 0
                             and 0 < clean["grid_column"] <= clean["grid_columns"])):
                raise ValueError("invalid boss catalog grid slot")
            result["boss_catalog"].append(clean)
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in KINDS:
            raise ValueError("invalid schedule event")
        start, end = _date(event.get("scheduled_at")), _date(event.get("end_at"))
        if end < start:
            raise ValueError("invalid schedule range")
        row = dict(kind=event["kind"], scheduled_at=event["scheduled_at"], end_at=event["end_at"],
                   text_color=_color(event.get("text_color")), bg_color=_color(event.get("bg_color")),
                   important=event.get("important") is True, last_hit=event.get("last_hit") is True)
        row["major"] = event.get("major", _major(event["kind"], event.get("name"), row["important"])) is True
        identity = event.get("catalog_id", "")
        if not isinstance(identity, str) or (identity and not re.fullmatch(r"[0-9a-f]{24}", identity)):
            raise ValueError("invalid event catalog identity")
        row["catalog_id"] = identity
        if event["kind"] == "break":
            scale = event.get("row_scale", 1.0)
            if type(scale) not in {int, float} or not math.isfinite(scale) or not 0.5 <= scale <= 12.0:
                raise ValueError("invalid rest row scale")
            row["row_scale"] = float(scale)
        for key, limit in (("id", 24), ("name", 200), ("note", 200)):
            value = event.get(key)
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError("invalid schedule text")
            row[key] = value
        result["events"].append(row)
    result["events"].sort(key=lambda row: (_date(row["scheduled_at"]), row["name"]))
    for item in active:
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or len(item["name"]) > 200 or item.get("status") not in {"출현 중", "시간 미확정"}):
            raise ValueError("invalid active row")
        identity = item.get("catalog_id", "")
        if not isinstance(identity, str) or (identity and not re.fullmatch(r"[0-9a-f]{24}", identity)):
            raise ValueError("invalid active catalog identity")
        result["active"].append(dict(kind=item.get("kind") if item.get("kind") in {"fixed", "invasion"} else "boss", catalog_id=identity,
                                     name=item["name"], status=item["status"], important=item.get("important") is True,
                                     last_hit=item.get("last_hit") is True,
                                     major=item.get("major", item.get("important", False)) is True))
    return result


def schedule_summary(snapshot, now):
    bosses = [row for row in snapshot["events"] if row["kind"] in {"boss", "fixed", "invasion"}]
    past = [row for row in bosses if _date(row["scheduled_at"]) < now]
    future = [row for row in bosses if _date(row["scheduled_at"]) >= now]
    if not future:
        future = [row for row in snapshot["events"]
                  if row["kind"] == "maintenance" and _date(row["scheduled_at"]) >= now][:1]
    def describe(row):
        return dict(row, remaining_seconds=max(0, (_date(row["scheduled_at"]) - now).total_seconds())) if row else None
    return dict(previous=describe(past[-1] if past else None),
                next=describe(future[0] if future else None),
                following=describe(future[1] if len(future) > 1 else None))
