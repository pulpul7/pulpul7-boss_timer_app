"""Confirmed notice facts -> one dated maintenance occurrence, on the host thread."""
from datetime import date, timedelta
import hashlib

from .notice_management import parse_time, encode_time
from .notice_text import compact


def transition_candidates(state, snapshot, now):
    now = parse_time(now)
    existing = {}
    for row in snapshot.get('control_events', []):
        kind = row.get('control_type')
        try:
            at = parse_time(row.get('scheduled_at'))
        except (ValueError, TypeError):
            continue
        if kind in {'regular_maintenance', 'temporary_maintenance'} and at:
            existing[(kind, at)] = row
    groups = {}
    blocked = set()
    for key, article in state.get('articles', {}).items():
        title = compact(article.get('title', ''))
        if article.get('category') != 'maintenance':
            continue
        kinds = [kind for text, kind in [('정기점검', 'regular_maintenance'), ('임시점검', 'temporary_maintenance')]
                 if text in title]
        if len(kinds) != 1:
            continue
        kind = kinds[0]
        windows = article.get('analysis', {}).get('windows', [])
        decisive = any(word in title for word in ('연장','완료','종료','취소'))
        if article.get('body_error') or len(windows) != 1 or any(row.get('issue') for row in windows):
            if decisive:
                try:
                    start = parse_time(windows[0].get('start')) if len(windows) == 1 else None
                    day = start.date() if start else date.fromisoformat(article.get('published_date', ''))
                    blocked.update((kind, day+timedelta(days=offset)) for offset in ([0] if start else [-1,0,1]))
                except (ValueError, TypeError):
                    blocked.update((kind, at.date()) for candidate_kind, at in existing if candidate_kind == kind)
            continue
        fact = windows[0]
        try:
            start, end = parse_time(fact.get('start')), parse_time(fact.get('end'))
            published = article.get('published_date')
            seen = parse_time(article.get('first_seen'))
            day = date.fromisoformat(published) if published else seen.date() if seen else None
            if day is None or day > now.date():
                continue
        except (ValueError, TypeError):
            continue
        if (fact.get('kind') != 'maintenance' or fact.get('issue') or not start or not end
                or not start < end < start+timedelta(days=7) or not now-timedelta(days=7) <= start <= now+timedelta(days=7)):
            continue
        action = ('cancel' if '취소' in title else 'complete' if any(word in title for word in ('완료', '종료'))
                  else 'plan' if '연장' in title else 'start')
        rank = {'start':0, 'plan':1, 'complete':2, 'cancel':3}[action]
        groups.setdefault((kind, start), []).append(dict(action=action, rank=rank, end=end, source_id=key,
                                                        pinned=article.get('pinned', False)))
    result = []
    for (kind, start), rows in groups.items():
        if (kind, start.date()) in blocked:
            continue
        if {'complete', 'cancel'} <= {row['action'] for row in rows}:
            continue  # Mutually exclusive outcomes are never resolved by title priority.
        priority = max(row['rank'] for row in rows)
        selected = [row for row in rows if row['rank'] == priority]
        if len({row['end'] for row in selected}) != 1:
            continue  # Contradictory completion/extension times require review.
        fact = selected[0]
        known = existing.get((kind, start))
        action = fact['action']
        if known is None:
            if action in {'complete','cancel'}:
                if kind != 'regular_maintenance' or (action == 'complete' and fact['end'] > now):
                    continue
                result.append(dict(kind=kind, action='start', scheduled_at=encode_time(start),
                                   event_until=encode_time(fact['end']), source_id=fact['source_id']))
            elif not any(row['pinned'] for row in selected):
                continue
            elif action == 'plan':
                action = 'start'
            if kind == 'temporary_maintenance' and start <= now:
                continue  # Do not invent an already-elapsed start from an old article.
        else:
            if known.get('cancelled_at') or known.get('reopened_at'):
                continue
            if action == 'start':
                action = 'plan'
            if action == 'plan' and known.get('planned_end_at') == encode_time(fact['end']):
                continue
        if action == 'complete' and fact['end'] > now:
            continue  # Planned opening is not a confirmed completion.
        result.append(dict(kind=kind, action=action, scheduled_at=encode_time(start),
                           event_until=encode_time(fact['end']), source_id=fact['source_id']))
    return sorted(result, key=lambda row:(row['scheduled_at'], row['kind']))


def import_transitions(store, snapshot, apply):
    if (not callable(apply) or not snapshot or snapshot.get('server_id') != store.server_id
            or not snapshot.get('season')):
        return
    now = parse_time(store.clock())
    with store._transaction() as state:
        if not state['settings']['collection_enabled'] or not state['settings']['categories']['maintenance']:
            return
        candidates = transition_candidates(state, snapshot, now)
        receipts = state.setdefault('maintenance_lifecycle_imports', {})
        cutoff = encode_time(now-timedelta(days=30))
        for key in list(receipts):
            if receipts[key].get('recorded_at', '') < cutoff:
                receipts.pop(key)
        requests = []
        for candidate in candidates:
            identity = '|'.join(candidate[key] for key in ('kind','action','scheduled_at','event_until'))
            key = str(snapshot['season'])+'/'+hashlib.sha256(identity.encode()).hexdigest()
            if key in receipts or len(receipts) >= 512:
                continue
            request = dict(candidate, key=key, server_id=store.server_id, season=str(snapshot['season']))
            receipts[key] = dict(request, status='pending', recorded_at=encode_time(now))
            requests.append(request)
    # Persist intent before calling GUI. Never infer success after interruption.
    for request in requests:
        try:
            result = apply(dict(request))
            status = result if result in {'applied','existing','conflict','deferred'} else 'failed'
        except Exception:
            status = 'failed'
        with store._transaction() as state:
            receipts = state.setdefault('maintenance_lifecycle_imports', {})
            if status == 'deferred':
                receipts.pop(request['key'], None)
            elif request['key'] in receipts:
                receipts[request['key']]['status'] = status
