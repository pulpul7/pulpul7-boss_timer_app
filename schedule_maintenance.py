"""Pure maintenance occurrence policy shared by desktop, web and Discord.

Existing control dictionaries remain the storage contract. Optional closure
fields never expire or delete history merely because the clock advanced.
"""
from datetime import datetime, timedelta
import hashlib
import re

MAINTENANCE_KINDS = frozenset({'regular_maintenance', 'temporary_maintenance'})


def control_kind(row):
    kind = str(row.get('control_type') or '')
    if kind == 'temporary_maintenance' and re.sub(r'\s+', '', str(row.get('display_name') or '')) in {'정기점검', '정기정검'}:
        return 'regular_maintenance'  # Read legacy labels without rewriting user files.
    return kind


def occurrence_id(row):
    at = row.get('scheduled_at')
    value = at.isoformat() if isinstance(at, datetime) else ''
    return hashlib.sha256((control_kind(row)+'|'+value).encode()).hexdigest()[:24]


def maintenance_rows(controls):
    return [row for row in controls if isinstance(row, dict)
            and control_kind(row) in MAINTENANCE_KINDS
            and isinstance(row.get('scheduled_at'), datetime)
            and not row.get('historical_only') and not row.get('cancelled_at')]


def reopening(row, controls):
    start = row['scheduled_at']
    explicit = row.get('reopened_at')
    if isinstance(explicit, datetime) and explicit >= start:
        return explicit
    candidates = []
    starts = maintenance_rows(controls)
    for opened in controls:
        if not isinstance(opened, dict) or control_kind(opened) != 'server_open' or opened.get('historical_only'):
            continue
        at = opened.get('scheduled_at')
        if not isinstance(at, datetime) or not start <= at < start+timedelta(days=7):
            continue
        owner = opened.get('maintenance_id')
        if owner:
            matched = owner == occurrence_id(row)
        else:
            preceding = [item for item in starts if item['scheduled_at'] <= at]
            latest = max(preceding, key=lambda item:item['scheduled_at'], default=None)
            matched = latest is not None and occurrence_id(latest) == occurrence_id(row)
        if matched:
            candidates.append(at)
    return min(candidates) if candidates else None


def regular_boundary(reference, weekday, hour, minute, controls=(), *, previous=False):
    """A dated occurrence overrides only its calendar week's weekly template."""
    week = reference.replace(hour=0, minute=0, second=0, microsecond=0)-timedelta(days=reference.weekday())
    overrides = {}
    for row in controls:
        if (isinstance(row, dict) and control_kind(row) == 'regular_maintenance'
                and isinstance(row.get('scheduled_at'), datetime) and not row.get('historical_only')):
            start = row['scheduled_at']
            key = start.date()-timedelta(days=start.weekday())
            overrides.setdefault(key, [])
            if not row.get('cancelled_at'):
                overrides[key].append(start)
    values = []
    for offset in range(-2, 3):
        start = week+timedelta(weeks=offset)
        if start.date() in overrides:
            values.extend(overrides[start.date()])
        elif hour or minute:
            values.append((start+timedelta(days=weekday)).replace(hour=hour, minute=minute))
    eligible = [at for at in values if at <= reference] if previous else [at for at in values if at > reference]
    return (max(eligible) if previous else min(eligible)) if eligible else None


def weekly_settings(state):
    day = str(state.get('maintenance_weekday') or '수')[:1]
    weekday = '월화수목금토일'.find(day)
    def number(key, default, maximum):
        try:
            value = int(re.sub(r'[^0-9]', '', str(state.get(key, default))))
            return value if 0 <= value <= maximum else default
        except ValueError:
            return default
    return max(0, weekday), number('maintenance_hour', 8, 23), number('maintenance_minute', 0, 59)


def next_maintenance(controls, reference):
    values = [row['scheduled_at'] for row in maintenance_rows(controls)
              if row['scheduled_at'] > reference]
    return min(values) if values else None


def active_control(controls, reference, previous_regular=None, imported_at=None):
    candidates = []
    if previous_regular is not None and (imported_at is None or imported_at < previous_regular):
        candidates.append(dict(control_type='maintenance_stale', scheduled_at=previous_regular,
                               display_name='정기점검', created_at=previous_regular))
    for row in controls:
        if not isinstance(row, dict) or row.get('historical_only') or row.get('cancelled_at'):
            continue
        at = row.get('scheduled_at')
        if not isinstance(at, datetime) or at > reference:
            continue
        if previous_regular is not None and at.date() < previous_regular.date():
            continue  # Archive previous-cycle controls; do not mark them completed.
        candidates.append(dict(row, control_type=control_kind(row)))
    return max(candidates, key=lambda row:row['scheduled_at'], default=None)


