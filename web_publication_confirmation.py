"""Pure provider acknowledgement check for a specific web takeover trial."""
from datetime import datetime
import math


def address_confirmed(ddns, started_at):
    if (not isinstance(ddns, dict) or ddns.get('configured') is False
            or ddns.get('enabled') is False or ddns.get('busy')
            or ddns.get('state') not in {'ready', 'dns_pending'}
            or type(started_at) not in (int, float) or not math.isfinite(started_at)):
        return False
    try:
        updated = datetime.fromisoformat(ddns.get('last_updated') or '').timestamp()
        return math.isfinite(updated) and updated >= started_at
    except (TypeError, ValueError, OverflowError, OSError):
        return False
