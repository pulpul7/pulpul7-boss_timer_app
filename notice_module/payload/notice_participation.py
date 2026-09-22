"""Important targets and fixed daily participation slots (no audio side effects)."""
import re
from datetime import timedelta
from .notice_management import parse_time, encode_time

SPECIAL_EVENTS = (('공성전',), ('트리니트리그', '트리니티리그'), ('방어전',), ('점령전',),
                  ('지옥성체', '지옥성채'), ('길던', '길드던전'))
CHAPTERS = {'요툰하임': 2, '니다벨리르': 3, '알브하임': 4, '무스펠하임': 5,
            '아스가르드': 6, '니플하임': 7, '바나하임': 8}
CHAIN_GAP_SECONDS = 60  # Same adjacent gap as the host's rapid-chain grouping.


def event_priority(name):
    compact = re.sub(r'[\s.·]', '', str(name))
    for rank, names in enumerate(SPECIAL_EVENTS):
        if compact in names:
            return rank
    return len(SPECIAL_EVENTS)


def important(item):
    return item.get('star') is True or event_priority(item.get('boss_name')) < len(SPECIAL_EVENTS)


def chapter(item):
    value = item.get('chapter') or CHAPTERS.get(item.get('area'))
    if type(value) is int:
        return value
    match = re.fullmatch(r'(\d+)챕(?:터)?', str(item.get('area', '')))
    return int(match[1]) if match else 0


def eligible(item):
    return (item.get('precision') in {'minute', 'second'} and not item.get('is_invasion')
            and not item.get('cut_applied') and item.get('state') not in {'elapsed', 'control', 'deleted'})


def manual_targets(snapshot, state, now):
    names = set(snapshot.get('important_names', []))
    result = []
    for item in state['events'].values():
        if (not item['id'].startswith('manual-') or item.get('category') != 'participation'
                or item.get('retired_at') or not item.get('enabled') or item.get('analysis_hold')
                or item.get('tts_review_required') or not item.get('valid_from') or not item.get('valid_until')
                or parse_time(item['valid_until']) <= now or not item.get('event_from')):
            continue
        result.append(dict(boss_name=item['title'], display_name=item['title'],
                           scheduled_at=item['event_from'], precision='minute', star=item['title'] in names,
                           manual_source=item['id'], manual_revision=item['revision'],
                           manual_text=item['tts_text'] if not item.get('tts_template') or item.get('manual_tts') else ''))
    return result


def first_opportunity(rows, day):
    beginning, ending = day.replace(hour=18), day.replace(hour=19)
    bosses = sorted([row for row in rows if eligible(row) and chapter(row) >= 5 and not row.get('fixed')
                     and beginning <= parse_time(row['scheduled_at']) - timedelta(minutes=1) < ending],
                    key=lambda row: (parse_time(row['scheduled_at']), row['boss_name']))
    bosses = list({(row['boss_name'], parse_time(row['scheduled_at'])): row for row in bosses}.values())
    if not bosses:
        return dict(kind='timer', at=encode_time(beginning))
    chained = [row for index, row in enumerate(bosses[:-1])
               if 0 <= (parse_time(bosses[index+1]['scheduled_at']) - parse_time(row['scheduled_at'])).total_seconds() <= CHAIN_GAP_SECONDS]
    chosen = min(chained or bosses, key=lambda row: (-chapter(row), parse_time(row['scheduled_at']), row['boss_name']))
    return dict(kind='boss', boss_name=chosen['boss_name'], at=chosen['scheduled_at'])


