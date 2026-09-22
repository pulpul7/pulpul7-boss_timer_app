"""Updateable per-client, per-server notice controls. No TTS connection yet.

Future collectors call register(); playback checks delivery_token immediately
before playing/resuming and calls complete_delivery only after successful audio.
Every notice must have an explicit validity interval to be playable.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import uuid

KST = timezone(timedelta(hours=9))
HISTORY_DAYS = 30
ONE_SHOT_SUFFIXES = ("/time-v1/unknown-discovery", "/time-v1/general-discovery")
CATEGORIES = {
    "maintenance": "정기·임시점검 / 연장",
    "transfer": "서버이전 / 이전권",
    "class_change": "신규 직업 / 클래스 변경권",
    "update": "업데이트 / 확인된 문제",
    "event": "기간제 이벤트 / 아이템",
    "participation": "보스 / 길던 참여 독려",
    "general": "기타 공지",
}
DEFAULT_RULES = [
    {"days": "매일", "start": "06:50", "end": "09:30", "minutes": 15},
    {"days": "수요일", "start": "09:30", "end": "11:00", "minutes": 3},
    {"days": "매일", "start": "09:30", "end": "24:00", "minutes": 10},
]
WEEKDAYS = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")


class NoticeError(ValueError):
    pass


def local_now():
    return datetime.now(KST)


def parse_time(value):
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        result = value
    else:
        if not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}", str(value).strip()):
            raise NoticeError("날짜와 시각을 함께 입력해 주세요: YYYY-MM-DD HH:MM")
        try:
            result = datetime.fromisoformat(str(value).strip())
        except ValueError as exc:
            raise NoticeError("날짜는 YYYY-MM-DD HH:MM 형식으로 입력해 주세요.") from exc
    return result.replace(tzinfo=KST) if result.tzinfo is None else result.astimezone(KST)


def encode_time(value):
    parsed = parse_time(value)
    return parsed.isoformat(timespec="seconds") if parsed else None


def display_time(value):
    parsed = parse_time(value)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else "미확정"


def validate_interval(start, end):
    first, last = parse_time(start), parse_time(end)
    if first and last and first >= last:
        raise NoticeError("유효 종료 시각은 시작 시각보다 늦어야 합니다.")
    return encode_time(first), encode_time(last)


def _clock_minutes(value, *, end=False):
    if end and value == "24:00":
        return 1440
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise NoticeError("조회 시각은 HH:MM 형식입니다. 종료에만 24:00을 사용할 수 있습니다.")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def validate_settings(settings):
    if not isinstance(settings.get("collection_enabled"), bool) or not isinstance(settings.get("output_enabled"), bool):
        raise NoticeError("수집 / 송출 설정이 잘못되었습니다.")
    categories = settings.get("categories")
    if not isinstance(categories, dict) or set(categories) != set(CATEGORIES) or any(type(v) is not bool for v in categories.values()):
        raise NoticeError("수집 종류 설정이 잘못되었습니다.")
    rules = settings.get("rules")
    if not isinstance(rules, list) or len(rules) > 40:
        raise NoticeError("조회 규칙은 최대 40개까지 등록할 수 있습니다.")
    for rule in rules:
        if rule.get("days") not in ("매일", *WEEKDAYS):
            raise NoticeError("조회 요일이 잘못되었습니다.")
        if _clock_minutes(rule.get("start")) >= _clock_minutes(rule.get("end"), end=True):
            raise NoticeError("조회 종료는 시작보다 늦어야 합니다. 자정을 넘는 규칙은 두 줄로 나눠 주세요.")
        if type(rule.get("minutes")) is not int or not 1 <= rule["minutes"] <= 1440:
            raise NoticeError("조회 주기는 1~1440분 사이로 입력해 주세요.")


def collection_interval(settings, now=None):
    """First matching rule wins. This does not restrict announcement times."""
    if not settings["collection_enabled"]:
        return None
    now = parse_time(now or local_now())
    minute = now.hour * 60 + now.minute
    for rule in settings["rules"]:
        if rule["days"] in ("매일", WEEKDAYS[now.weekday()]):
            if _clock_minutes(rule["start"]) <= minute < _clock_minutes(rule["end"], end=True):
                return rule["minutes"]
    return None


def event_status(event, now=None):
    now = parse_time(now or local_now())
    if event.get("retired_at"):
        return event["retired_reason"]
    first, last = parse_time(event.get("valid_from")), parse_time(event.get("valid_until"))
    if last and now >= last:
        return "유효기간 만료"
    if not first or not last:
        return "기간 미확정 · 실행 보류"
    if event.get("analysis_hold"):
        return "원문 변경 · 재확인 필요 · 실행 보류"
    if event.get("tts_review_required"):
        return "기간/자동 문장 변경 · 편집 문장 확인 필요"
    if not event["enabled"]:
        return "사용자 해제"
    if event.get("delivered_revision") == event["revision"]:
        return "안내 완료"
    return "예정" if now < first else "유효 · 송출 조건 대기"


class NoticeStore:
    def __init__(self, root, server_id, *, clock=local_now):
        self.root = Path(root)
        self.server_id = str(server_id or "").strip()
        if not self.server_id:
            raise NoticeError("서버를 선택한 뒤 알리미 관리를 열어 주세요.")
        self.key = hashlib.sha256(self.server_id.encode("utf-8")).hexdigest()
        self.path = self.root / f"{self.key}.json"
        self.clock = clock
        self.thread_lock = threading.Lock()

    def _load(self):
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            state = {"schema": 1, "server_id": self.server_id, "generation": 0,
                    "settings": {"collection_enabled": True, "output_enabled": False,
                                 "categories": dict.fromkeys(CATEGORIES, True), "rules": deepcopy(DEFAULT_RULES)},
                    "events": {}, "suppressed": {}}
            from .notice_defaults import bundled_preferences, PREFERENCE_KEYS
            defaults = bundled_preferences()
            if defaults:
                state.update({key: deepcopy(defaults[key]) for key in PREFERENCE_KEYS if key in defaults})
            return state
        if not isinstance(state, dict) or state.get("schema") != 1 or state.get("server_id") != self.server_id:
            raise NoticeError("알림 기록의 서버 정보가 다릅니다. 원본을 보존했습니다.")
        validate_settings(state["settings"])
        if not isinstance(state.get("events"), dict) or not isinstance(state.get("suppressed"), dict):
            raise NoticeError("알림 기록 형식이 잘못되었습니다. 원본을 보존했습니다.")
        return state

    def _save(self, state):
        fd, name = tempfile.mkstemp(prefix="notice-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(state, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @contextmanager
    def _transaction(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.thread_lock, self.path.with_suffix(".lock").open("a+b") as lock:
            if sys.platform == "win32":
                import msvcrt
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise NoticeError("다른 작업에서 알림 설정을 저장 중입니다. 잠시 후 다시 시도해 주세요.") from exc
                release = lambda: (lock.seek(0), msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1))
            else:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
                release = lambda: fcntl.flock(lock, fcntl.LOCK_UN)
            try:
                state = self._load()
                before = deepcopy(state)
                from .notice_templates import migrate_date_templates, migrate_participation_templates
                migrate_date_templates(state)
                migrate_participation_templates(state)
                self._prune(state)
                self._refresh_relative_speech(state)
                yield state
                if state != before:
                    self._save(state)
                    from .notice_changes import publish
                    publish(self.root, self.server_id)
            finally:
                release()

    def _refresh_relative_speech(self, state):
        from .notice_templates import render_template, TEMPLATES
        now = parse_time(self.clock())
        for event in state['events'].values():
            key = event.get('tts_template')
            if key not in TEMPLATES or event.get('retired_at'):
                continue
            try:
                generated = render_template(state, key, event.get('tts_values', {}), now=now)
            except (ValueError, TypeError, KeyError) as exc:
                # An incomplete legacy event must not prevent opening management
                # or other valid notices from refreshing. Never guess its dates.
                reason = 'TTS 날짜/문장 정보 확인 필요: ' + str(exc)
                if event.get('analysis_hold') != reason:
                    event['analysis_hold'] = reason
                    event['revision'] += 1
                continue
            if generated == event.get('generated_tts_text', event['tts_text']):
                continue
            completed = event.get('delivered_revision') == event['revision']
            event['generated_tts_text'] = generated
            if not event.get('manual_tts'):
                event['tts_text'] = generated
                event['revision'] += 1
                if completed:
                    event['delivered_revision'] = event['revision']

    def _retire(self, state, event, reason, when):
        event['enabled_before_retirement'] = event.get('enabled', True)
        event.update(retired_at=encode_time(when), retired_reason=reason, enabled=False)
        event["revision"] += 1
        # Only an opaque source identity is retained beyond history retention.
        if event.get("source_key"):
            state["suppressed"][event["source_key"]] = True

    def _prune(self, state):
        now = parse_time(self.clock())
        if "delivery_ledger" in state:
            state["delivery_ledger"] = [row for row in state["delivery_ledger"]
                                        if now < parse_time(row["at"]) + timedelta(days=HISTORY_DAYS)]
        for key, event in list(state["events"].items()):
            end = parse_time(event.get("valid_until"))
            if (not event.get("retired_at") and event.get("season_number") is not None
                    and str(event["season_number"]) in state.get("season_announced", {})):
                self._retire(state, event, "동일 시즌 응원 안내 완료", now)
            if not event.get("retired_at") and end and now >= end:
                self._retire(state, event, "유효기간 만료", end)
            retired = parse_time(event.get("retired_at"))
            if retired and now >= retired + timedelta(days=HISTORY_DAYS):
                del state["events"][key]
        for key, article in list(state.get("articles", {}).items()):
            removed = parse_time(article.get("removed_at"))
            if removed and now >= removed + timedelta(days=HISTORY_DAYS):
                del state["articles"][key]

    def snapshot(self):
        with self._transaction() as state:
            return deepcopy(state)

    def configure(self, settings):
        validate_settings(settings)
        with self._transaction() as state:
            state["settings"] = deepcopy(settings)
            # Queued items must never resume across a settings off/on cycle.
            state["generation"] += 1

    def restore_preferences(self, payload):
        """Restore settings without deleting event history or replaying deliveries."""
        from .notice_defaults import validate_preferences, export_preferences, PREFERENCE_KEYS
        defaults = validate_preferences(payload)
        with self._transaction() as state:
            previous = export_preferences(state)
            backup_dir = self.root / "settings_rollback_backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            backup = backup_dir / f"{self.key}_{uuid.uuid4().hex}.json"
            # Save old preferences before changing the live state.
            with backup.open("x", encoding="utf-8") as file:
                json.dump(previous, file, ensure_ascii=False, indent=2)
            for key in PREFERENCE_KEYS:
                if key in defaults:
                    state[key] = deepcopy(defaults[key])
                else:
                    state.pop(key, None)
            state["tts_templates_revision"] = state.get("tts_templates_revision", 0) + 1
            state["generation"] += 1
            self._refresh_relative_speech(state)
        return str(backup)

    def register(self, event, *, collected=False, _state=None):
        from .notice_opportunities import POLICIES
        from .notice_seasons import season_template_key
        from .notice_templates import render_template

        key = str(event.get("id") or "").strip()
        title = str(event.get("title") or "").strip()
        category = event.get("category", "general")
        if not key or not title or category not in CATEGORIES:
            raise NoticeError("알림 ID, 제목, 종류를 확인해 주세요.")
        season_number = event.get("season_number")
        if season_number is not None and (category != "transfer" or type(season_number) is not int or not 1 <= season_number <= 9999):
            raise NoticeError("시즌 응원 알림의 서버이전 차수를 확인해 주세요.")
        policy = event.get("policy", "participation" if category == "participation" else "once")
        trigger = event.get("trigger", "major_boss_after" if policy != "once" else "immediate")
        if (policy not in POLICIES or trigger not in {"immediate", "major_boss_after"}
                or (policy not in {"once", 'participation_slot'} and trigger != "major_boss_after")
                or (policy == "transfer_repeat" and category != "transfer")
                or (policy in {"participation", 'participation_slot'} and category != "participation")
                or (policy == "deadline_repeat" and category not in {"class_change", "event"})):
            raise NoticeError("알림 종류와 반복 안내 조건을 확인해 주세요.")
        first, last = validate_interval(event.get("valid_from"), event.get("valid_until"))
        period_start, period_end = validate_interval(event.get("event_from"), event.get("event_until"))
        if policy in {"participation", 'participation_slot'} and not period_start:
            raise NoticeError("참여 독려는 행사 시작 시각을 입력해야 합니다.")
        source_key = str(event.get("source_key") or "")
        # Callers must supply an alert-specific identity (article + rule/window).
        if collected and not source_key:
            raise NoticeError("수집 알림은 공지와 안내 규칙을 식별하는 source_key가 필요합니다.")
        with (self._transaction() if _state is None else nullcontext(_state)) as state:
            if collected and (not state["settings"]["collection_enabled"]
                              or not state["settings"]["categories"][category]
                              or source_key in state["suppressed"]):
                return False
            old = state["events"].get(key)
            if old and old.get("retired_at"):
                return False
            if season_number is not None:
                event = dict(event, tts_template=season_template_key(state, season_number),
                             tts_values={"차수": str(season_number)})
            if event.get("tts_template"):
                event = dict(event, tts_text=render_template(state, event["tts_template"], event.get("tts_values", {}), now=parse_time(self.clock())))
            item = {"id": key, "title": title, "category": category, "source_key": source_key,
                    "source_url": str(event.get("source_url") or ""), "body": str(event.get("body") or ""),
                    "tts_text": str(event.get("tts_text") or ""), "valid_from": first, "valid_until": last,
                    "event_from": period_start, "event_until": period_end, "enabled": True,
                    "revision": 1, "created_at": encode_time(self.clock()), "updated_at": encode_time(self.clock()),
                    "delivered_revision": None, "last_delivery": None, "manual_period": False,
                    "analysis_evidence": str(event.get("analysis_evidence") or ""),
                    "retire_after_delivery": bool(event.get("retire_after_delivery", False)),
                    "generated_tts_text": str(event.get("tts_text") or ""),
                    "tts_template": event.get("tts_template", ""), "tts_values": deepcopy(event.get("tts_values", {})),
                    "manual_tts": False, "tts_review_required": False,
                    "season_number": season_number,
                    "trigger": trigger, "policy": policy, "analysis_hold": ""}
            if old:
                if old.get('source_recovery'):
                    item['source_recovery'] = deepcopy(old['source_recovery'])
                for field in ("enabled", "created_at", "last_delivery", "delivered_revision", "manual_period"):
                    item[field] = old[field]
                if collected and old["manual_period"]:
                    item["valid_from"], item["valid_until"] = old["valid_from"], old["valid_until"]
                if collected and old.get("manual_tts"):
                    item["manual_tts"] = True
                    item["tts_text"] = old["tts_text"]
                    item["tts_review_required"] = bool(old.get("tts_review_required") or
                        item["generated_tts_text"] != old.get("generated_tts_text", old["tts_text"]) or
                        item["event_from"] != old["event_from"] or item["event_until"] != old["event_until"])
                meaningful = ("title", "category", "source_key", "source_url", "body", "tts_text", "valid_from", "valid_until", "event_from", "event_until", "tts_values")
                if (all(item[field] == old[field] for field in meaningful)
                        and item["trigger"] == old.get("trigger", "immediate")
                        and item["retire_after_delivery"] == old.get("retire_after_delivery", False)
                        and item["policy"] == old.get("policy", "once")
                        and item["tts_review_required"] == old.get("tts_review_required", False)
                        and item["season_number"] == old.get("season_number")
                        and not old.get("analysis_hold")):
                    old["analysis_evidence"] = item["analysis_evidence"]
                    old["generated_tts_text"] = item["generated_tts_text"]
                    old["tts_template"], old["tts_values"] = item["tts_template"], item["tts_values"]
                    return False
                item["revision"] = old["revision"] + 1
            state["events"][key] = item
            self._prune(state)
            return True

    def _event(self, state, key):
        event = state["events"].get(key)
        if not event or event.get("retired_at"):
            raise NoticeError("이미 종료되거나 폐기된 알림입니다. 목록을 새로 확인해 주세요.")
        return event

    def save_tts_templates(self, templates, *, expected_revision, enabled=None):
        """Defaults affect pending automatic text, never replay completed notices."""
        from .notice_templates import TEMPLATES, validate_template, render_template
        if not isinstance(templates, dict) or set(templates) != set(TEMPLATES):
            raise NoticeError("안내 유형 목록이 변경되었습니다. 기본 설정 창을 다시 열어 주세요.")
        if enabled is not None and (not isinstance(enabled, dict) or set(enabled) != set(TEMPLATES)
                                    or any(type(value) is not bool for value in enabled.values())):
            raise NoticeError("유형별 실행 체크 설정을 확인해 주세요.")
        try:
            overrides = {key: value for key, text in templates.items()
                         if (value := validate_template(key, text)) != TEMPLATES[key]["text"]}
        except ValueError as exc:
            raise NoticeError(str(exc)) from exc
        with self._transaction() as state:
            if expected_revision != state.get("tts_templates_revision", 0):
                raise NoticeError("다른 창에서 기본 문구를 변경했습니다. 창을 다시 열어 주세요.")
            old_flags = {key: state.get('tts_template_enabled', {}).get(key, True) for key in TEMPLATES}
            if ({key: value for key, value in state.get("tts_templates", {}).items() if key in TEMPLATES} == overrides
                    and (enabled is None or enabled == old_flags)):
                return expected_revision
            if enabled is not None:
                state.setdefault('tts_template_enabled', {}).update(enabled)
            # Preserve unknown future-version keys when opening an older module.
            extra = {key: value for key, value in state.get("tts_templates", {}).items() if key not in TEMPLATES}
            state["tts_templates"] = dict(extra, **overrides)
            # Backfill only identities supported by already stored analysis.
            # Never infer a template from an arbitrary user's speech string.
            from .notice_analysis import notice_events
            from .notice_seasons import season_template_key
            for article in state.get("articles", {}).values():
                if not isinstance(article.get("analysis"), dict):
                    continue
                try:
                    candidates = notice_events(article, article["analysis"])
                except (ValueError, KeyError, TypeError):
                    continue  # Old/incomplete analysis: next successful collection will link it.
                for candidate in candidates:
                    item = state["events"].get(candidate["id"])
                    if item and not item.get("retired_at") and not item.get("tts_template"):
                        item["tts_template"] = candidate["tts_template"]
                        item["tts_values"] = candidate["tts_values"]
                        if item.get("season_number") is not None:
                            item["tts_template"] = season_template_key(state, item["season_number"])
                            item["tts_values"] = {"차수": str(item["season_number"])}
            for item in state["events"].values():
                key = item.get("tts_template")
                if not key or key not in TEMPLATES or item.get("retired_at"):
                    continue
                try:
                    generated = render_template(state, key, item.get("tts_values", {}), now=parse_time(self.clock()))
                except (ValueError, TypeError, KeyError):
                    continue  # Incomplete legacy data is held by _refresh_relative_speech.
                if generated == item.get("generated_tts_text", item["tts_text"]):
                    continue
                completed = item.get("delivered_revision") == item["revision"]
                item["generated_tts_text"] = generated
                if not item.get("manual_tts"):
                    item["tts_text"] = generated
                item["updated_at"] = encode_time(self.clock())
                item["revision"] += 1
                if completed:
                    item["delivered_revision"] = item["revision"]
            state["generation"] += 1
            state["tts_templates_revision"] = expected_revision + 1
            return state["tts_templates_revision"]

    def set_enabled(self, key, enabled):
        with self._transaction() as state:
            item = self._event(state, key)
            item["enabled"] = bool(enabled)
            # Generation invalidates queued audio without replaying an already
            # delivered event when a checkbox is merely switched off then on.
            state["generation"] += 1

    def edit_period(self, key, first, last):
        first, last = validate_interval(first, last)
        with self._transaction() as state:
            item = self._event(state, key)
            item.update(valid_from=first, valid_until=last, manual_period=True,
                        updated_at=encode_time(self.clock()))
            item["revision"] += 1
            self._prune(state)

    def discard(self, key):
        with self._transaction() as state:
            self._retire(state, self._event(state, key), "사용자 폐기", self.clock())

    def edit_tts(self, key, text, *, use_generated=False, expected_revision=None):
        """Editing never replays an already completed notice or resets quotas."""
        if not isinstance(text, str) or len(text) > 10000:
            raise NoticeError("TTS 문장은 10,000자 이내로 입력해 주세요.")
        with self._transaction() as state:
            item = self._event(state, key)
            if expected_revision is not None and expected_revision != item["revision"]:
                raise NoticeError("편집 중 공지가 변경되었습니다. 창을 다시 열어 최신 내용을 확인해 주세요.")
            completed = item.get("delivered_revision") == item["revision"]
            generated = item.get("generated_tts_text", item["tts_text"])
            item.update(tts_text=generated if use_generated else text.strip(), generated_tts_text=generated,
                        manual_tts=not use_generated, tts_review_required=False, updated_at=encode_time(self.clock()))
            item["revision"] += 1
            if completed:
                item["delivered_revision"] = item["revision"]

    def reconcile_sources(self, current_keys, *, successful=False):
        """Only a complete successful pinned-list read can retire removed notices.

        current_keys must include all previously known rules belonging to pinned
        articles, not only today's newly parsed candidates. Failed/partial reads
        must never pass successful=True.
        """
        if not successful:
            return
        current_keys = set(current_keys)
        with self._transaction() as state:
            for event in state["events"].values():
                if (event["source_key"] and not event["source_key"].startswith('schedule/')
                        and event["source_key"] not in current_keys and not event.get("retired_at")):
                    self._retire(state, event, "공지 해제", self.clock())
            state["suppressed"] = {key: True for key in state["suppressed"]
                                   if key in current_keys or key.startswith('schedule/') or key.endswith(ONE_SHOT_SUFFIXES)}

    def delete_history(self, keys=None):
        with self._transaction() as state:
            selected = set(keys) if keys is not None else set(state["events"])
            removed = 0
            for key in list(state["events"]):
                if key in selected and state["events"][key].get("retired_at"):
                    del state["events"][key]
                    removed += 1
            return removed

    def _can_deliver(self, state, event, context):
        from .notice_opportunities import requires_opportunity, opportunity_allowed
        from .notice_templates import event_template_enabled

        if (not state["settings"]["output_enabled"] or not event
                or not event_template_enabled(state, event)
                or not event.get("tts_text", "").strip()
                or event_status(event, self.clock()) != "유효 · 송출 조건 대기"):
            return False
        if (event['id'].startswith('manual-') and event.get('category') == 'participation'
                and event.get('tts_template', '') in {'', 'participation.general'}):
            return False  # Manual targets feed the daily summary, not extra standalone reminders.
        if event.get('policy') == 'participation_slot':
            from .notice_participation import slot_allowed
            values = event.get('tts_values', {})
            if values.get('_manual_source'):
                source = state['events'].get(values['_manual_source'])
                if (not source or source.get('revision') != values.get('_manual_revision')
                        or not source.get('enabled') or source.get('retired_at') or source.get('analysis_hold')
                        or source.get('tts_review_required') or not event_template_enabled(state, source)):
                    return False
            return slot_allowed(event, context, state.get('delivery_ledger', []), parse_time(self.clock()))
        if event.get("season_number") is not None:
            end = parse_time(event.get("event_until"))
            if (str(event["season_number"]) in state.get("season_announced", {}) or not end
                    or not end - timedelta(minutes=15) <= parse_time(self.clock()) < end):
                return False
        return (not requires_opportunity(event) or
                opportunity_allowed(event, context, state.get("delivery_ledger", []), parse_time(self.clock())))

    def delivery_token(self, key, *, opportunity=None):
        """None means do not enqueue/play/resume. No connection arbitration."""
        from .notice_opportunities import normalize_opportunity, requires_opportunity, encode_opportunity

        with self._transaction() as state:
            event = state["events"].get(key)
            context = normalize_opportunity(opportunity, self.server_id, parse_time(self.clock()))
            if not self._can_deliver(state, event, context):
                return None
            token = (self.server_id, key, event["revision"], state["generation"])
            return token + (encode_opportunity(context),) if requires_opportunity(event) else token

    def opportunity_candidate(self, opportunity):
        """Choose at most one notice; the future audio host must recheck token.

        Selection is not playback, a quota debit or a reservation.
        """
        from .notice_opportunities import normalize_opportunity, requires_opportunity, encode_opportunity

        with self._transaction() as state:
            context = normalize_opportunity(opportunity, self.server_id, parse_time(self.clock()))
            candidates = [event for event in state["events"].values()
                          if requires_opportunity(event) and self._can_deliver(state, event, context)]
            if not candidates:
                return None
            event = min(candidates, key=lambda item: (0 if item["category"] == "transfer" else
                        1 if item.get("policy") == "deadline_repeat" else 2,
                        item["valid_until"], item["id"]))
            token = (self.server_id, event["id"], event["revision"], state["generation"], encode_opportunity(context))
            return {"id": event["id"], "tts_text": event["tts_text"], "token": token}

    def automatic_candidate(self, opportunity=None, *, exclude_ids=()):
        """Lowest-priority notice queue selection; never synthesizes or plays."""
        from .notice_opportunities import normalize_opportunity, requires_opportunity, encode_opportunity

        with self._transaction() as state:
            context = normalize_opportunity(opportunity, self.server_id, parse_time(self.clock()))
            candidates = [event for event in state["events"].values()
                          if event["id"] not in exclude_ids and self._can_deliver(state, event, context)]
            if not candidates:
                return None
            priorities = {"transfer": 0, "maintenance": 1, "class_change": 2, "event": 2, "participation": 3}
            event = min(candidates, key=lambda item: (priorities.get(item["category"], 4), item["valid_until"], item["id"]))
            token = (self.server_id, event["id"], event["revision"], state["generation"])
            if requires_opportunity(event):
                token += (encode_opportunity(context),)
            return {"id": event["id"], "tts_text": event["tts_text"], "token": token}

    def preparation_candidates(self):
        """Upcoming enabled text, independent of playback time/opportunities."""
        from .notice_templates import event_template_enabled
        with self._transaction() as state:
            if not state['settings']['output_enabled']:
                return []
            now = parse_time(self.clock())
            result = []
            for event in state['events'].values():
                start, end = parse_time(event.get('valid_from')), parse_time(event.get('valid_until'))
                if (not start or not end or end <= now or start > now + timedelta(days=1)
                        or not state['settings']['categories'].get(event.get('category'), False)
                        or not event.get('enabled') or event.get('retired_at') or event.get('analysis_hold')
                        or event.get('tts_review_required') or not event.get('tts_text', '').strip()
                        or event.get('delivered_revision') == event['revision'] or not event_template_enabled(state, event)):
                    continue
                if event['id'].startswith('manual-') and event.get('category') == 'participation':
                    continue  # Source target, not an independent utterance.
                slot = event.get('tts_values', {}).get('_slot')
                if slot and any(row.get('slot') == slot and parse_time(row['at']).date() == now.date()
                                for row in state.get('delivery_ledger', [])):
                    continue
                result.append(dict(id=event['id'], tts_text=event['tts_text'],
                                   stamp=(event['revision'], state['generation']), valid_from=event['valid_from']))
            return sorted(result, key=lambda row: (row['valid_from'], row['id']))

    def complete_delivery(self, token):
        from .notice_opportunities import normalize_opportunity, requires_opportunity, group_of

        if not isinstance(token, (tuple, list)) or len(token) not in {4, 5}:
            return False
        server_id, key, revision, generation = token[:4]
        try:
            raw_context = json.loads(token[4]) if len(token) == 5 else None
        except (ValueError, TypeError):
            return False
        with self._transaction() as state:
            context = normalize_opportunity(raw_context, self.server_id, parse_time(self.clock()))
            event = state["events"].get(key)
            if (server_id != self.server_id or generation != state["generation"] or not event
                    or event["revision"] != revision or not self._can_deliver(state, event, context)):
                return False
            event.update(delivered_revision=revision, last_delivery=encode_time(self.clock()))
            if event.get("season_number") is not None:
                state.setdefault("season_announced", {})[str(event["season_number"])] = True
                state["generation"] += 1
            if requires_opportunity(event) or event.get('policy') == 'participation_slot':
                state.setdefault("delivery_ledger", []).append({"at": encode_time(self.clock()), "event_id": key,
                                                               "opportunity_id": context['id'] if context else 'timer/' + str(event.get('tts_values', {}).get('_slot')) + '/' + parse_time(self.clock()).date().isoformat(),
                                                               "group": group_of(event), 'slot': event.get('tts_values', {}).get('_slot')})
                state["generation"] += 1  # Revalidate every pending notice against the shared quota.
            if event.get("policy", "once") != "once":
                event["delivered_revision"] = None
                event["revision"] += 1
            if event.get("retire_after_delivery"):
                self._retire(state, event, "1회 안내 완료 · 자동 폐기", self.clock())
            return True

    def claim_collection(self, *, manual=False):
        """Persistent per-server due check. Manual bypasses hours, not disable."""
        from .notice_polling import polling_plan

        with self._transaction() as state:
            now = parse_time(self.clock())
            settings = state["settings"]
            if not settings["collection_enabled"]:
                return None
            plan = polling_plan(state, now)
            interval = plan["minutes"]
            previous = state.get("collection", {})
            last = parse_time(previous.get("last_attempt"))
            boundary = parse_time(plan["boundary"])
            first_after_extension = (plan["mode"] == "extension_followup" and (not last or last < boundary))
            if not manual and (interval is None or (last and not first_after_extension and now - last < timedelta(minutes=interval))):
                return None
            token = uuid.uuid4().hex
            state["collection"] = dict(previous, token=token, last_attempt=encode_time(now), status="수집 중",
                                       polling_mode=plan["mode"], message="상단 공지 조회 중")
            return token, deepcopy(state)

    def fail_collection(self, token, message):
        with self._transaction() as state:
            status = state.get("collection", {})
            if status.get("token") == token:
                status.update(status="수집 실패", message=str(message)[:1500])

    def finish_collection(self, token, listing, fetched, errors, *, cancelled=False):
        """Commit to the server captured at dispatch, never whichever UI is active.

        listing includes every pinned ID, even excluded/disabled categories.
        Any partial body failure defers unpin retirement for this cycle.
        Raw source records are separate from explicitly dated notification events.
        """
        from .notice_analysis import analyze_notice, notice_events

        with self._transaction() as state:
            status = state.get("collection", {})
            if status.get("token") != token:
                return False
            if cancelled or not state["settings"]["collection_enabled"]:
                status.update(status="수집 중단", message="설정 변경 또는 모듈 중지로 결과를 적용하지 않았습니다.")
                return False
            now = parse_time(self.clock())
            timestamp = encode_time(now)
            initial = not status.get("first_success")
            articles = state.setdefault("articles", {})
            pinned_ids = {row["id"] for row in listing}
            changed = 0
            for row in listing:
                key, category = row["id"], row["category"]
                if category is None or not state["settings"]["categories"].get(category, False):
                    if key in articles:
                        articles[key].update(row, pinned=True, removed_at=None, last_seen=timestamp)
                    continue
                old = articles.get(key, {})
                body = fetched.get(key)
                article = dict(old, **row, pinned=True, removed_at=None, last_seen=timestamp,
                               first_seen=old.get("first_seen", timestamp), body_error=errors.get(key, ""))
                if body:
                    different = body["content_hash"] != old.get("content_hash")
                    article.update(body, last_checked=timestamp)
                    article["revision"] = int(old.get("revision", 0)) + int(different)
                    article["changed_at"] = timestamp if different else old.get("changed_at", timestamp)
                    changed += int(different)
                article["baseline"] = old.get("baseline", initial)
                date = article.get("published_date")
                article["recent"] = bool(date and (now.date() - timedelta(days=3)).isoformat() <= date <= now.date().isoformat())
                articles[key] = article
                if body:
                    analysis = analyze_notice(article)
                    article["analysis"] = analysis
                    if not analysis["windows"] or any([fact["issue"] for fact in analysis["windows"]]):
                        article.setdefault("uncertain_first_seen", timestamp)
                    candidates = notice_events(article, analysis)
                    wanted = {event["id"] for event in candidates}
                    prefix = key + "/time-v1/"
                    # A successful changed body may remove or invalidate an old
                    # period. Revoke queued speech without losing user controls.
                    # Fetch failures deliberately never enter this branch.
                    for event in state["events"].values():
                        if (event["id"].startswith(prefix) and event["id"] not in wanted
                                and not event.get("retired_at")):
                            if event["id"].endswith(("/review", "/unknown-discovery")):
                                self._retire(state, event, "최신 분석 알림으로 대체", now)
                                continue
                            if event.get("analysis_hold"):
                                continue
                            event["analysis_hold"] = "최신 원문에서 이전 기간을 확인하지 못했습니다."
                            event["revision"] += 1
                            event["updated_at"] = timestamp
                    for event in candidates:
                        self.register(event, collected=True, _state=state)
            if not errors:
                for key, article in articles.items():
                    if key not in pinned_ids and article.get("pinned"):
                        article.update(pinned=False, removed_at=timestamp)
                def source_article(key):
                    if key.startswith('schedule/'):
                        return None  # Local schedule/<season>/... is never a cafe article.
                    match = re.match(r"^([A-Za-z0-9_-]+/\d+)(?:/|$)", key)
                    return match[1] if match else None
                for event in state["events"].values():
                    article_id = source_article(event["source_key"])
                    if article_id and article_id not in pinned_ids and not event.get("retired_at"):
                        self._retire(state, event, "공지 해제", now)
                state["suppressed"] = {key: value for key, value in state["suppressed"].items()
                                       if source_article(key) is None or source_article(key) in pinned_ids
                                       or key.endswith(ONE_SHOT_SUFFIXES)}
                status.update(last_success=timestamp, first_success=status.get("first_success") or timestamp)
            status.update(status="부분 수집 실패" if errors else "수집 완료", last_finished=timestamp,
                          message=f"상단 공지 {len(listing)}개 / 본문 확인 {len(fetched)}개 / 변경 {changed}개 / 오류 {len(errors)}개",
                          errors=errors, changed=changed)
            self._prune(state)
            return True


def prune_all_servers(root):
    """Called at GUI startup/hourly; touches only this module's own JSON records."""
    root = Path(root)
    errors = []
    if not root.exists():
        return errors
    for path in root.glob("*.json"):
        if not re.fullmatch(r"[0-9a-f]{64}", path.stem) or path.is_symlink():
            continue
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            store = NoticeStore(root, state["server_id"])
            if store.path != path:
                raise NoticeError("서버별 알림 파일 식별자가 다릅니다.")
            store.snapshot()
        except Exception as exc:
            errors.append(f"{path.name}: {exc}")
    return errors
