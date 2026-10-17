"""Read-only publication receipt for the post-opening schedule announcement."""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def _time(value):
    if not value:
        return None
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        return value.replace(tzinfo=KST) if value.tzinfo is None else value
    except (ValueError, TypeError):
        return None


def schedule_published(app):
    """No disk, network, web startup or authority changes on the Tk thread.

    Local worker's consumed timestamp or delegated host's ACK is required. A
    captured snapshot older than the latest successful import cannot qualify.
    """
    manager = getattr(app, 'web_service_manager', None)
    imported = _time((getattr(app, 'schedule_last_import_meta', None) or {}).get('imported_at'))
    if manager is None or manager.closed.is_set() or imported is None:
        return False
    receipt = (str(getattr(app, 'schedule_server_profile_id', '')), str(app.current_season_no), imported.isoformat())
    if getattr(app, '_notice_web_publication_receipt', None) == receipt:
        return True  # Routine three-second captures must not reset notice debounce.

    def remember(ready):
        if ready:
            app._notice_web_publication_receipt = receipt
        return bool(ready)

    def current(payload):
        captured = _time((payload or {}).get('captured_at'))
        return bool(captured and captured >= imported and
                    str(payload.get('season')) == str(app.current_season_no) and
                    payload.get('server_name') == app._get_schedule_share_server_name()[:200])

    relay = getattr(manager, 'delegation', None)
    if relay is not None:
        with relay.lock:
            if relay.role == 'source':
                from web_delegated_hosting import edit_hash
                payload = relay.local_payload
                return remember(current(payload) and relay.pending is None and relay.sent_hash and
                                relay.sent_hash == edit_hash(payload))
            if relay.role == 'host':
                return False  # This PC publishes someone else's schedule, not its local edits.
    with manager.lock:
        return remember(current(manager.pending_snapshot) and manager.view.get('state') == 'running' and
                        manager.view.get('schedule_state') == 'ready' and manager.snapshot_version > 0 and
                        manager.written_snapshot_version >= manager.snapshot_version and
                        manager.snapshot_updated_at and
                        manager.view.get('schedule_updated_at') == manager.snapshot_updated_at)
