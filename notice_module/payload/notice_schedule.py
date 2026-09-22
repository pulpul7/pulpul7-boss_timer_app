"""Read-only schedule projection into editable, expiring notice events.

No game schedule mutation, speech, Discord or network access belongs here.
"""
from datetime import datetime, time, timedelta
from copy import deepcopy

from .notice_management import KST, parse_time, encode_time
from .notice_participation import important, evening_notices, dawn_opportunity

CHAPTERS = {'요툰하임': 2, '니다벨리르': 3, '알브하임': 4, '무스펠하임': 5,
            '아스가르드': 6, '니플하임': 7, '바나하임': 8}
DEPTH_NAMES = {'최하층강글': '최하층 강글', '최하층굴베': '최하층 굴베', '최하층스네르': '최하층 스네르'}


def maintenance_start(snapshot, state, tomorrow):
    """Confirmed current regular-maintenance notice overrides the local default."""
    matches = []
    for event in state['events'].values():
        if (event.get('category') != 'maintenance' or event.get('retired_at')
                or not event.get('enabled') or event.get('analysis_hold') or event.get('tts_review_required')
                or '정기' not in event.get('title', '') or '연장' in event.get('title', '')):
            continue
        start, end = parse_time(event.get('event_from')), parse_time(event.get('event_until'))
        if start and end and start < end and start.date() == tomorrow:
            matches.append((parse_time(event['updated_at']), start))
    if matches:
        latest = max(updated for updated, _ in matches)
        starts = {start for updated, start in matches if updated == latest}
        return next(iter(starts)) if len(starts) == 1 else None
    start = parse_time(snapshot.get('regular_maintenance'))
    return start if start and start.date() == tomorrow else None


def schedule_notices(snapshot, state, now):
    now = parse_time(now)
    day = now.date()
    midnight = datetime.combine(day + timedelta(days=1), time(), KST)
    evening = midnight - timedelta(hours=3)
    valid_from = midnight - timedelta(hours=6)  # 18:00
    valid_until = midnight - timedelta(hours=1)  # Existing 23:00 quiet policy.
    cutoff = maintenance_start(snapshot, state, midnight.date()) if day.weekday() == 1 else None
    groups = {'evening': [], 'overnight': [], 'morning': []}
    seen = set()
    for item in snapshot.get('events', []):
        when = parse_time(item.get('scheduled_at'))
        if (not when or when <= now + timedelta(minutes=30)
                or item.get('precision') not in {'minute', 'second'}
                or item.get('state') in {'elapsed', 'control', 'deleted'} or item.get('cut_applied')
                or item.get('is_invasion')):
            continue
        name = str(item.get('boss_name') or '').strip()
        chapter = CHAPTERS.get(item.get('area'))
        major = important(item)
        compact = ''.join(name.split())
        overnight = major
        period = None
        if overnight and midnight + timedelta(seconds=1) <= when < midnight + timedelta(hours=6):
            group, period = 'overnight', '새벽'
        elif (overnight and cutoff and midnight.date().weekday() == 2
              and max(midnight, cutoff - timedelta(hours=2)) <= when < cutoff):
            group, period = 'morning', '아침'
        if period is None or not name or (cutoff and when.date() == cutoff.date() and when >= cutoff):
            continue
        identity = (name, when)
        if identity in seen:
            continue
        seen.add(identity)
        groups[group].append({'name': DEPTH_NAMES.get(compact, item.get('display_name') or name),
                              'at': when.isoformat(), 'period': period})
    result = evening_notices(snapshot, state, now)
    for group, entries in groups.items():
        if not entries:
            continue
        entries.sort(key=lambda entry: (entry['at'], entry['name']))
        start, end = parse_time(entries[0]['at']), parse_time(entries[-1]['at'])
        rule = {'evening': 'general', 'overnight': 'dawn', 'morning': 'morning'}[group]
        slot_values = {}
        event_valid_from, event_valid_until = valid_from, valid_until
        policy, trigger = 'participation', 'major_boss_after'
        if group == 'overnight':
            plan = dawn_opportunity(snapshot.get('events', []), now)
            if plan is None:
                continue
            planned_at = parse_time(plan['at'])
            event_valid_from = planned_at if plan['kind'] == 'timer' else planned_at - timedelta(minutes=1)
            event_valid_until = min(midnight, planned_at + timedelta(minutes=2))
            policy = 'participation_slot'
            trigger = 'immediate' if plan['kind'] == 'timer' else 'major_boss_after'
            slot_values = dict(_slot='overnight_once', _slot_plan=plan)
        if now >= event_valid_until:
            continue
        key = f"schedule/{snapshot['season']}/{day.isoformat()}/{group}"
        result.append(dict(id=key, source_key=key, category='participation',
            title={'evening': '저녁 주요 보스 참여 독려', 'overnight': '다음 날 새벽 보스 안내',
                   'morning': '점검 전 아침 보스 안내'}[group],
            tts_template='participation.' + rule,
            tts_values={'알림제목': ', '.join(e['name'] for e in entries), '_boss_entries': entries,
                        '시작시간': start.strftime('%m월 %d일 %H시 %M분'), '_start_date': start.date().isoformat(), **slot_values},
            valid_from=encode_time(event_valid_from), valid_until=encode_time(event_valid_until),
            event_from=start.isoformat(), event_until=(end + timedelta(seconds=1)).isoformat(),
            policy=policy, trigger=trigger,
            body='\n'.join(f"{e['at']} {e['name']} ({e['period'] or '저녁'})" for e in entries),
            analysis_evidence='스케줄 분/초 확정 · 저녁 21:00~24:00 / 새벽 00:00:01~05:59:59 · '
                              + (f'정기점검 {cutoff.isoformat()} 직전 2시간' if cutoff else '점검 전 추가 구간 없음')
                              + (' · 새벽 안내 별도 하루 1회: 22~24시 주요·연타임 보스 안내 후 / 없으면 23시 대기열'
                                 if group == 'overnight' else '')))
    return result


