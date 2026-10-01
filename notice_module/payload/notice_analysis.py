"""Conservative, offline extraction of explicitly dated notice windows.

No OCR/LLM/network, no schedule mutation and no speech. Uncertain windows stay
visible in the source; a one-time discovery message never claims a deadline.
"""
from datetime import datetime, timedelta
import re

from .notice_management import KST, encode_time, parse_time

ANALYSIS_VERSION = 2
START_NOTICE_MINUTES = 15
DISCOVERY_VALID_HOURS = 24  # Announcement lifetime, NOT the unknown event deadline.
DATE = r"(?:(?:20\d{2})\s*년\s*)?\d{1,2}\s*월\s*\d{1,2}\s*일|(?:20\d{2}[./-])?\d{1,2}[./-]\d{1,2}"
TIME = r"(?:오전\s*|오후\s*)?\d{1,2}(?::\d{2}|\s*시(?:\s*\d{1,2}\s*분)?)"
WEEKDAY = r"(?:\s*\([월화수목금토일](?:요일)?\))?"
RANGE = re.compile(rf"(?<![\d./-])(?P<date>{DATE}){WEEKDAY}\s*(?P<start>{TIME})\s*[~～∼–—]\s*"
                   rf"(?:(?P<end_date>{DATE}){WEEKDAY}\s*)?(?P<end>{TIME})(?![\d:])")
POINT = re.compile(rf"(?<![\d./-])(?P<date>{DATE}){WEEKDAY}\s*(?P<time>{TIME})(?![\d:])")
AFTER_MAINTENANCE = re.compile(
    rf"(?<![\d./-])(?P<date>{DATE}){WEEKDAY}\s*(?:정기\s*)?점검\s*(?:이후|후)\s*[~～∼–—]\s*"
    rf"(?P<end_date>{DATE}){WEEKDAY}\s*(?P<end>{TIME})(?![\d:])")
CLASS_SHARED_PERIOD = re.compile(r"클래스변경권(?:판매|구매)및클래스변경기간")
KINDS = {"maintenance": "점검", "transfer_sale": "이전권 판매", "transfer_use": "서버 이전",
         "class_purchase": "클래스 변경권 구매/획득", "class_use": "클래스 변경권 사용",
         "item_drop": "아이템 획득", "item_exchange": "아이템 교환/조합",
         "item_use": "아이템 사용", "item_delete": "아이템 삭제"}


def _date(value, published):
    numbers = [int(n) for n in re.findall(r"\d+", value)]
    explicit_year = len(numbers) == 3
    if explicit_year:
        year, month, day = numbers
    else:
        if not published:
            raise ValueError("연도를 확인할 작성일이 없습니다.")
        year = datetime.fromisoformat(published).year
        month, day = numbers
    result = datetime(year, month, day, tzinfo=KST)
    if not explicit_year and abs((result.date() - datetime.fromisoformat(published).date()).days) > 180:
        raise ValueError("연말/연초의 연도가 불명확합니다.")
    return result


def _time(day, value):
    values = [int(n) for n in re.findall(r"\d+", value)]
    hour, minute = values[0], values[1] if len(values) > 1 else 0
    if "오전" in value or "오후" in value:
        if not 1 <= hour <= 12:
            raise ValueError("오전/오후 시각이 올바르지 않습니다.")
        hour = hour % 12 + (12 if "오후" in value else 0)
    if hour == 24 and minute == 0:
        return day + timedelta(days=1)
    return day.replace(hour=hour, minute=minute)


def _kind(line, category):
    compact = re.sub(r"\s+", "", line)
    if category == "transfer":
        if "이전권" in compact and any([word in compact for word in ("판매기간", "판매일정", "구매기간")]):
            return "transfer_sale"
        if "서버이전기간" in compact or "서버이전일정" in compact:
            return "transfer_use"
    if category == "maintenance" and re.search(r"점검.{0,10}(?:일시|시간|일정|기간)", compact):
        return "maintenance"
    if category == "class_change":
        if CLASS_SHARED_PERIOD.search(compact):
            return "class_purchase"
        if any([word in compact for word in ("판매기간", "구매기간", "획득기간", "수령기간")]):
            return "class_purchase"
        if any(word in compact for word in ('사용기간', '이용기간', '클래스변경기간')):
            return "class_use"
    if category == "event":
        for kind, words in (("item_drop", ("드롭기간", "드랍기간", "획득기간", "획득기한")),
                            ("item_exchange", ("교환기간", "교환기한", "조합기간", "조합기한", "제작기간")),
                            ("item_use", ("사용기간", "사용기한")),
                            ("item_delete", ("삭제일시", "삭제시각", "삭제예정일", "삭제기간"))):
            if any([word in compact for word in words]):
                return kind
    return None


