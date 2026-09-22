"""One-shot, server-local import of confirmed temporary maintenance."""
import re
from .notice_management import parse_time, encode_time


def import_temporary_maintenance(store, snapshot, apply):
    if not callable(apply) or not snapshot or snapshot.get('server_id') != store.server_id:
        return
    now = parse_time(store.clock())
    with store._transaction() as state:
        if not state['settings']['collection_enabled'] or not state['settings']['categories']['maintenance']:
            return
        candidates = []
        for key, article in state.get('articles', {}).items():
            title = re.sub(r'\s+', '', article.get('title', ''))
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
