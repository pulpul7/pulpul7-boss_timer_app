"""GUI-thread notice commands for dated maintenance; no Tk or network calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from schedule_maintenance import control_kind, occurrence_id, merge_controls


def _local(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('점검 공지에 시간대가 없습니다.')
    return parsed.astimezone(timezone(timedelta(hours=9))).replace(tzinfo=None)


def apply_transition(app, request):
    if (getattr(app, 'discord_handover_busy', False) or getattr(app, 'schedule_input_window_busy', False)
            or str(request.get('server_id')) != str(getattr(app, 'schedule_server_profile_id', ''))
            or str(request.get('season')) != str(app.current_season_no)):
        return 'deferred'
    from program_update_guard import active
    if active():
        return 'deferred'
    kind = request.get('kind')
    action = request.get('action')
    if kind not in {'regular_maintenance', 'temporary_maintenance'} or action not in {'start', 'plan', 'complete', 'cancel'}:
        return 'conflict'
    at = _local(request['scheduled_at'])
    now = app._get_schedule_reference_datetime()
    if not now-timedelta(days=7) <= at <= now+timedelta(days=7):
        return 'conflict'
    before = getattr(app, 'schedule_control_events', [])
    rows = deepcopy(before)
    matches = [row for row in rows if control_kind(row) == kind and row.get('scheduled_at') == at]
    if len(matches) > 1:
        return 'conflict'
    row = matches[0] if matches else None
    if action == 'start' and kind == 'regular_maintenance':
        hour = str(app.schedule_maintenance_hour_var.get())
        minute = str(app.schedule_maintenance_minute_var.get())
        import re
        if re.sub(r'\D', '', hour) in {'0', '00'} and re.sub(r'\D', '', minute) in {'0', '00'}:
            return 'conflict'  # Preserve an explicitly disabled automatic regular import.
    if row is None:
        if action != 'start' or (kind == 'temporary_maintenance' and at <= now):
            return 'conflict'
        row = dict(control_type=kind, scheduled_at=at, created_at=now,
                   display_name='정기점검' if kind == 'regular_maintenance' else '임시점검',
                   notice_import_key=request.get('source_id') or request.get('key', ''), source='notice')
        # Notice collection never cancels/replaces a different manual future start.
        if any(control_kind(old) == kind and old.get('scheduled_at', datetime.min) > now
               and not old.get('cancelled_at') for old in rows):
            return 'conflict'
        rows.append(row)
    end = _local(request['event_until']) if request.get('event_until') else None
    if end is not None and not at < end < at+timedelta(days=7):
        return 'conflict'
    if action in {'start', 'plan'}:
        if row.get('reopened_at') or row.get('cancelled_at'):
            return 'existing' if action == 'start' else 'conflict'
        if end is not None:
            row['planned_end_at'] = end
    elif action == 'complete':
        if end is None or end > now or row.get('cancelled_at'):
            return 'conflict'
        if row.get('reopened_at'):
            return 'existing' if row['reopened_at'] == end else 'conflict'
        row['reopened_at'] = end
        row['closure_source'] = 'notice'
        rows = merge_controls(rows, [dict(control_type='server_open', display_name='서버오픈',
            scheduled_at=end, created_at=now, source='notice', maintenance_id=occurrence_id(row))], now)
    else:
        if row.get('reopened_at'):
            return 'conflict'
        if row.get('cancelled_at'):
            return 'existing'
        row['cancelled_at'] = now
        row['cancellation_reason'] = 'notice'
    if rows == before:
        return 'existing'
    app.schedule_control_events = rows
    try:
        app._save_schedule_state(mark_github_dirty=True, raise_on_error=True)
    except Exception:
        app.schedule_control_events = before
        raise
    try:
        refresh = getattr(app, '_refresh_schedule_view', None) or app._refresh_schedule_tree_scope
        refresh()
    except Exception:
        app._append_debug_log('maintenance_transition_saved_refresh_failed')
    app._append_debug_log('maintenance_transition_applied kind='+kind+' action='+action+' at='+at.isoformat())
    return 'applied'