def analyze_notice(article, *, related_articles=(), now=None):
    category = article.get("category")
    if category in {"update", "general"}:
        return {"version": ANALYSIS_VERSION, "windows": [], "status": "공지 발견 1회 안내 · 반복 없음"}
    if category not in {"transfer", "maintenance", "class_change", "event"}:
        return {"version": ANALYSIS_VERSION, "windows": [], "status": "이 유형의 자동 알림은 다음 단계에서 연결합니다."}
    windows, counters = [], {}
    context = None
    context_age = 0
    context_end_only = False
    class_scope = False
    for raw in (article.get("body") or "").splitlines():
        line = raw.strip()
        heading = bool(re.match(r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.|\d+\)|■|【)", line))
        if category == "class_change":
            if "클래스변경권" in re.sub(r"\s+", "", line):
                class_scope = True
            elif heading and not _kind(line, category):
                class_scope = False
        selected = _kind(line, category)
        if category == "class_change" and not class_scope:
            selected = None
        if selected:
            context, context_age = selected, 0
            context_end_only = category == "event" and (selected == "item_delete" or "기한" in line)
        elif heading:
            context = None
        context_age += 1
        if not context or context_age > 12:
            continue
        # A date plus range delimiter signals a candidate even if its end is
        # "점검 전". Keep that case held instead of borrowing a different time.
        has_range = bool(re.search(r"[~～∼–—]", line))
        point_deadline = category == "event" and (context_end_only or "기한" in line)
        if not re.search(DATE, line) or not (has_range or point_deadline):
            continue
        counters[context] = counters.get(context, 0) + 1
        ordinal = re.search(r"(\d+)\s*회차", line)
        key = f"{context}/{ordinal[1] if ordinal else counters[context]}"
        fact = {"key": key, "kind": context, "label": KINDS[context], "evidence": line,
                "start": None, "end": None, "issue": "날짜/시각을 끝까지 읽지 못했습니다."}
        matches = list(RANGE.finditer(line))
        if len(matches) == 1:
            try:
                match = matches[0]
                day = _date(match["date"], article.get("published_date"))
                end_day = _date(match["end_date"], article.get("published_date")) if match["end_date"] else day
                start, end = _time(day, match["start"]), _time(end_day, match["end"])
                if end <= start:
                    raise ValueError("종료일이 빠르거나 자정 이후 날짜가 불명확합니다.")
                fact.update(start=encode_time(start), end=encode_time(end), issue="")
            except ValueError as exc:
                fact["issue"] = str(exc)
        elif category == 'class_change' and len(list(AFTER_MAINTENANCE.finditer(line))) == 1:
            match = AFTER_MAINTENANCE.search(line)
            try:
                day = _date(match['date'], article.get('published_date'))
                end = _time(_date(match['end_date'], article.get('published_date')), match['end'])
                if end <= day:
                    raise ValueError('종료일이 점검 시작 날짜보다 빠릅니다.')
                fact.update(end=encode_time(end), issue='', start_condition='maintenance_end',
                            start_date=day.date().isoformat(),
                            note='종료 시각 확정 · 시작은 해당 날짜 정기점검 종료 후 (시각 미확정)')
                if now is not None:
                    from .notice_server_open import server_open_override
                    maintenance = {}
                    for index, related in enumerate(related_articles):
                        if related.get('category') == 'maintenance':
                            maintenance[str(index)] = dict(related, analysis=analyze_notice(related))
                    opening = server_open_override({'articles': maintenance}, now)
                    if (opening and parse_time(opening['maintenance_start']).date() == day.date()
                            and parse_time(opening['server_open']) < end):
                        fact.update(start=opening['server_open'], start_source_id=opening['source_id'],
                                    note='해당 날짜 정기점검 공지의 종료 시각과 연결 · 종료 시각은 클래스 변경 공지 기준')
            except (ValueError, TypeError) as exc:
                fact.update(start=None, end=None, issue=str(exc))
        elif not has_range and point_deadline:
            points = list(POINT.finditer(line))
            if len(points) == 1:
                try:
                    day = _date(points[0]["date"], article.get("published_date"))
                    fact.update(end=encode_time(_time(day, points[0]["time"])), issue="")
                except ValueError as exc:
                    fact["issue"] = str(exc)
        windows.append(fact)
        if category == 'class_change' and CLASS_SHARED_PERIOD.search(re.sub(r'\s+', '', line)):
            counters['class_use'] = counters.get('class_use', 0) + 1
            windows.append(dict(fact, key=f"class_use/{counters['class_use']}",
                                kind='class_use', label=KINDS['class_use']))
    # Conflicting duplicate sale-slot numbers must never silently choose one.
    seen = {}
    for fact in windows:
        if fact["key"] in seen:
            for duplicate in (fact, seen[fact["key"]]):
                duplicate.update(start=None, end=None, issue="같은 회차의 기간이 중복되어 확인이 필요합니다.")
        seen[fact["key"]] = fact
    windows = list(seen.values())
    if category == "maintenance" and len(windows) > 1:
        for fact in windows:
            fact.update(start=None, end=None, issue="점검 기간이 여러 개입니다. 최종 적용 시간을 확인해 주세요.")
    if category == "event":
        for fact in windows:
            if sum(other["kind"] == fact["kind"] for other in windows) > 1:
                fact.update(start=None, end=None, issue="같은 종류의 아이템 기간이 여러 개입니다. 대상별 구분을 확인해 주세요.")
    good = sum(not fact["issue"] for fact in windows)
    partial = sum(not fact['issue'] and not fact['start'] and fact.get('start_condition') == 'maintenance_end'
                  for fact in windows)
    return {"version": ANALYSIS_VERSION, "windows": list(seen.values()),
            "status": (f"기간 확인 {good - partial}개 / 종료 확정·점검 후 시작 {partial}개 / 확인 필요 {len(windows) - good}개"
                       if partial else f"기간 확인 {good}개 / 확인 필요 {len(windows) - good}개")
                      if windows else "기간 미확정 · 원문 확인 필요"}