def recover_wrongly_unpinned_schedule(state, candidate, now):
    """Repair only the known source-classification bug, never manual retirement.

    Called only for a freshly computed candidate of this server/season/day.
    Older records lost their enabled flag at retirement; the authorized repair
    restores their default enabled state. Recorded manual disables are retained.
    """
    key = candidate['id']
    old = state['events'].get(key)
    if (not old or not key.startswith('schedule/') or old.get('source_key') != key
            or candidate.get('source_key') != key or old.get('category') != 'participation'
            or not old.get('retired_at') or old.get('retired_reason') != '공지 해제'):
        return False
    now = parse_time(now)
    for value in (old.get('valid_until'), candidate.get('valid_until')):
        end = parse_time(value)
        if not end or end <= now:
            return False  # Never restore expired original or replacement windows.
    old['source_recovery'] = dict(reason='스케줄을 공지로 오인한 자동 폐기 복구',
                                  retired_at=old['retired_at'], recovered_at=encode_time(now))
    old.pop('retired_at', None)
    old.pop('retired_reason', None)
    old['enabled'] = bool(old.pop('enabled_before_retirement', True))
    old['analysis_hold'] = '복구 후 최신 스케줄 재검토'  # Force register() to update the projection.
    state['suppressed'].pop(key, None)
    state['generation'] += 1  # Invalidate any previously issued audio token.
    return True


def synchronize_schedule(store, snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('server_id') != store.server_id:
        return False
    if not isinstance(snapshot.get('events'), list) or not snapshot.get('season'):
        return False
    with store._transaction() as state:
        if not state['settings']['collection_enabled'] or not state['settings']['categories']['participation']:
            return False
        wanted = schedule_notices(snapshot, state, store.clock())
        keys = {event['id'] for event in wanted}
        changed = False
        # Capture before updating the dawn row, which removes its morning entries.
        legacy_groups = {key: deepcopy(item) for key, item in state['events'].items()
                         if key.endswith('/overnight') and any(row.get('period') == '아침'
                             for row in item.get('tts_values', {}).get('_boss_entries', []))}
        for event in wanted:
            if '/evening_' in event['id'] and event['id'] not in state['events']:
                old_key = event['id'].rsplit('/', 1)[0] + '/evening'
                if old_key in state['suppressed']:
                    state['suppressed'][event['source_key']] = True
                elif old_key in state['events']:
                    inherited = deepcopy(state['events'][old_key])
                    inherited.update(id=event['id'], source_key=event['source_key'])
                    state['events'][event['id']] = inherited
            if event['id'].endswith('/morning') and event['id'] not in state['events']:
                # Older versions combined dawn and morning. Preserve manual
                # controls on the first split; never revive a discarded group.
                old_key = event['id'].rsplit('/', 1)[0] + '/overnight'
                old = legacy_groups.get(old_key)
                if old_key in state['suppressed']:
                    state['suppressed'][event['source_key']] = True
                elif old and any(row.get('period') == '아침' for row in old.get('tts_values', {}).get('_boss_entries', [])):
                    inherited = deepcopy(old)
                    inherited.update(id=event['id'], source_key=event['source_key'])
                    state['events'][event['id']] = inherited
            changed |= recover_wrongly_unpinned_schedule(state, event, store.clock())
            changed |= store.register(event, collected=True, _state=state)
        for event in state['events'].values():
            if (event['source_key'].startswith('schedule/') and event['id'] not in keys
                    and not event.get('retired_at') and not event.get('analysis_hold')):
                event['analysis_hold'] = '스케줄 대상 변경/삭제 · 자동 안내 보류'
                event['revision'] += 1
                changed = True
        return changed
