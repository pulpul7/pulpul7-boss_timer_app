"""Class-aware speech for all remaining major targets, without time slicing."""
from datetime import timedelta

from .notice_management import parse_time, encode_time
from .notice_participation import grouped_targets


def classified_targets(rows):
    """Shared singleton/combined-class rules for remaining and dawn speech."""
    return grouped_targets(rows)


def remaining_message(rows):
    groups = classified_targets(rows)
    if not groups:
        return ''
    names = [group['label'] for group in groups]
    listing = ', '.join(names)
    return f'오늘 남은 주요보스는 {listing}입니다.'


def third_notices(snapshot, state, now):
    """A completed first/second announcement supplies the immutable anchor.

    Retain yesterday's midnight anchor across the calendar rollover. Never pick
    the next future target again after the previously announced target starts.
    """
    from .notice_participation import eligible, important, manual_targets
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    results = []
    for cycle_day in (day - timedelta(days=1), day):
        prefix = f"schedule/{snapshot['season']}/{cycle_day.date().isoformat()}"
        announced = []
        for receipt in state.get('delivery_ledger', []):
            if (receipt.get('slot') not in {'evening_first', 'evening_second'}
                    or receipt.get('event_id') not in {prefix + '/evening_first', prefix + '/evening_second'}):
                continue
            source = state['events'].get(receipt['event_id'], {})
            representative = receipt.get('representative') or source.get('tts_values', {}).get('_representative')
            if not representative:
                # Old completion ledgers can use the retained source's first
                # entry only when it matches the source's representative time.
                at = source.get('event_from')
                representative = next((row for row in source.get('tts_values', {}).get('_boss_entries', [])
                                       if parse_time(row.get('at')) == parse_time(at)), None)
            if representative:
                at = parse_time(representative.get('scheduled_at') or representative.get('at'))
                done = parse_time(receipt.get('at'))
                if at and done and cycle_day.replace(hour=21) <= at <= cycle_day + timedelta(days=1):
                    announced.append((done, at))
        if not announced:
            continue
        _, anchor = max(announced)
        until = anchor + timedelta(minutes=2)
        if now >= until:
            continue  # Do not replay a missed start after a late program launch.
        search_end = cycle_day + timedelta(days=1, hours=1, minutes=30, seconds=1)
        entries = []
        seen = set()
        rows = list(snapshot.get('events', [])) + manual_targets(snapshot, state, now)
        for row in rows:
            at = parse_time(row.get('scheduled_at'))
            if not at or not max(anchor, now) < at < search_end or not eligible(row) or not important(row):
                continue
            identity = (row.get('boss_name'), at)
            if identity in seen:
                continue
            seen.add(identity)
            entries.append({key: row[key] for key in ('boss_name', 'display_name', 'scheduled_at', 'area',
                           'chapter', 'absolute', 'fixed') if key in row})
        if not entries:
            continue
        entries.sort(key=lambda row: parse_time(row['scheduled_at']))
        key = prefix + '/evening_third'
        results.append(dict(id=key, source_key=key, category='participation',
            title='저녁 참여 독려 · 3차 (남은 주요보스)', tts_template='participation.remaining',
            tts_values={'_slot': 'evening_third', '_cycle_day': cycle_day.date().isoformat(),
                        '_slot_plan': dict(kind='timer', at=encode_time(anchor)), '_remaining_entries': entries},
            valid_from=encode_time(anchor), valid_until=encode_time(until),
            event_from=entries[0]['scheduled_at'],
            event_until=encode_time(parse_time(entries[-1]['scheduled_at']) + timedelta(seconds=1)),
            policy='participation_slot', trigger='immediate', retire_after_delivery=True,
            body='\n'.join(f"{row['scheduled_at']} {row['boss_name']}" for row in entries),
            analysis_evidence='1·2차 실제 안내 대표 일정 시작 직후 1회 · 다음 날 01:30까지 · 분류별 요약'))
    return results
