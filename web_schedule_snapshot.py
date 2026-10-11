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
CONFIGURED_FIXED = {"월드보스", "닻", "공성전", "쟁탈전", "방어전", "점령전"}
MAJOR_FIXED_PREFIXES = ("공성전", "쟁탈전", "길던", "길드던전", "트리니티리그", "점령전", "방어전")


def _name_key(value):
    return re.sub(r"\s+", "", re.sub(r"\s*★\s*$", "", str(value or "")))


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
        if key in CONFIGURED_FIXED:
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


def collect_snapshot(app):
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
        text_color, bg_color = _web_colors(app, kind, name, text_color, bg_color, fixed_item)
        # The GUI resolver strips the invasion prefix and resolves configured aliases.
        # Use the current home-server star setting instead of a carried-over row flag.
        important = bool(app._schedule_boss_has_star(name)) if kind == "invasion" else bool(row.get("has_star"))
        event = dict(kind=kind, name=name[:200],
                           scheduled_at=_iso(when),
                           end_at=_iso(finish if isinstance(finish, datetime) and finish >= when else when),
                           important=important,
                           major=_major(kind, name, important, fixed_item),
                           text_color=text_color,
                           bg_color=bg_color,
                           note=str(row.get("break_display_text") or "")[:200])
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
            active.append(dict(name=name[:200], status="출현 중" if item.get("state") == "active" else "시간 미확정",
                               important=important, major=_major("fixed" if item.get("is_fixed") else "boss", name, important)))
    season_entry = getattr(app, "season_history_map", {}).get(str(app.current_season_no or ""), {})
    guild_name = str(season_entry.get("guild_name") or "").strip()[:200] if isinstance(season_entry, dict) else ""
    return dict(schema=SCHEMA, server_name=app._get_schedule_share_server_name()[:200], guild_name=guild_name,
                season=str(app.current_season_no or "")[:20], window_start=_iso(start), window_end=_iso(end),
                events=events, active=active, captured_at=timestamp())


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
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in KINDS:
            raise ValueError("invalid schedule event")
        start, end = _date(event.get("scheduled_at")), _date(event.get("end_at"))
        if end < start:
            raise ValueError("invalid schedule range")
        row = dict(kind=event["kind"], scheduled_at=event["scheduled_at"], end_at=event["end_at"],
                   text_color=_color(event.get("text_color")), bg_color=_color(event.get("bg_color")),
                   important=event.get("important") is True)
        row["major"] = event.get("major", _major(event["kind"], event.get("name"), row["important"])) is True
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
        result["active"].append(dict(name=item["name"], status=item["status"], important=item.get("important") is True,
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
