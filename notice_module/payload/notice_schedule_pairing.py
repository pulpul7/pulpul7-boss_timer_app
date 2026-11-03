"""Pair output of independent dawn/morning notices; never merge their targets."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json

from .notice_management import encode_time, parse_time


def _signature(event):
    values = {key: value for key, value in event.get('tts_values', {}).items()
              if key not in {'_paired_dawn', '_morning_pair'}}
    fields = {key: event.get(key) for key in ('id', 'tts_template', 'valid_from', 'valid_until',
                                             'event_from', 'event_until', 'policy', 'trigger')}
    for key in ('valid_from', 'valid_until', 'event_from', 'event_until'):
        fields[key] = encode_time(fields[key])
    fields['tts_values'] = values
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _available(state, event, now, *, future=False):
    from .notice_templates import event_template_enabled, template_text, TEMPLATES
    if (not event or not event.get('enabled', True) or event.get('retired_at') or event.get('analysis_hold')
            or event.get('manual_tts') or event.get('manual_period') or event.get('tts_review_required')
            or event.get('last_delivery') or not event_template_enabled(state, event)):
        return False
    key = event.get('tts_template')
    if key not in TEMPLATES or template_text(state, key) != TEMPLATES[key]['text']:
        return False  # Preserve explicitly edited speech as separate notices.
    first, last = parse_time(event.get('valid_from')), parse_time(event.get('valid_until'))
    return bool(first and last and now < last and (future or first <= now))


def _morning_quota(state, now):
    from .notice_opportunities import LIMITED_DAILY_COUNT, LIMITED_GAP_MINUTES
    previous = [row for row in state.get('delivery_ledger', []) if row.get('group') == 'participation']
    today = [row for row in previous if parse_time(row['at']).date() == now.date()]
    return (len(today) < LIMITED_DAILY_COUNT and
            (not previous or now - max(parse_time(row['at']) for row in previous) >= timedelta(minutes=LIMITED_GAP_MINUTES)))


def pair_candidates(candidates, state, now):
    dawn = next((event for event in candidates if event.get('tts_template') == 'participation.dawn'), None)
    morning = next((event for event in candidates if event.get('tts_template') == 'participation.morning'), None)
    if not dawn or not morning or not _morning_quota(state, now):
        return candidates
    for candidate in (dawn, morning):
        previous = state['events'].get(candidate['id'])
        if not _available(state, previous or candidate, now, future=True):
            return candidates
    # A 23:00 dawn fallback may carry the morning clause through its two-minute
    # output window; standalone morning opportunities still stop at 23:00.
    morning['valid_until'] = max((morning['valid_until'], dawn['valid_until']), key=parse_time)
    morning['tts_values']['_paired_dawn'] = dawn['id']
    dawn['tts_values']['_morning_pair'] = dict(id=morning['id'], signature=_signature(morning),
                                             values=deepcopy(morning['tts_values']))
    return candidates


def paired_morning(state, dawn, now):
    pair = (dawn or {}).get('tts_values', {}).get('_morning_pair')
    if not pair or not _available(state, dawn, now, future=True) or not _morning_quota(state, now):
        return None
    morning = state['events'].get(pair['id'])
    if (not _available(state, morning, now, future=True)
            or morning.get('tts_values', {}).get('_paired_dawn') != dawn['id']
            or _signature(morning) != pair['signature']):
        return None
    return morning


def held_for_dawn(state, morning, now):
    parent = morning.get('tts_values', {}).get('_paired_dawn')
    return bool(parent and paired_morning(state, state['events'].get(parent), now))
