"""Complete speech scenario catalog and safe, server-owned text templates."""
from string import Formatter
from datetime import date, timedelta
import re
from .notice_seasons import CHEERS

TEMPLATES = {}
EXAMPLES = {"공지제목": "이벤트 아이템 이용 안내", "알림제목": "길드던전",
            "시작시간": "09월 20일 18시 30분", "종료시간": "09월 20일 23시 59분", "차수": "18"}


def _add(key, category, name, text, condition, variables=()):
    # Historic defaults used '시간' for a full date+time. Keep that wording via
    # explicit '일시' aliases; the new '시간' fields always mean clock time only.
    for label in ('시작', '종료'):
        if label + '시간' in variables:
            if '{' + label + '날짜}' not in text:
                text = text.replace('{' + label + '시간}', '{' + label + '일시}')
            variables = (*variables, label + '날짜', label + '일시')
    TEMPLATES[key] = dict(id=key, category=category, name=name, text=text,
                          condition=condition, variables=tuple(variables))


for kind, label in (("regular", "정기점검"), ("temporary", "임시점검"), ("other", "점검")):
    for extended in (False, True):
        name = label + (" 연장" if extended else "")
        suffix = kind + (".extension" if extended else "")
        _add("maintenance." + suffix, "점검", name + " · 기간 확인",
             name + " 공지가 올라왔습니다. 점검은 {시작시간}부터 {종료시간}까지입니다.",
             "기간을 확인한 점검 공지 발견 시 1회", ("시작시간", "종료시간"))
        _add("unknown.maintenance." + suffix, "기간 미확정", name,
             name + " 공지가 올라왔습니다.", "기간 미확정 공지 발견 후 24시간 이내 1회 · 완료 후 폐기")

for key, label in (("transfer_sale", "이전권 판매"), ("transfer_use", "서버이전"),
                   ("class_purchase", "클래스 변경권"), ("new_class", "신규 직업"), ("event", "이벤트")):
    _add("unknown." + key, "기간 미확정", label, label + " 공지가 올라왔습니다.",
         "기간 미확정 공지 발견 후 24시간 이내 1회 · 완료 후 폐기")

for key, label in (("update", "업데이트"), ("issue", "업데이트 후 확인된 문제"), ("general", "새")):
    _add("discovery." + key, "공지 발견", label + " 공지", label + " 공지가 올라왔습니다. {공지제목}",
         "공지 발견 후 24시간 이내 1회 · 완료 후 폐기", ("공지제목",))

for key, name, text, condition in (
    ("preview", "판매 전날 예고", "서버이전권이 {시작시간}부터 판매됩니다. 꼭 구매해 주세요.", "판매 전날 18시부터 판매 시작 전 · 주요 보스 안내 후 1회"),
    ("start", "판매 시작", "서버이전권 판매가 시작됐습니다. {종료시간}까지 꼭 구매해 주세요.", "판매 시작 후 유효 구간에서 1회"),
    ("deadline_60", "판매 종료 1시간 전", "서버이전권 판매가 {종료시간}에 종료됩니다. 꼭 구매해 주세요.", "마감 1시간 전부터 10분 전까지 1회"),
    ("deadline_10", "판매 종료 10분 전", "서버이전권 판매가 곧 종료됩니다. {종료시간}까지 꼭 구매해 주세요.", "마감 10분 전부터 종료 전까지 1회"),
    ("boss_reminder", "판매 중 구매 독려", "서버이전권을 {종료시간}까지 꼭 구매해 주세요.", "판매 중 주요 보스 안내 후 · 최소 30분 간격 · 하루 횟수 상한 없음")):
    _add("transfer." + key, "서버이전권", name, text, condition, ("시작시간", "종료시간"))

for key, name, verb, condition in (
    ("purchase", "구매 안내", "구매", "확인된 구매 기간 내 1회"),
    ("purchase_deadline", "구매 마감", "구매", "마감 24시간 이내 주요 보스 안내 후 · 같은 공지 합계 하루 2회"),
    ("use_deadline", "사용 마감", "사용", "마감 24시간 이내 주요 보스 안내 후 · 같은 공지 합계 하루 2회")):
    _add("class." + key, "클래스 변경권", name, "클래스 변경권을 {종료시간}까지 꼭 " + verb + "해 주세요.",
         condition, ("시작시간", "종료시간"))

