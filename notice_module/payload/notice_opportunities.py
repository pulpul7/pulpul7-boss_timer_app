"""Announcement opportunity rules, independent of game widgets and audio.

The future audio host supplies a completed boss one-minute announcement with a
stable occurrence ID. These rules never manufacture a boss event or play speech.
"""
from datetime import timedelta
import json

from .notice_management import encode_time, parse_time

POLICIES = {"once", "transfer_repeat", "participation", "deadline_repeat", "participation_slot"}
OPPORTUNITY_MAX_AGE_SECONDS = 120
TRANSFER_GAP_MINUTES = 30
LIMITED_GAP_MINUTES = 60
LIMITED_DAILY_COUNT = 2
PARTICIPATION_START_HOUR = 18
PARTICIPATION_END_HOUR = 23


def normalize_opportunity(value, server_id, now):
    if not isinstance(value, dict):
        return None
    try:
        when = parse_time(value.get("at"))
        scheduled = parse_time(value.get('scheduled_at'))
        chapter = value.get("chapter")
        if (not isinstance(value.get("id"), str) or not 0 < len(value["id"]) <= 200
                or value.get("server_id") != server_id or not when
                or not timedelta(0) <= now - when <= timedelta(seconds=OPPORTUNITY_MAX_AGE_SECONDS)
                or value.get("phase") != "one_minute_complete"
                or value.get("kind") not in {"boss", "fixed"}
                or (chapter is not None and (type(chapter) is not int or not 1 <= chapter <= 99))):
            return None
        return {"id": value["id"], "server_id": server_id, "at": encode_time(when),
                "phase": "one_minute_complete", "kind": value["kind"], "chapter": chapter,
                "absolute": value.get("absolute") is True, "major": value.get("major") is True,
                "star": value.get('star') is True, "boss_name": str(value.get('boss_name') or ''),
                "scheduled_at": scheduled.isoformat() if scheduled else None,
                "fixed_kind": value.get("fixed_kind") if value.get("fixed_kind") in {"valhalla", "world_boss"} else ""}
    except (ValueError, TypeError):
        return None