def _spoken_time(value):
    return value.strftime("%m월 %d일 %H시 %M분")


def notice_events(article, analysis):
    """Create timed events with catalog-owned speech templates.

    Opportunity/repeat metadata is for the future audio scheduler, not permission
    to speak immediately. Stable source keys preserve user-disabled checkboxes.
    """
    events = []
    if article.get("category") not in {"maintenance", "transfer", "class_change", "event", "update", "general"}:
        return events
    anchor = parse_time(article.get("first_seen"))
    prefix = article["id"] + "/time-v1/"

    def add(suffix, title, start, end, fact=None, trigger="immediate"):
        if start and end and start >= end:
            return
        key = prefix + suffix
        from .notice_templates import event_template_key, render_template
        template_key = event_template_key(article, suffix, fact)
        values = {"공지제목": article["title"], "기한종류": (fact or {}).get("label", "")}
        if fact and not fact.get('start') and fact.get('start_condition') == 'maintenance_end':
            values.update(시작시간='점검 종료 후', _start_date=fact['start_date'])
        for field, source_field in (("시작시간", "start"), ("종료시간", "end")):
            when = parse_time((fact or {}).get(source_field))
            if when:
                values[field] = _spoken_time(when)
                values["_" + source_field + "_date"] = when.date().isoformat()
        text = render_template({}, template_key, values)
        events.append({"id": key, "source_key": key, "title": title, "category": article["category"],
                       "source_url": article["url"], "body": title, "tts_text": text,
                       "tts_template": template_key, "tts_values": values,
                       "valid_from": start, "valid_until": end,
                       "event_from": (fact or {}).get("start"), "event_until": (fact or {}).get("end"),
                       "analysis_evidence": (fact or {}).get("evidence", ""), "trigger": trigger})

    if article["category"] in {"update", "general"}:
        if anchor:
            add("general-discovery", article["title"], anchor, anchor + timedelta(hours=DISCOVERY_VALID_HOURS))
            events[-1].update(retire_after_delivery=True, analysis_evidence="공지 발견 후 24시간 이내 1회 안내")
        return events

    for fact in analysis["windows"]:
        start, end = parse_time(fact["start"]), parse_time(fact["end"])
        if (fact['kind'] in {'class_purchase', 'class_use'} and end and not fact['issue']
                and not start and fact.get('start_condition') == 'maintenance_end'):
            # This is an explicit deadline, NOT an invented opening time.
            # Until the maintenance is resolved, generate only end-based speech.
            buying = fact['kind'] == 'class_purchase'
            add(fact['key'] + '/deadline', '클래스 변경권 ' + ('구매' if buying else '사용') + ' 마감 안내',
                max(datetime.fromisoformat(fact['start_date']).replace(tzinfo=KST),
                    end - timedelta(hours=24)), end, fact, 'major_boss_after')
            events[-1]['policy'] = 'deadline_repeat'
            continue
        if fact["kind"].startswith("item_") and end and not fact["issue"]:
            label = fact["label"]
            add(fact["key"] + "/deadline", article["title"] + " · " + label + " 마감",
                max(start, end - timedelta(hours=24)) if start else end - timedelta(hours=24), end, fact, "major_boss_after")
            events[-1]["policy"] = "deadline_repeat"
            continue
        if not start or not end:
            continue
        if fact["kind"] == "maintenance":
            label = "정기점검" if "정기" in article["title"] else "임시점검" if "임시" in article["title"] else "점검"
            extension = " 연장" if "연장" in article["title"] else ""
            add(fact["key"] + "/discovery", label + extension + " 안내", anchor, end, fact)
        elif fact["kind"] == "transfer_sale":
            title = f"이전권 {fact['key'].split('/')[-1]}회차 판매"
            preview = (start - timedelta(days=1)).replace(hour=18, minute=0, second=0)
            add(fact["key"] + "/preview", title + " 예고",
                preview, start, fact, "major_boss_after")
            add(fact["key"] + "/start", title + " 시작",
                start, min(end, start + timedelta(minutes=START_NOTICE_MINUTES)), fact)
            add(fact["key"] + "/deadline_60", title + " 종료 전 안내",
                max(start, end - timedelta(hours=1)), end - timedelta(minutes=10), fact)
            add(fact["key"] + "/deadline_10", title + " 마감 임박",
                max(start, end - timedelta(minutes=10)), end, fact)
            add(fact["key"] + "/boss_reminder", title + " 구매 안내",
                start, end, fact, "major_boss_after")
            events[-1]["policy"] = "transfer_repeat"
        elif fact["kind"] in {"class_purchase", "class_use"}:
            buying = fact["kind"] == "class_purchase"
            title = "클래스 변경권 " + ("구매" if buying else "사용")
            if buying:
                add(fact["key"] + "/purchase_notice", title + " 안내", max(anchor or start, start), end, fact)
            add(fact["key"] + "/deadline", title + " 마감 안내",
                max(start, end - timedelta(hours=24)), end, fact, "major_boss_after")
            events[-1]["policy"] = "deadline_repeat"
        elif fact["kind"] == "transfer_use":
            season = re.search(r"(?<!\d)(\d{1,4})\s*차\s*서버\s*이전", article["title"])
            uses = [window for window in analysis["windows"] if window["kind"] == "transfer_use"]
            if season and int(season[1]) > 0 and len(uses) == 1:
                add(fact["key"] + "/season_close", "시즌 종료 응원",
                    max(start, end - timedelta(minutes=15)), end, fact)
                events[-1].update(season_number=int(season[1]), retire_after_delivery=True)
    if not analysis["windows"] or any([fact["issue"] for fact in analysis["windows"]]):
        # One article, one discovery announcement, regardless of how many
        # ambiguous sale slots it contains. No claimed sale/event deadline.
        compact = re.sub(r"\s+", "", article.get("title", "") + article.get("body", ""))
        category = article["category"]
        if category == "transfer":
            label = "이전권 판매" if "이전권" in compact else "서버이전"
        elif category == "maintenance":
            label = "정기점검" if "정기" in article["title"] else "임시점검" if "임시" in article["title"] else "점검"
            if "연장" in article["title"]:
                label += " 연장"
        elif category == "class_change":
            label = "클래스 변경권" if "클래스변경권" in compact else "신규 직업"
        else:
            label = "이벤트"
        discovered = parse_time(article.get("uncertain_first_seen")) or anchor
        if discovered:
            add("unknown-discovery", label + " 공지 발견 · 1회 안내",
                discovered, discovered + timedelta(hours=DISCOVERY_VALID_HOURS))
            events[-1].update(retire_after_delivery=True,
                             analysis_evidence="행사 기간 미확정. 안내 자체는 등록 후 24시간 이내 1회만 유효합니다.")
    return events