for key, label in (("item_drop", "아이템 획득"), ("item_exchange", "아이템 교환/조합"),
                   ("item_use", "아이템 사용"), ("item_delete", "아이템 삭제")):
    # Actual labels are supplied by the analyzer, not guessed by the template.
    text = ("{공지제목}. {기한종류} 시각은 {종료시간}입니다. 삭제 전에 아이템을 확인해 주세요." if key == "item_delete"
            else "{공지제목}. {기한종류} 기한이 {종료시간}까지입니다. 잊지 말고 확인해 주세요.")
    _add(key + ".deadline", "이벤트 아이템", label, text,
         "마감 24시간 이내 주요 보스 안내 후 · 같은 공지 합계 하루 2회", ("공지제목", "기한종류", "종료시간"))
EXAMPLES["기한종류"] = "아이템 사용"

for index, text in enumerate(CHEERS, 1):
    _add(f"season.close.{index}", "시즌 종료", f"응원 문장 {index} / 5", text,
         "서버이전 종료 15분 전 · 시즌마다 5개 문장을 순환 · 한 시즌 1회", ("차수",))

_add("participation.general", "참여 독려", "보스 / 길던 참여 독려",
     "{참여안내}",
     "별 체크·지정 행사 중 21~24시 대표 대상 · 1차 18~19시 / 2차 20시 월드보스 안내 뒤 · 각 1회",
     ("알림제목", "시작시간", "보스목록", "참여안내"))
_add('participation.siege_noon', '참여 독려', '공성전 당일 정오 추가 안내',
     '오늘은 공성전이 있는 날입니다. 많은 참여 부탁드립니다.',
     '공성전 당일 12시 월드보스 1분 전 안내 완료 후 1회 · 저녁 두 번과 별도')
_add("manual.general", "직접 등록", "수동 일반 안내", "{알림제목} 안내입니다.",
     "수동 알림의 유효기간 내 1회 · 개별 문장 입력 시 개별 문장 우선", ("알림제목",))

for key, name in (
    ('dawn', '다음 날 새벽 보스 안내'),
    ('morning', '점검 전 아침 보스 안내')):
    _add('participation.' + key, '자동 참여 독려', name,
         '{보스목록} 일정이 있습니다. 많은 참여 부탁드립니다.',
         ('분/초 확정 새벽 보스 · 22~24시 주요·연타임 보스 1분 전 안내 뒤 / 없으면 23시 대기열 · 별도 하루 1회'
          if key == 'dawn' else '분/초 확정 스케줄에서 자동 등록 · 기존 참여 독려와 합계 하루 2회'),
         ('알림제목', '시작시간', '보스목록'))
EXAMPLES['보스목록'] = '내일 새벽 2시 30분 최하층 강글'
EXAMPLES['참여안내'] = '오늘 저녁 22시 공성전 외 2개 일정이 있습니다. 많은 참여 부탁드립니다.'


def migrate_participation_templates(state):
    """Merge the redundant evening setting, preserving user overrides/history."""
    old, new = 'participation.evening', 'participation.general'
    if not state.get('participation_template_merged'):
        templates = state.setdefault('tts_templates', {})
        if old in templates and new not in templates:
            templates[new] = templates[old]
        # Retain the old custom text as inactive data if both were edited.
        flags = state.setdefault('tts_template_enabled', {})
        if old in flags:
            flags[new] = flags.get(new, True) and flags[old]
        state['participation_template_merged'] = True
        if old in templates or old in flags or any(e.get('tts_template') == old for e in state.get('events', {}).values()):
            state['tts_templates_revision'] = state.get('tts_templates_revision', 0) + 1
            state['generation'] += 1
    for event in state.get('events', {}).values():
        if event.get('tts_template') == old and not event.get('retired_at'):
            event['tts_template'] = new


def event_template_enabled(state, event):
    key = event.get('tts_template')
    if key == 'participation.evening':
        key = 'participation.general'
    if not key:
        # Explicitly typed manual speech still obeys the scenario switch.
        if event.get('category') == 'participation':
            key = 'participation.general'
        elif event.get('id', '').startswith('manual-'):
            key = 'manual.general'
    return state.get('tts_template_enabled', {}).get(key, True) is True


def migrate_date_templates(state):
    """Preserve old custom date+time meanings; never rewrite literal speech."""
    if state.get('tts_date_syntax', 1) >= 2:
        return
    changed = False
    for key, text in list(state.get('tts_templates', {}).items()):
        if key not in TEMPLATES:
            continue
        parts = []
        for literal, field, spec, conversion in Formatter().parse(text):
            parts.append(literal.replace('{', '{{').replace('}', '}}'))
            if field is not None:
                renamed = {'시작시간': '시작일시', '종료시간': '종료일시'}.get(field, field)
                parts.append('{' + renamed + ('!' + conversion if conversion else '') + (':' + spec if spec else '') + '}')
        updated = ''.join(parts)
        changed |= updated != text
        state['tts_templates'][key] = updated
    state['tts_date_syntax'] = 2
    if changed:
        state['tts_templates_revision'] = state.get('tts_templates_revision', 0) + 1
        state['generation'] += 1