def encode_opportunity(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def requires_opportunity(event):
    if event.get('policy') == 'participation_slot':
        return event.get('trigger') != 'immediate'
    return event.get("trigger", "immediate") == "major_boss_after" or event.get("policy", "once") != "once"


def group_of(event):
    policy = event.get("policy", "once")
    if policy == 'participation_slot':
        return {'siege_noon': 'siege_noon', 'overnight_once': 'participation_dawn'}.get(
            event.get('tts_values', {}).get('_slot'), 'participation_evening')
    if policy in {"participation", "transfer_repeat"}:
        return policy  # shared across all targets/articles on this server
    source = event.get("source_key") or event["id"]
    return policy + ":" + source.split("/time-v1/", 1)[0]


def opportunity_allowed(event, context, ledger, now):
    """Only called after validity/enabled/revision/output checks by NoticeStore."""
    if not context:
        return False
    policy = event.get("policy", "once")
    normal_major = (context["kind"] == "boss" and
                    (context["absolute"] or (context["major"] and context["chapter"] not in {None, 2, 3, 4})))
    schedule_participation = policy == 'participation' and event.get('source_key', '').startswith('schedule/')
    if schedule_participation:
        from .notice_participation import event_priority
        normal_major = context['kind'] == 'boss' and (context['star'] or event_priority(context['boss_name']) < 6)
    fixed_major = context["kind"] == "fixed" and context["fixed_kind"] in {"valhalla", "world_boss"}
    if not (normal_major or fixed_major):
        return False
    if policy in {"transfer_repeat", "deadline_repeat"}:
        start, end = parse_time(event.get("event_from")), parse_time(event.get("event_until"))
        if not end or now >= end or (start and now < start) or (policy == "transfer_repeat" and not start):
            return False
        if policy == "deadline_repeat" and now < end - timedelta(hours=24):
            return False
    if policy == "participation":
        if schedule_participation:
            if not normal_major:
                return False
        target = parse_time(event.get("event_from"))
        if (not PARTICIPATION_START_HOUR <= now.hour < PARTICIPATION_END_HOUR
                or not target or target - now <= timedelta(minutes=30)):
            return False
    # No two notices consume the same boss announcement opportunity. The audio
    # host must serialize playback and recheck its token immediately before play.
    if any([row["opportunity_id"] == context["id"] for row in ledger]):
        return False
    group = group_of(event)
    previous = [row for row in ledger if row["group"] == group]
    if policy in {"participation", "deadline_repeat"}:
        today = [row for row in previous if parse_time(row["at"]).date() == now.date()]
        if len(today) >= LIMITED_DAILY_COUNT:
            return False
    gap = TRANSFER_GAP_MINUTES if policy == "transfer_repeat" else LIMITED_GAP_MINUTES
    if policy != "once" and previous and now - max(parse_time(row["at"]) for row in previous) < timedelta(minutes=gap):
        return False
    return True


def playback_description(event):
    """Earliest planned time/condition, not a promise of immediate audio output."""
    if (event.get('id', '').startswith('manual-') and event.get('category') == 'participation'
            and event.get('tts_template', '') in {'', 'participation.general'}):
        return '대상 선정용 · 1차/2차 알림 참조'
    try:
        beginning = parse_time(event.get('valid_from'))
        ending = parse_time(event.get('valid_until'))
        if not beginning or not ending:
            return '미정 · 유효기간 확인 필요'
        def label(at):
            return at.strftime('%m/%d %H:%M:%S' if at.second or at.microsecond else '%m/%d %H:%M')
        if event.get('policy') == 'participation_slot':
            plan = event.get('tts_values', {}).get('_slot_plan', {})
            at = parse_time(plan.get('at'))
            if not at:
                return '미정 · 재생 계획 확인 필요'
            if plan.get('kind') == 'timer':
                return f'{label(at)} 이후 · 대기열'
            if plan.get('kind') in {'world_boss', 'boss', 'dawn_boss'}:
                name = '월드보스' if plan['kind'] == 'world_boss' else plan.get('boss_name') or '선정 보스'
                return f'{label(at - timedelta(minutes=1))} · {name} 안내 후'
            return '미정 · 재생 계획 확인 필요'
        policy = event.get('policy', 'once')
        if policy == 'participation':
            return '18~23시 · 주요 보스 안내 후 (하루 최대 2회)'
        if policy == 'transfer_repeat':
            return '판매 중 · 주요 보스 안내 후 반복'
        if policy == 'deadline_repeat':
            return '마감 전날부터 · 주요 보스 안내 후 (하루 최대 2회)'
        if requires_opportunity(event):
            return f'{label(beginning)} 이후 · 주요 보스 안내 후 1회'
        return f'{label(beginning)} 이후 · 대기열 1회'
    except (ValueError, TypeError, AttributeError):
        return '미정 · 재생 계획 확인 필요'


def policy_description(event):
    policy = event.get("policy", "once")
    if policy == 'participation_slot':
        slot = event.get('tts_values', {}).get('_slot')
        return {'evening_first': '1차 · 18~19시 선택된 5챕 이상 보스 안내 뒤 / 없으면 18시 · 하루 1회',
                'evening_second': '2차 · 20시 월드보스 1분 전 안내 완료 후 · 하루 1회',
                'overnight_once': '22~24시 주요·연타임 보스 1분 전 안내 완료 후 / 해당 보스가 없으면 23시 대기열 · 별도 하루 1회',
                'siege_noon': '공성전 당일 12시 월드보스 1분 전 안내 완료 후 · 별도 1회'}.get(slot, '참여 독려 시간대 확인 필요')
    if policy == "transfer_repeat":
        return "판매 중 주요 보스 안내 직후 · 횟수 상한 없음 · 최소 30분 간격"
    if policy == "participation":
        prefix = "별 체크·지정 행사 안내 직후 · " if event.get('source_key', '').startswith('schedule/') else ""
        return prefix + "18~23시 · 모든 참여 독려 합계 하루 2회 · 최소 60분 간격 · 대상까지 30분 초과"
    if policy == "deadline_repeat":
        return "주요 보스 안내 직후 · 같은 공지 마감 안내 합계 하루 2회 · 최소 60분 간격"
    return "주요 보스 안내 직후 1회" if requires_opportunity(event) else "유효기간 내 1회"
