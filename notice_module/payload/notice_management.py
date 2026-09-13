"""Updateable per-client, per-server notice controls. No crawler or TTS yet.

Future collectors call register(); playback checks delivery_token immediately
before playing/resuming and calls complete_delivery only after successful audio.
Every notice must have an explicit validity interval to be playable.
"""
from __future__ import annotations

from contextlib import contextmanager
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
            raise NoticeError("서버를 선택한 뒤 알림 관리를 열어 주세요.")
        self.key = hashlib.sha256(self.server_id.encode("utf-8")).hexdigest()
        self.path = self.root / f"{self.key}.json"
        self.clock = clock
        self.thread_lock = threading.Lock()

    def _load(self):
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"schema": 1, "server_id": self.server_id, "generation": 0,
                    "settings": {"collection_enabled": True, "output_enabled": False,
                                 "categories": dict.fromkeys(CATEGORIES, True), "rules": deepcopy(DEFAULT_RULES)},
                    "events": {}, "suppressed": {}}
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
                self._prune(state)
                yield state
                if state != before:
                    self._save(state)
            finally:
                release()

    def _retire(self, state, event, reason, when):
        event.update(retired_at=encode_time(when), retired_reason=reason, enabled=False)
        event["revision"] += 1
        # Only an opaque source identity is retained beyond history retention.
        if event.get("source_key"):
            state["suppressed"][event["source_key"]] = True

    def _prune(self, state):
        now = parse_time(self.clock())
        for key, event in list(state["events"].items()):
            end = parse_time(event.get("valid_until"))
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

    def register(self, event, *, collected=False):
        key = str(event.get("id") or "").strip()
        title = str(event.get("title") or "").strip()
        category = event.get("category", "general")
        if not key or not title or category not in CATEGORIES:
            raise NoticeError("알림 ID, 제목, 종류를 확인해 주세요.")
        first, last = validate_interval(event.get("valid_from"), event.get("valid_until"))
        period_start, period_end = validate_interval(event.get("event_from"), event.get("event_until"))
        source_key = str(event.get("source_key") or "")
        # Callers must supply an alert-specific identity (article + rule/window).
        if collected and not source_key:
            raise NoticeError("수집 알림은 공지와 안내 규칙을 식별하는 source_key가 필요합니다.")
        with self._transaction() as state:
            if collected and (not state["settings"]["collection_enabled"]
                              or not state["settings"]["categories"][category]
                              or source_key in state["suppressed"]):
                return False
            old = state["events"].get(key)
            if old and old.get("retired_at"):
                return False
            item = {"id": key, "title": title, "category": category, "source_key": source_key,
                    "source_url": str(event.get("source_url") or ""), "body": str(event.get("body") or ""),
                    "tts_text": str(event.get("tts_text") or ""), "valid_from": first, "valid_until": last,
                    "event_from": period_start, "event_until": period_end, "enabled": True,
                    "revision": 1, "created_at": encode_time(self.clock()), "updated_at": encode_time(self.clock()),
                    "delivered_revision": None, "last_delivery": None, "manual_period": False}
            if old:
                for field in ("enabled", "created_at", "last_delivery", "delivered_revision", "manual_period"):
                    item[field] = old[field]
                if collected and old["manual_period"]:
                    item["valid_from"], item["valid_until"] = old["valid_from"], old["valid_until"]
                meaningful = ("title", "category", "source_key", "source_url", "body", "tts_text", "valid_from", "valid_until", "event_from", "event_until")
                if all(item[field] == old[field] for field in meaningful):
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
                if event["source_key"] and event["source_key"] not in current_keys and not event.get("retired_at"):
                    self._retire(state, event, "공지 해제", self.clock())
            state["suppressed"] = {key: True for key in state["suppressed"] if key in current_keys}

    def delete_history(self, keys=None):
        with self._transaction() as state:
            selected = set(keys) if keys is not None else set(state["events"])
            removed = 0
            for key in list(state["events"]):
                if key in selected and state["events"][key].get("retired_at"):
                    del state["events"][key]
                    removed += 1
            return removed

    def delivery_token(self, key):
        """None means do not enqueue/play/resume. No connection arbitration."""
        with self._transaction() as state:
            event = state["events"].get(key)
            if (not state["settings"]["output_enabled"] or not event
                    or not event.get("tts_text", "").strip()
                    or event_status(event, self.clock()) != "유효 · 송출 조건 대기"):
                return None
            return (self.server_id, key, event["revision"], state["generation"])

    def complete_delivery(self, token):
        with self._transaction() as state:
            server_id, key, revision, generation = token
            event = state["events"].get(key)
            if (server_id != self.server_id or generation != state["generation"] or not event
                    or not state["settings"]["output_enabled"] or event["revision"] != revision
                    or event_status(event, self.clock()) != "유효 · 송출 조건 대기"):
                return False
            event.update(delivered_revision=revision, last_delivery=encode_time(self.clock()))
            return True

    def claim_collection(self, *, manual=False):
        """Persistent per-server due check. Manual bypasses hours, not disable."""
        with self._transaction() as state:
            now = parse_time(self.clock())
            settings = state["settings"]
            if not settings["collection_enabled"]:
                return None
            interval = collection_interval(settings, now)
            previous = state.get("collection", {})
            last = parse_time(previous.get("last_attempt"))
            if not manual and (interval is None or (last and now - last < timedelta(minutes=interval))):
                return None
            token = uuid.uuid4().hex
            state["collection"] = dict(previous, token=token, last_attempt=encode_time(now), status="수집 중", message="상단 공지 조회 중")
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
        Raw source records are not playable notification events.
        """
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
            if not errors:
                for key, article in articles.items():
                    if key not in pinned_ids and article.get("pinned"):
                        article.update(pinned=False, removed_at=timestamp)
                def source_article(key):
                    match = re.match(r"^([A-Za-z0-9_-]+/\d+)(?:/|$)", key)
                    return match[1] if match else None
                for event in state["events"].values():
                    article_id = source_article(event["source_key"])
                    if article_id and article_id not in pinned_ids and not event.get("retired_at"):
                        self._retire(state, event, "공지 해제", now)
                state["suppressed"] = {key: value for key, value in state["suppressed"].items()
                                       if source_article(key) is None or source_article(key) in pinned_ids}
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