def _boss_speech_group(source):
    from .notice_management import parse_time
    ordered = sorted(source, key=lambda entry: parse_time(entry['at']))
    if not ordered:
        return []
    start = parse_time(ordered[0]['at'])
    return [entry for entry in ordered if parse_time(entry['at']) - start < timedelta(minutes=30)]


def _boss_group_names(group):
    names = list(dict.fromkeys(entry['name'] for entry in group))
    return ', '.join(names) if len(names) <= 2 else f'{names[0]} 외 {len(names) - 1}개'


def _spoken_boss_entries(source, now=None):
    """Speak one time for the first, non-chaining 30-minute window only.

    Keep the full input untouched: this is a speech projection, not a change
    to the schedule or the registered event's supporting data.
    """
    from .notice_management import parse_time
    group = _boss_speech_group(source)
    if not group:
        return ''
    first = group[0]
    when = parse_time(first['at'])
    split = date_time_values({'시작시간': when.strftime('%m월 %d일 %H시 %M분'),
                              '_start_date': when.date().isoformat()}, now)
    name_text = _boss_group_names(group)
    return ' '.join(part for part in (split['시작날짜'], first.get('period', ''),
                                      split['시작시간'], name_text) if part)


def spoken_title_dates(title):
    """Expand numeric dates for speech only; do not rewrite the stored title."""
    pattern = r'(?<![\d/])(?:(\d{4}|\d{2})/)?(\d{1,2})/(\d{1,2})(?![\d/])(?:\s*\(([월화수목금토일])(?:요일)?\))?'
    def expand(match):
        year, month, day, weekday = match.groups()
        try:
            date(int(year) if year and len(year) == 4 else 2000 + int(year) if year else 2000,
                 int(month), int(day))
        except ValueError:
            return match.group(0)
        return ((f'{int(year)}년 ' if year else '') + f'{int(month)}월 {int(day)}일'
                + (f' {weekday}요일' if weekday else ''))
    return re.sub(pattern, expand, title)


def date_time_values(values, now=None):
    """Derive speech fields; original absolute dates remain untouched in storage."""
    result = dict(values)
    if isinstance(result.get('공지제목'), str):
        result['공지제목'] = spoken_title_dates(result['공지제목'])
    pattern = r'(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일\s+(\d{1,2})시(?:\s*(\d{1,2})분)?'
    for label, source in (('시작', 'start'), ('종료', 'end')):
        raw = values.get(label + '일시') or values.get(label + '시간', '')
        match = re.fullmatch(pattern, raw) if isinstance(raw, str) else None
        iso = values.get('_' + source + '_date')
        day = date.fromisoformat(iso) if iso else None
        if match:
            year, month, dom, hour, minute = match.groups()
            day_text = f'{month}월 {dom}일'
            clock_text = f'{hour}시' + (f' {minute}분' if minute is not None else '')
            if not day and year:
                day = date(int(year), int(month), int(dom))
            if now is not None:
                clock_text = f'{int(hour)}시' + (f' {int(minute)}분' if minute and int(minute) else '')
        else:
            day_text, clock_text = values.get(label + '날짜', ''), values.get(label + '시간', '')
        if day and now is not None:
            day_text = ('오늘' if day == now.date() else '내일' if day == now.date() + timedelta(days=1)
                        else f'{day.month}월 {day.day}일')
        elif day and not day_text:
            day_text = f'{day.month}월 {day.day}일'
        result[label + '날짜'] = day_text
        result[label + '시간'] = clock_text
        result[label + '일시'] = ' '.join(part for part in (day_text, clock_text) if part)
    if values.get('_boss_entries'):
        result['보스목록'] = _spoken_boss_entries(values['_boss_entries'], now)
        # Older saved templates use {시작시간} + {알림제목} instead of
        # {보스목록}/{참여안내}. Apply the same window without replacing the
        # user's wording, stored title or full supporting schedule.
        result['알림제목'] = _boss_group_names(_boss_speech_group(values['_boss_entries']))
    elif not result.get('보스목록') and result.get('알림제목') and result.get('시작일시'):
        result['보스목록'] = result['시작일시'] + ' ' + result['알림제목']
    if values.get('_participation_name'):
        period = '' if values.get('_midnight') else '저녁 '
        if values.get('_boss_entries'):
            automatic = result['보스목록'] + ' 일정이 있습니다. 많은 참여 부탁드립니다.'
        else:
            automatic = f"{result['시작날짜']} {period}{result['시작시간']} {values['_participation_name']} 있습니다. 많은 참여 부탁드립니다."
            result['보스목록'] = f"{result['시작날짜']} {period}{result['시작시간']} {result.get('알림제목', '')}"
        result['참여안내'] = values.get('_manual_participation') or automatic
    elif not result.get('참여안내') and result.get('보스목록'):
        result['참여안내'] = result['보스목록'] + ' 일정이 있습니다. 많은 참여 부탁드립니다.'
    return result