def fixed_visible(at, reference, controls, cutoff=None, cycle_start=None):
    if cutoff is not None and at >= cutoff:
        return False
    rows = [row for row in maintenance_rows(controls) if row['scheduled_at'] <= at
            and (cycle_start is None or row['scheduled_at'].date() >= cycle_start.date())]
    current = max(rows, key=lambda row:row['scheduled_at'], default=None)
    if current is None:
        return True
    end = reopening(current, controls)
    return end is not None and at >= end


def ordinary_visible(at, created_at, reference, controls, cutoff=None, previous_regular=None):
    if cutoff is not None and at >= cutoff:
        return False
    future = next_maintenance(controls, reference)
    if future is not None and at >= future:
        return isinstance(created_at, datetime) and created_at > future
    resets = [row['scheduled_at'] for row in maintenance_rows(controls) if row['scheduled_at'] <= reference]
    if previous_regular is not None:
        resets.append(previous_regular)
    boundary = max(resets, default=None)
    if boundary is not None and at >= boundary:
        return isinstance(created_at, datetime) and created_at > boundary
    if boundary is None:
        opens = [row['scheduled_at'] for row in controls if isinstance(row, dict)
                 and control_kind(row) == 'server_open' and not row.get('historical_only')
                 and isinstance(row.get('scheduled_at'), datetime) and row['scheduled_at'] <= reference]
        if opens and at < max(opens):
            return False
    return True


def merge_controls(existing, incoming, reference):
    """Merge immutable copies; opening closes one matching maintenance only."""
    result = [dict(row) for row in existing]
    for value in incoming:
        row = dict(value, control_type=control_kind(value))
        kind, at = row['control_type'], row.get('scheduled_at')
        if not isinstance(at, datetime):
            continue
        match = next((i for i, old in enumerate(result)
                      if control_kind(old) == kind and old.get('scheduled_at') == at), None)
        if match is not None:
            result[match] = dict(result[match], **row)
        else:
            if kind in MAINTENANCE_KINDS:
                for old in result:
                    if control_kind(old) == kind and old.get('scheduled_at', datetime.min) > reference and not old.get('cancelled_at'):
                        old['cancelled_at'] = reference
                        old['cancellation_reason'] = 'rescheduled'
            result.append(row)
        if kind == 'server_open' and at <= reference:
            candidates = [old for old in maintenance_rows(result)
                          if old['scheduled_at'] <= at < old['scheduled_at']+timedelta(days=7)
                          and reopening(old, existing) is None
                          and (not row.get('maintenance_id') or occurrence_id(old) == row['maintenance_id'])]
            previous = max(candidates, key=lambda old:old['scheduled_at'], default=None)
            if previous is not None:
                previous['reopened_at'] = at
                previous['closure_source'] = row.get('source', 'manual')
                row['maintenance_id'] = occurrence_id(previous)
                for saved in result:
                    if control_kind(saved) == 'server_open' and saved.get('scheduled_at') == at:
                        saved['maintenance_id'] = row['maintenance_id']
    return sorted(result, key=lambda row:row.get('scheduled_at') or datetime.max)


def _state_boundaries(reference, state):
    controls = state.get('schedule_control_events', [])
    weekday, hour, minute = weekly_settings(state)
    if not any(key in state for key in ('maintenance_weekday', 'maintenance_hour', 'maintenance_minute')):
        hour = minute = 0  # Old partial payloads do not invent a weekly setting.
    previous = regular_boundary(reference, weekday, hour, minute, controls, previous=True)
    cutoff = regular_boundary(reference, weekday, hour, minute, controls)
    deleted = state.get('schedule_delete_active_cutoff_datetime')
    if isinstance(deleted, datetime):
        cutoff = min(cutoff, deleted) if cutoff is not None else deleted
    return controls, cutoff, previous


def fixed_visible_from_state(at, reference, state):
    controls, cutoff, previous = _state_boundaries(reference, state)
    return fixed_visible(at, reference, controls, cutoff, previous)


def ordinary_visible_from_state(at, created_at, reference, state):
    controls, cutoff, previous = _state_boundaries(reference, state)
    return ordinary_visible(at, created_at, reference, controls, cutoff, previous)
