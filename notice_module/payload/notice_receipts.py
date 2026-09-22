"""Validate bounded bot receipts; last_played is never a completion signal."""
from .notice_management import parse_time


def bridge_receipt_state(payload, *, job_id, scope_id, bot_pid, sent_at):
    if not isinstance(payload, dict) or payload.get("voice_bridge_receipt_protocol") != 1:
        return "unsupported"
    if payload.get("pid") != bot_pid or not payload.get("voice_connected"):
        return "failed"
    receipts = payload.get("voice_bridge_receipts")
    if not isinstance(receipts, list) or len(receipts) > 128:
        return "unsupported"
    matches = [row for row in receipts if isinstance(row, dict) and row.get("id") == job_id]
    if not matches:
        return "waiting"
    if len(matches) != 1 or matches[0].get("scope_id") != scope_id:
        return "failed"
    row = matches[0]
    try:
        when, sent = parse_time(row.get("at")), parse_time(sent_at)
        if not when or not sent or when < sent:
            return "failed"
    except (ValueError, TypeError):
        return "failed"
    state = row.get("state")
    return state if state in {"started", "completed", "cancelled", "failed"} else "unsupported"
