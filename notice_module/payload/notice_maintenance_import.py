"""One-shot, server-local application of confirmed maintenance schedules."""
from datetime import date, timedelta
from .notice_management import parse_time, encode_time
from .notice_text import compact


def regular_maintenance_candidate(state, now):
    """Keep the official date, including today's elapsed maintenance."""
    now = parse_time(now)
    articles, candidates = [], []
    for key, article in state.get('articles', {}).items():
        title = compact(article.get('title', ''))
        if (not article.get('pinned') or article.get('category') != 'maintenance'
                or '정기점검' not in title):
            continue
        windows = article.get('analysis', {}).get('windows', [])
        fact = windows[0] if len(windows) == 1 else {}
        try:
            start, end = parse_time(fact.get('start')), parse_time(fact.get('end'))
            seen = parse_time(article.get('first_seen')) if not article.get('published_date') else None
            published = (date.fromisoformat(article['published_date']) if article.get('published_date')
                         else seen.date() if seen else None)
        except (ValueError, TypeError):
            continue
        if published is None or published > now.date():
            continue
        valid = (fact.get('kind') == 'maintenance' and not fact.get('issue')
                 and not article.get('body_error') and '취소' not in title
                 and start and end and start < end < start + timedelta(days=7))
        row = dict(key=key, article=article, start=start, end=end, published=published, valid=bool(valid))
        articles.append(row)
        if (valid and start - timedelta(days=7) <= now
                and (now < end or start.date() == now.date())):
            candidates.append(row)
    if not candidates:
        return None
    ongoing = [row for row in candidates if row['start'] <= now < row['end']]
    today = [row for row in candidates if row['start'].date() == now.date()]
    day = (max(row['start'].date() for row in ongoing) if ongoing
           else now.date() if today else min(row['start'].date() for row in candidates))
    cycle = [row for row in candidates if row['start'].date() == day]
    if any(not row['valid'] and ((row['start'] and row['start'].date() == day)
            or (not row['start'] and row['published']
                and day - timedelta(days=1) <= row['published'] <= day)) for row in articles):
        return None
    if len({row['start'] for row in cycle}) != 1:
        return None  # Conflicting starts require review, never a guessed overwrite.
    return max(cycle, key=lambda row: row['end'])


def import_regular_maintenance(store, snapshot, apply):
    if (not callable(apply) or not snapshot or snapshot.get('server_id') != store.server_id
            or not snapshot.get('season')):
        return
    now = parse_time(store.clock())
    with store._transaction() as state:
        if not state['settings']['collection_enabled'] or not state['settings']['categories']['maintenance']:
            return
        candidate = regular_maintenance_candidate(state, now)
        if candidate is None:
            return
        at = encode_time(candidate['start'])
        # A new end/extension does not rewrite a manually adjusted start again.
        receipts = state.setdefault('regular_maintenance_imports', {})
        previous = [row for row in receipts.values() if row.get('season') == str(snapshot['season'])
                    and str(row.get('scheduled_at', ''))[:10] == at[:10]]
        if previous and previous[-1].get('scheduled_at') == at:
            return
        key = str(snapshot['season']) + '/' + at + '/' + str(len(receipts) + 1)
        request = dict(key=key, article_id=candidate['key'], server_id=store.server_id,
                       season=str(snapshot['season']), scheduled_at=at,
                       event_until=encode_time(candidate['end']), source_url=candidate['article'].get('url', ''),
                       status='pending', recorded_at=encode_time(now))
        receipts[key] = dict(request)
    try:
        result = apply(dict(request))
        status = result if result in {'applied', 'existing', 'conflict', 'deferred'} else 'failed'
    except Exception:
        status = 'failed'
    with store._transaction() as state:
        if status == 'deferred':
            state['regular_maintenance_imports'].pop(key, None)
        else:
            state['regular_maintenance_imports'][key]['status'] = status


def import_temporary_maintenance(store, snapshot, apply):
    if not callable(apply) or not snapshot or snapshot.get('server_id') != store.server_id:
        return
    now = parse_time(store.clock())
    with store._transaction() as state:
        if not state['settings']['collection_enabled'] or not state['settings']['categories']['maintenance']:
            return
        candidates = []
        for key, article in state.get('articles', {}).items():
            title = compact(article.get('title', ''))
            if (not article.get('pinned') or article.get('category') != 'maintenance' or article.get('body_error')
                    or '임시점검' not in title or any(word in title for word in ('연장', '완료', '취소'))):
                continue
            windows = article.get('analysis', {}).get('windows', [])
            if len(windows) != 1:
                continue
            fact = windows[0]
            try:
                start, end = parse_time(fact.get('start')), parse_time(fact.get('end'))
            except (ValueError, TypeError):
                continue
            if fact.get('kind') != 'maintenance' or fact.get('issue') or not start or not end or not now < start < end:
                continue
            candidates.append((start, key, article))
        receipts = state.setdefault('maintenance_imports', {})
        request = None
        for start, key, article in sorted(candidates):
            at = encode_time(start)
            if key in receipts or any(row.get('scheduled_at') == at for row in receipts.values()):
                continue
            request = dict(key=key, server_id=store.server_id, season=snapshot['season'], scheduled_at=at,
                           source_url=article.get('url', ''), status='pending', recorded_at=encode_time(now))
            receipts[key] = dict(request)
            break
    if request is None:
        return
    # Persist intent before touching the schedule. An interrupted pending import
    # requires manual review, never a blind retry that could undo a deletion.
    try:
        result = apply(dict(request))
        status = result if result in {'applied', 'existing', 'conflict', 'deferred'} else 'failed'
    except Exception:
        status = 'failed'
    with store._transaction() as state:
        if status == 'deferred':  # Host guarantees no mutation for this result.
            state['maintenance_imports'].pop(request['key'], None)
        else:
            state['maintenance_imports'][request['key']]['status'] = status
