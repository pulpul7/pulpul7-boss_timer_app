"""Read-only regular-maintenance facts for the schedule input default.

No collection, schedule writes, speech or global default changes here.
"""
import re
from datetime import date, timedelta

from .notice_management import parse_time


def server_open_override(state, now, reference=None, *, include_upcoming=False):
    now = parse_time(now)
    reference = parse_time(reference) if reference is not None else now
    records = []
    for article in state.get('articles', {}).values():
        title = re.sub(r'\s+', '', article.get('title', ''))
        if article.get('category') != 'maintenance' or '정기점검' not in title:
            continue
        try:
            published = date.fromisoformat(article.get('published_date', ''))
        except (ValueError, TypeError):
            continue
        if published > now.date():
            continue
        windows = article.get('analysis', {}).get('windows', [])
        fact = windows[0] if len(windows) == 1 else {}
        try:
            start, end = parse_time(fact.get('start')), parse_time(fact.get('end'))
        except (ValueError, TypeError):
            start = end = None
        valid = (fact.get('kind') == 'maintenance' and not fact.get('issue')
                 and not article.get('body_error') and start and end and start < end < start + timedelta(days=7))
        records.append(dict(article=article, title=title, published=published,
                            start=start, end=end, valid=bool(valid)))

    # A future notice must not replace last week's opening time on publication.
    current = [row for row in records if row['start'] and row['start'] <= now < row['start'] + timedelta(days=7)]
    if not current:
        return None
    day = max(row['start'].date() for row in current)
    cycle = [row for row in current if row['start'].date() == day]
    # Unreadable extension/completion on this maintenance day cannot be ignored
    # in favour of the earlier planned end. Fall back instead of guessing.
    if any(not row['valid']
           and any(word in row['title'] for word in ('연장', '완료', '취소'))
           and ((row['start'] and row['start'].date() == day)
                or (not row['start'] and row['published'] == day)) for row in records):
        return None
    if any(not row['valid'] or '취소' in row['title'] for row in cycle):
        return None
    # An explicit completion outranks an extension, which outranks the original
    # plan. Conflicting notices of the same kind are not resolved by fetch time.
    rank = lambda row: 2 if '완료' in row['title'] else 1 if '연장' in row['title'] else 0
    priority = max(rank(row) for row in cycle)
    selected = [row for row in cycle if rank(row) == priority]
    ends = {row['end'] for row in selected}
    if len(ends) != 1:
        return None
    end = next(iter(ends))
    expires = min(row['start'] for row in cycle) + timedelta(days=7)
    earliest = min(row['start'] for row in cycle) if include_upcoming else end
    if not (earliest <= now < expires and earliest <= reference < expires):
        return None
    source = selected[0]['article']
    return dict(server_open=end.isoformat(), maintenance_start=min(row['start'] for row in cycle).isoformat(),
                valid_until=expires.isoformat(), source_id=source.get('id', ''), source_url=source.get('url', ''))


def server_open_alarm(state, now):
    """Read a current maintenance's planned opening without changing schedules."""
    settings = state.get('settings', {})
    if (not settings.get('collection_enabled', True)
            or not settings.get('categories', {}).get('maintenance', True)):
        return None
    return server_open_override(state, now, include_upcoming=True)
