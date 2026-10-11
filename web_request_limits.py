"""Bounded worker-local limits for reloads and read APIs; never contact services."""
import math
import threading
import time

READ_PATHS = frozenset({"/", "/api/status", "/api/schedule", "/api/session"})


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
