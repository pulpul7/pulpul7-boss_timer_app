"""One announcement per maintenance cycle after all enabled bosses are entered."""
from datetime import timedelta

from .notice_management import encode_time, parse_time
from .notice_text import compact

PREFIX = 'schedule-update/'
MESSAGE = '웹 서비스 스케쥴이 업데이트 되었습니다'


def _time(value):
    try:
        return parse_time(value)
    except (ValueError, TypeError):
        return None


def opening_cycle(snapshot, state, now):
    """Use regular/manual openings and confirmed maintenance of every kind.

    A maintenance start alone is not an opening. Extensions/completions of one
    maintenance retain the same identity, even when its opening time changes.
    """
    candidates, barriers = [], []
    opened = _time(snapshot.get('server_open'))
    start = _time(snapshot.get('maintenance_start'))
    if opened:
        candidates.append((start or opened, opened))
    if start:
        barriers.append(start)
    controls = sorted(snapshot.get('control_events', []), key=lambda row: row.get('scheduled_at', ''))
    for row in controls:
        at = _time(row.get('scheduled_at'))
        if not at:
            continue
        if row.get('control_type') == 'temporary_maintenance':
            barriers.append(at)

    # Public analyses already exclude struck-out/replaced ranges. Do not infer
    # an opening from the title or notice publication/fetch time.
    windows = {}
    for article in state.get('articles', {}).values():
        if article.get('category') != 'maintenance' or article.get('body_error'):
            continue
        title = compact(article.get('title', ''))
        for fact in article.get('analysis', {}).get('windows', []):
            if fact.get('kind') != 'maintenance':
                continue
            first, last = _time(fact.get('start')), _time(fact.get('end'))
            if not first:
                continue
            barriers.append(first)
            rank = 2 if '완료' in title else 1 if '연장' in title else 0
            valid = bool(last and first < last < first + timedelta(days=7)
                         and not fact.get('issue') and '취소' not in title)
            windows.setdefault(first, []).append((rank, last if valid else None))
    for first, records in windows.items():
        rank = max(row[0] for row in records)
        ends = {last for priority, last in records if priority == rank}
        if len(ends) == 1 and None not in ends:
            candidates.append((first, next(iter(ends))))
    for row in controls:
        at = _time(row.get('scheduled_at'))
        if row.get('control_type') == 'server_open' and at:
            before = [value for value in barriers if value <= at]
            candidates.append((max(before) if before else at, at))

    current = [(first, last) for first, last in candidates if first <= now]
    if not current:
        return None
    first = max([value[0] for value in current] + [value for value in barriers if value <= now])
    ends = [last for start, last in current if start == first]
    if not ends:
        return None  # A newer maintenance has started but its opening is unknown.
    opened = max(ends)
    # Invalid/conflicting latest analysis must not fall through to a local guess.
    if first in windows:
        rank = max(row[0] for row in windows[first])
        latest = {last for priority, last in windows[first] if priority == rank}
        if len(latest) != 1 or None in latest:
            return None
        opened = next(iter(latest))
    end = min([opened + timedelta(days=7)] +
              [value for value in barriers if value > first] +
              [value for value in [_time(snapshot.get('regular_maintenance'))]
               if value and value > first])
    return (first, opened, end) if first <= opened and now < end else None


def input_complete(snapshot, opened):
    expected = snapshot.get('input_boss_names')
    rows = snapshot.get('input_entries')
    if not isinstance(expected, list) or not expected or not isinstance(rows, list):
        return False  # Older hosts/empty catalogs cannot prove completed input.
    entered = {row.get('boss_name') for row in rows
               if not row.get('is_invasion') and row.get('state') not in {'control', 'deleted'}
               and (created := _time(row.get('created_at'))) is not None and created >= opened}
    return set(expected) <= entered


def synchronize_web_schedule_notice(store, snapshot, state):
    """Participate in the caller's transaction; no I/O to Discord or the web."""
    now = parse_time(store.clock())
    cycle = opening_cycle(snapshot, state, now)
    key = f"{PREFIX}{snapshot['season']}/{encode_time(cycle[0])}" if cycle else None
    ready = bool(cycle and cycle[1] <= now and input_complete(snapshot, cycle[1])
                 and snapshot.get('web_schedule_published') is True)
    changed = False
    for event in list(state['events'].values()):
        if not event['id'].startswith(PREFIX) or event.get('retired_at'):
            continue
        if key is not None and event['id'] != key:
            store._retire(state, event, '다음 점검 또는 서버 오픈 기준 변경', now)
            changed = True
        elif not ready and not event.get('analysis_hold'):
            event['analysis_hold'] = '서버 오픈 후 전체 스케쥴 입력 대기'
            event['revision'] += 1
            changed = True
    if not ready:
        return changed
    first, opened, end = cycle
    # Stable periods/text keep revisions unchanged on ordinary schedule edits.
    event = dict(id=key, source_key=key, category='maintenance', title=MESSAGE,
                 tts_text=MESSAGE, valid_from=encode_time(opened), valid_until=encode_time(end),
                 event_from=encode_time(first), event_until=encode_time(opened),
                 policy='once', trigger='immediate', retire_after_delivery=True,
                 analysis_evidence='점검 종류 무관 · 서버 오픈 이후 사용 보스 전체 입력 완료 · 1회 방송 후 폐기')
    # Zero-length event periods are unnecessary for an explicit opening without
    # a known maintenance start; validity still begins at the opening itself.
    if first == opened:
        event.pop('event_from')
        event.pop('event_until')
    return store.register(event, collected=True, _state=state) or changed