def dawn_opportunity(rows, now):
    """One upcoming 22:00~24:00 boss/chain, otherwise the 23:00 queue."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day + timedelta(days=1)
    bosses = sorted([row for row in rows if eligible(row)
                     and day.replace(hour=22) <= parse_time(row['scheduled_at']) <= end],
                    key=lambda row: (parse_time(row['scheduled_at']), row['boss_name']))
    bosses = list({(row['boss_name'], parse_time(row['scheduled_at'])): row for row in bosses}.values())
    chains = [row for row in bosses if chapter(row) >= 5 and not row.get('fixed')]
    chained = {(row['boss_name'], row['scheduled_at']) for index, row in enumerate(chains[:-1])
               if 0 <= (parse_time(chains[index + 1]['scheduled_at']) - parse_time(row['scheduled_at'])).total_seconds() <= CHAIN_GAP_SECONDS}
    candidates = [row for row in bosses if important(row) or (row['boss_name'], row['scheduled_at']) in chained]
    # An existing but missed boss slot must not turn into an unrelated 23:00 timer.
    if candidates:
        future = [row for row in candidates if now < min(end, parse_time(row['scheduled_at']) + timedelta(minutes=2))]
        if not future:
            return None
        chosen = future[0]
        return dict(kind='dawn_boss', at=chosen['scheduled_at'], boss_name=chosen['boss_name'],
                    boss_kind='fixed' if chosen.get('fixed') else 'boss')
    return dict(kind='timer', at=encode_time(day.replace(hour=23)))


def evening_notices(snapshot, state, now):
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day + timedelta(days=1)
    rows = list(snapshot.get('events', [])) + manual_targets(snapshot, state, now)
    targets = [row for row in rows if eligible(row) and important(row)
               and day.replace(hour=21) <= parse_time(row['scheduled_at']) <= end]
    # Deduplicate ordinary/fixed/manual copies of one occurrence.
    unique = {}
    for row in targets:
        unique[(row['boss_name'], parse_time(row['scheduled_at']))] = row
    targets = sorted(unique.values(), key=lambda row: (event_priority(row['boss_name']), parse_time(row['scheduled_at']), row['boss_name']))
    sieges = [row for row in rows if eligible(row) and event_priority(row.get('boss_name')) == 0
              and day <= parse_time(row['scheduled_at']) < end and parse_time(row['scheduled_at']) > now]
    future = [row for row in targets if parse_time(row['scheduled_at']) > now]
    results = []
    prefix = f"schedule/{snapshot['season']}/{day.date().isoformat()}"
    if future:
        target = future[0]
        at = parse_time(target['scheduled_at'])
        name = target.get('display_name') or target['boss_name']
        other = ' 외 주요보스들이' if len(future) > 1 else ('이' if 0xAC00 <= ord(name[-1]) <= 0xD7A3 and (ord(name[-1])-0xAC00) % 28 else '가')
        values = {'알림제목': name, '시작시간': at.strftime('%m월 %d일 %H시 %M분'), '_start_date': at.date().isoformat(),
                  '_participation_name': name + other, '_manual_participation': target.get('manual_text', ''),
                  '_manual_source': target.get('manual_source', ''), '_manual_revision': target.get('manual_revision'),
                  '_midnight': at == end}
        plans = [('evening_first', first_opportunity(rows, day)),
                 ('evening_second', dict(kind='world_boss', at=encode_time(day.replace(hour=20))))]
        for slot, plan in plans:
            planned_at = parse_time(plan['at'])
            start = planned_at if plan['kind'] == 'timer' else planned_at - timedelta(minutes=1)
            until = min(day.replace(hour=19), planned_at + timedelta(minutes=2)) if slot == 'evening_first' else planned_at + timedelta(minutes=2)
            if now >= until:
                continue
            results.append(dict(id=prefix + '/' + slot, source_key=prefix + '/' + slot,
                category='participation', title='저녁 참여 독려 · ' + ('1차' if slot == 'evening_first' else '2차'),
                tts_template='participation.general', tts_values=dict(values, _slot=slot, _slot_plan=plan),
                valid_from=encode_time(start), valid_until=encode_time(until), event_from=encode_time(at),
                event_until=encode_time(end + timedelta(seconds=1)), policy='participation_slot',
                trigger='immediate' if plan['kind'] == 'timer' else 'major_boss_after',
                analysis_evidence='별 체크 / 행사 우선순위 · 1차 18~19시, 2차 20시 월드보스 안내 완료 후',
                body='\n'.join(f"{row['scheduled_at']} {row['boss_name']}" for row in targets)))
    if sieges and now < day.replace(hour=12, minute=2):
        results.append(dict(id=prefix + '/siege_noon', source_key=prefix + '/siege_noon', category='participation',
            title='공성전 당일 정오 참여 독려', tts_template='participation.siege_noon',
            tts_values={'_slot': 'siege_noon', '_slot_plan': {'kind': 'world_boss', 'at': encode_time(day.replace(hour=12))}},
            valid_from=encode_time(day.replace(hour=11, minute=59)), valid_until=encode_time(day.replace(hour=12, minute=2)),
            event_from=encode_time(min(parse_time(row['scheduled_at']) for row in sieges)), event_until=encode_time(end),
            policy='participation_slot', trigger='major_boss_after'))
    return results


def slot_allowed(event, context, ledger, now):
    values = event.get('tts_values', {})
    slot, plan = values.get('_slot'), values.get('_slot_plan', {})
    today = [row for row in ledger if parse_time(row['at']).date() == now.date()]
    if slot not in {'evening_first', 'evening_second', 'siege_noon', 'overnight_once'} or any(row.get('slot') == slot for row in today):
        return False
    if slot == 'overnight_once' and any(row.get('event_id') == event['id'] for row in today):
        return False  # Keep completions recorded under the previous shared policy.
    if slot in {'evening_first', 'evening_second'} and sum(row['group'] == 'participation_evening' for row in today) >= 2:
        return False
    at = parse_time(plan.get('at'))
    plan_day = (at - timedelta(minutes=1)).date() if at and plan.get('kind') == 'dawn_boss' else at.date() if at else None
    if not at or plan_day != now.date():
        return False
    if plan.get('kind') == 'timer':
        return slot in {'evening_first', 'overnight_once'} and at <= now < at + timedelta(minutes=2)
    if not context or parse_time(context.get('scheduled_at')) != at:
        return False
    if any(row['opportunity_id'] == context['id'] for row in ledger):
        return False
    if plan.get('kind') == 'world_boss':
        return context['kind'] == 'fixed' and context['fixed_kind'] == 'world_boss'
    if plan.get('kind') == 'dawn_boss':
        return context['kind'] == plan.get('boss_kind') and context.get('boss_name') == plan.get('boss_name')
    return (context['kind'] == 'boss' and context.get('boss_name') == plan.get('boss_name')
            and (context.get('chapter') or 0) >= 5)
