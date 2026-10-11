"""Fail-closed authority lease shared only by the GUI and its web worker."""
import math
import time
from web_service_common import read_json


def external_allowed(root, run_id, now=None):
    try:
        data = read_json(root / "authority_lease.json")
        if not isinstance(data, dict):
            return False
        now = time.time() if now is None else now
        expires = data.get("valid_until")
        return bool(data.get("schema") == 1 and data.get("enabled") is True
                    and data.get("run_id") == run_id and run_id
                    and type(expires) in {int, float} and math.isfinite(expires)
                    and now < expires <= now + 12)
    except (OSError, ValueError, TypeError):
        return False
