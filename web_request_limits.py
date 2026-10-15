"""Bounded worker-local limits for reloads and read APIs; never contact services."""
import math
import threading
import time

READ_PATHS = frozenset({"/", "/api/status", "/api/schedule", "/api/session", "/api/push", "/api/boss-spectrum"})


class CooldownLimiter:
    """Fixed minimum intervals. Rejected attempts never extend the deadline."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.deadlines = {}
        self.next_cleanup = 0.0

    def _cleanup(self, now):
        if now >= self.next_cleanup:
            self.deadlines = {key: until for key, until in self.deadlines.items() if until > now}
            self.next_cleanup = now + 10

    def _key(self, key):
        if key not in self.deadlines and len(self.deadlines) >= 4092:
            return ('overflow', key[0])
        return key

    def claim(self, keys, seconds):
        now = self.clock()
        with self.lock:
            self._cleanup(now)
            keys = [self._key(key) for key in keys]
            remaining = max((self.deadlines.get(key, now) - now for key in keys), default=0)
            if remaining > 0:
                return math.ceil(remaining)
            for key in keys:
                self.deadlines[key] = now + seconds
            return 0

    def start(self, key, seconds):
        now = self.clock()
        with self.lock:
            self._cleanup(now)
            self.deadlines[self._key(key)] = now + seconds

    def wait(self, key):
        now = self.clock()
        with self.lock:
            self._cleanup(now)
            return max(0, math.ceil(self.deadlines.get(self._key(key), now) - now))

    def clear(self, keys):
        with self.lock:
            for key in keys:
                self.deadlines.pop(key, None)


class ReadLimiter:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.buckets = {}
        self.next_cleanup = 0.0

    def wait(self, visitor, ip):
        now = self.clock()
        with self.lock:
            if now >= self.next_cleanup:
                self.buckets = {key: row for key, row in self.buckets.items() if now - row[1] < 120}
                self.next_cleanup = now + 30
            scopes = [(('global', ''), 240, 40), (('ip', ip), 120, 10),
                      (('visitor', visitor or ip), 8, 0.5)]
            rows, delay = [], 0.0
            for key, capacity, rate in scopes:
                # Keep two overflow slots; new identities never bypass the bounds.
                if key not in self.buckets and len(self.buckets) >= 4094 and key[0] != 'global':
                    key = ('overflow', key[0])
                tokens, last = self.buckets.get(key, (float(capacity), now))
                tokens = min(capacity, tokens + max(0, now - last) * rate)
                self.buckets[key] = (tokens, now)
                rows.append((key, tokens))
                delay = max(delay, (1 - tokens) / rate)
            if delay > 0:
                return max(1, math.ceil(delay))
            for key, tokens in rows:
                self.buckets[key] = (tokens - 1, now)
            return 0
