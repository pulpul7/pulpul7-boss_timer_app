"""In-memory GitHub rate-limit cooldowns; no credentials or files are persisted."""
from email.utils import parsedate_to_datetime
import hashlib
import math
import threading
import time


def header_value(headers, name):
    return next((str(value) for key, value in (headers or {}).items()
                 if key.lower() == name.lower()), "")


def is_rate_limit_response(code, headers, message=""):
    return code == 429 or (code == 403 and (
        header_value(headers, "X-RateLimit-Remaining") == "0"
        or bool(header_value(headers, "Retry-After"))
        or "rate limit" in str(message).lower()))


class GithubRequestGate:
    def __init__(self):
        self._lock = threading.Lock()
        self._deadlines = {}
        self._secondary_failures = {}

    @staticmethod
    def _scope(settings, token):
        credential = hashlib.sha256(str(token).encode()).hexdigest()
        # GitHub quotas are shared by the credential across repositories.
        return credential

    def remaining(self, settings, token, method):
        scope = self._scope(settings, token)
        with self._lock:
            now = time.monotonic()
            self._deadlines = {key: until for key, until in self._deadlines.items() if until > now}
            until = self._deadlines.get(scope, 0)
            return max(0, math.ceil(until - now))

    def limited(self, settings, token, method, headers):
        # Retry-After takes priority; primary exhaustion also needs reset time.
        delays = [60.0]
        retry_after = header_value(headers, "Retry-After")
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
                except (ValueError, TypeError, OverflowError):
                    delay = 60.0
            if math.isfinite(delay):
                delays = [max(1.0, delay)]
        primary = header_value(headers, "X-RateLimit-Remaining") == "0"
        if primary:
            try:
                reset = float(header_value(headers, "X-RateLimit-Reset")) - time.time() + 1
                if math.isfinite(reset):
                    delays.append(reset)
            except (ValueError, TypeError, OverflowError):
                pass
        scope = self._scope(settings, token)
        with self._lock:
            now = time.monotonic()
            self._secondary_failures = {key: value for key, value in self._secondary_failures.items()
                                        if now - value[1] < 3600}
            if not primary:
                failures = min(self._secondary_failures.get(scope, (0, now))[0] + 1, 5)
                self._secondary_failures[scope] = (failures, now)
                if failures > 1:
                    delays.append(min(900, 60 * 2 ** (failures - 1)))
            # Secondary quotas are not isolated to writes. Do not keep polling
            # GET or switch to anonymous access while this token is limited.
            self._deadlines[scope] = max(self._deadlines.get(scope, 0), now + max(delays))


GITHUB_REQUEST_GATE = GithubRequestGate()