def validate_template(key, text):
    if key not in TEMPLATES or not isinstance(text, str) or len(text) > 10000:
        raise ValueError("안내 유형과 10,000자 이내의 문장을 확인해 주세요.")
    allowed = TEMPLATES[key]["variables"]
    try:
        for _, field, spec, conversion in Formatter().parse(text):
            if field is not None and (field not in allowed or spec or conversion):
                raise ValueError("허용되지 않은 치환 항목: {" + field + "}")
    except ValueError as exc:
        raise ValueError(f"{TEMPLATES[key]['name']}: {exc}") from exc
    return text.strip()


def template_text(state, key):
    return state.get("tts_templates", {}).get(key, TEMPLATES[key]["text"])


def _same_day_end_time(values):
    """Shorten speech only; retain full values for storage and end-only notices."""
    pattern = r"(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일\s+(\d{1,2}시(?:\s*\d{1,2}분)?)"
    start_text, end_text = values.get("시작시간", ""), values.get("종료시간", "")
    if not isinstance(start_text, str) or not isinstance(end_text, str):
        return None
    start = re.fullmatch(pattern, start_text)
    end = re.fullmatch(pattern, end_text)
    if not start or not end:
        return None
    if tuple(int(part) if part else None for part in start.groups()[:3]) != tuple(
            int(part) if part else None for part in end.groups()[:3]):
        return None
    # New observations carry the year too, even though speech omits it.
    if values.get("_start_date") != values.get("_end_date"):
        return None
    return end.group(4)


def render_template(state, key, values, *, now=None):
    text = validate_template(key, template_text(state, key))
    needed = {field for _, field, _, _ in Formatter().parse(text) if field is not None}
    original = values
    values = date_time_values(values, now)
    if any(not isinstance(values.get(field), str) or not values[field].strip() for field in needed):
        raise ValueError("문장에 필요한 실제 날짜/정보가 없습니다. 미확정 정보를 추측하지 않습니다.")
    same_day = (bool(original.get('_start_date')) and original.get('_start_date') == original.get('_end_date'))
    same_day = same_day or _same_day_end_time(original) is not None
    parts, start_seen = [], False
    for literal, field, _, _ in Formatter().parse(text):
        parts.append(literal)
        if field is None:
            continue
        value = values[field]
        if field in {"종료일시", "종료날짜"}:
            if start_seen and same_day:
                value = values['종료시간'] if field == '종료일시' else ''
            start_seen = False
        elif field in {"시작일시", "시작날짜"}:
            start_seen = True
        parts.append(value)
    result = re.sub(r'[ \t]{2,}', ' ', "".join(parts)).strip()
    if len(result) > 10000:
        raise ValueError("치환된 안내 문장이 10,000자를 초과합니다.")
    return result


def maintenance_key(title):
    return ("regular" if "정기" in title else "temporary" if "임시" in title else "other") + (".extension" if "연장" in title else "")


def event_template_key(article, suffix, fact=None):
    category, title = article["category"], article["title"]
    if suffix == "general-discovery":
        return "discovery." + ("issue" if category == "update" and "확인된 문제" in title else
                                "update" if category == "update" else "general")
    if suffix == "unknown-discovery":
        compact = re.sub(r"\s+", "", title + article.get("body", ""))
        if category == "maintenance":
            return "unknown.maintenance." + maintenance_key(title)
        if category == "transfer":
            return "unknown.transfer_sale" if "이전권" in compact else "unknown.transfer_use"
        if category == "class_change":
            return "unknown.class_purchase" if "클래스변경권" in compact else "unknown.new_class"
        return "unknown.event"
    kind = (fact or {}).get("kind")
    if kind == "maintenance":
        return "maintenance." + maintenance_key(title)
    if kind == "transfer_sale":
        return "transfer." + suffix.rsplit("/", 1)[-1]
    if kind in {"class_purchase", "class_use"}:
        return "class." + ("purchase" if suffix.endswith("/purchase_notice") else
                            "purchase_deadline" if kind == "class_purchase" else "use_deadline")
    if kind and kind.startswith("item_"):
        return kind + ".deadline"
    if suffix.endswith("/season_close"):
        return "season.close.1"  # Store selects the actual per-server rotation slot.
    raise ValueError("기본 문구가 등록되지 않은 안내 규칙입니다: " + suffix)
