"""Shared, bounded routing for explicit read-only Discord queries.

The interaction token and schedule/audio data stay on the originating PCs.
This ledger is independent of administrator authority and voice ownership.
"""
from copy import deepcopy
import threading
import time

RESPONSE_SECONDS = 3
REQUEST_TTL = 120
MAX_REQUESTS = 32
QUERY_READ_CACHE_SECONDS = 0.5
QUERY_TYPES = frozenset({"help", "schedule", "plain", "voices", "image"})


class QueryRoutingError(RuntimeError):
    pass


class QueryLedger:
    def __init__(self, authority_record):
        self.get = authority_record.get
        self.put = authority_record.put
        self.guild = str(authority_record.scope["guild"])
        self.path = authority_record.path.removesuffix(".json") + "_queries.json"
        self.read_lock = threading.Lock()
        self.read_cache = None
        self.read_cache_at = 0.0

    def read(self, *, cache_for=0.0):
        # Only waiting query loops use a short shared read cache. CAS updates
        # always call the default uncached path for a fresh SHA.
        with self.read_lock:
            if (cache_for > 0 and self.read_cache is not None
                    and time.monotonic() - self.read_cache_at < cache_for):
                return deepcopy(self.read_cache)
            data, sha = self._read_latest()
            self.read_cache = deepcopy((data, sha))
            self.read_cache_at = time.monotonic()
            return data, sha

    def _read_latest(self):
        data, sha, error = self.get(self.path)
        if error:
            raise QueryRoutingError(error)
        if data is None:
            data = dict(schema=1, guild=self.guild, requests={}, responder={}, routing_generation=0)
        if (not isinstance(data, dict) or data.get("schema") != 1
                or str(data.get("guild")) != self.guild or not isinstance(data.get("requests"), dict)):
            raise QueryRoutingError("조회 요청 기록을 확인할 수 없습니다.")
        return data, sha

    def mutate(self, change):
        for attempt in range(3):
            data, sha = self.read()
            previous = deepcopy(data)
            result = change(data["requests"], data)
            if data == previous:
                return deepcopy(result)
            try:
                ok, error = self.put(self.path, data, sha=sha, message="BossTimer read-only query routing")
            finally:
                with self.read_lock:
                    self.read_cache = None
            if ok:
                return deepcopy(result)
            if attempt == 2 or not any(code in str(error) for code in ("409", "422")):
                raise QueryRoutingError(error or "조회 담당자 기록이 변경되었습니다.")

    @staticmethod
    def _prune(requests):
        now = time.time()
        for key, entry in list(requests.items()):
            if not isinstance(entry, dict) or now - float(entry.get("created", 0)) >= REQUEST_TTL:
                del requests[key]

    @staticmethod
    def order_candidates(candidates, preferred=None):
        """Pure ordering shared by the background cache and timeout router."""
        ordered = deepcopy(candidates)
        preferred = preferred or {}
        match = next((member for member in ordered if member.get("client_id") == preferred.get("client_id")
                      and member.get("runtime") == preferred.get("runtime")), None)
        if match:
            owner = ordered[0] if ordered and ordered[0].get("sending") else None
            ordered.remove(match)
            ordered.insert(1 if owner and owner != match else 0, match)
        return ordered

    def offer(self, request_id, query, client, runtime):
        """Register a receiving bot's readiness before waiting for its turn."""
        if query not in QUERY_TYPES:
            raise QueryRoutingError("지원하지 않는 조회 요청입니다.")
        offered_at = time.time()
        def change(requests, record):
            self._prune(requests)
            if request_id not in requests:
                if len(requests) >= MAX_REQUESTS:
                    raise QueryRoutingError("조회 요청이 많습니다. 잠시 후 다시 시도하세요.")
                requests[request_id] = dict(query=query, created=time.time(), state="opening", offers={})
            entry = requests[request_id]
            if entry.get("query") != query:
                raise QueryRoutingError("조회 요청의 종류가 변경되었습니다.")
            entry.setdefault("offers", {})[client] = dict(runtime=runtime, at=offered_at)
            return entry
        return self.mutate(change)

    def open(self, request_id, query, candidates):
        if query not in QUERY_TYPES:
            raise QueryRoutingError("지원하지 않는 조회 요청입니다.")
        def change(requests, record):
            now = time.time()
            self._prune(requests)
            previous = requests.get(request_id, {})
            if previous and previous.get("query") != query:
                raise QueryRoutingError("조회 요청의 종류가 변경되었습니다.")
            if previous and previous.get("state") != "opening":
                return previous
            if not previous and len(requests) >= MAX_REQUESTS:
                raise QueryRoutingError("조회 요청이 많습니다. 잠시 후 다시 시도하세요.")
            preferred = record.get("responder", {})
            ordered = self.order_candidates(candidates, preferred)
            if preferred and not any(member.get("client_id") == preferred.get("client_id")
                                     and member.get("runtime") == preferred.get("runtime") for member in ordered):
                # Once disconnected, the former query PC cannot regain its old pin.
                record["responder"] = {}
                record["routing_generation"] = int(record.get("routing_generation", 0)) + 1
            entry = dict(query=query, created=previous.get("created", now), candidates=ordered, index=0,
                         state="pending" if ordered else "unavailable", deadline=now + RESPONSE_SECONDS,
                         retries=[], offers=deepcopy(previous.get("offers", {})),
                         routing_generation=int(record.get("routing_generation", 0)))
            requests[request_id] = entry
            return entry
        return self.mutate(change)

    def entry(self, request_id, *, cached=False):
        data, _ = self.read(cache_for=QUERY_READ_CACHE_SECONDS if cached else 0.0)
        return deepcopy(data["requests"].get(request_id))

    def clear_responder(self, preferred, generation):
        def change(requests, record):
            current = record.get("responder") or {}
            if (int(record.get("routing_generation", 0)) == generation
                    and current.get("client_id") == preferred.get("client_id")
                    and current.get("runtime") == preferred.get("runtime")):
                record.update(responder={}, routing_generation=generation + 1)
            return record.get("responder", {})
        return self.mutate(change)

    @staticmethod
    def offered(entry):
        index = entry.get("index", 0)
        candidates = entry.get("candidates", [])
        if not 0 <= index < len(candidates):
            return False
        target = candidates[index]
        offer = entry.get("offers", {}).get(target["client_id"], {})
        return (offer.get("runtime") == target.get("runtime")
                and float(offer.get("at", float("inf"))) <= float(entry.get("deadline", 0)))

    def choose(self, request_id, index):
        """Only the coordinator commits a single responding PC using CAS."""
        def change(requests, record):
            entry = requests.get(request_id, {})
            if (entry.get("state") != "pending" or entry.get("index") != index
                    or not self.offered(entry)):
                raise QueryRoutingError("조회 담당자 또는 응답 시간이 변경되었습니다.")
            target = entry["candidates"][index]
            entry.update(state="ready", source=deepcopy(target))
            return entry
        return self.mutate(change)

    def advance(self, request_id, index):
        def change(requests, record):
            entry = requests.get(request_id, {})
            if (entry.get("state") != "pending" or entry.get("index") != index
                    or time.time() < float(entry.get("deadline", 0)) or self.offered(entry)):
                return entry  # A ready response wins against a concurrent timeout.
            candidates = entry["candidates"]
            next_index = index + 1
            entry["retries"].append(dict(previous=deepcopy(candidates[index]),
                next=deepcopy(candidates[next_index]) if next_index < len(candidates) else None))
            entry.update(index=next_index, deadline=time.time() + RESPONSE_SECONDS,
                         state="pending" if next_index < len(candidates) else "unavailable")
            if (next_index < len(candidates)
                    and entry.get("routing_generation") == int(record.get("routing_generation", 0))):
                # Persist failover across subsequent commands, independently of voice.
                record["responder"] = deepcopy(candidates[next_index])
                record["routing_generation"] = int(record.get("routing_generation", 0)) + 1
                entry["routing_generation"] = record["routing_generation"]
            return entry
        return self.mutate(change)
