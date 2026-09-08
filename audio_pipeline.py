"""Application-independent audio request pipeline.

The module deliberately does not import Tkinter, Discord, edge-tts, or a
platform player.  It turns an already planned alert into a durable playback
request, queues it, applies safe output checks, and routes it to an adapter.
GUI and Discord code can therefore share the same request contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
import itertools
import os
import queue
import threading
import time
import uuid
from typing import Any, Callable, Iterable


class AudioSourceKind(str, Enum):
    FILE = "file"
    TTS = "tts"
    SILENT = "silent"


class OutputTarget(str, Enum):
    LOCAL = "local"
    DISCORD = "discord"
    LEGACY = "legacy"


@dataclass(frozen=True)
class AudioSource:
    kind: AudioSourceKind
    paths: tuple[str, ...] = ()
    fallback_text: str = ""
    ready: bool = False


@dataclass(frozen=True)
class PlaybackRequest:
    request_id: str
    phase: str
    priority: int
    lane: str
    target_time: datetime
    start_time: datetime
    deadline: datetime | None
    source: AudioSource
    targets: frozenset[OutputTarget]
    dedupe_key: str
    generation: int
    created_at: datetime
    payload: dict[str, Any] = field(compare=False, repr=False)


@dataclass(frozen=True)
class OutputDecision:
    allowed: bool
    reason: str = ""


class AudioSourceResolver:
    """Choose a source without generating audio or touching a player."""

    def resolve(self, clip_paths: Iterable[object], fallback_text: object = "") -> AudioSource:
        paths = tuple(
            path_text
            for path_text in (str(path or "").strip() for path in clip_paths)
            if path_text
        )
        ready_paths = tuple(path for path in paths if os.path.isfile(path))
        if ready_paths:
            return AudioSource(AudioSourceKind.FILE, ready_paths, str(fallback_text or "").strip(), True)
        text = str(fallback_text or "").strip()
        if text:
            return AudioSource(AudioSourceKind.TTS, (), text, False)
        return AudioSource(AudioSourceKind.SILENT, (), "", False)


class OutputConditionEvaluator:
    """Reject only requests that are impossible or already expired.

    More aggressive policies (bot status, pre-emption, late-drop tolerance)
    can be added here later without putting those rules in players.
    """

    def __init__(
        self,
        *,
        enforce_deadline: bool = False,
        policy: Callable[[PlaybackRequest, datetime], OutputDecision | bool | None] | None = None,
    ) -> None:
        # The legacy broker remains the deadline authority during migration.
        # Turning this on is a later, explicitly tested behavior change.
        self.enforce_deadline = bool(enforce_deadline)
        # An application injects its existing business rules here.  Keeping
        # this as a callback makes this module usable by both the desktop app
        # and the Discord process without importing either of them.
        self.policy = policy

    def evaluate(self, request: PlaybackRequest, *, now: datetime | None = None) -> OutputDecision:
        current_time = now or datetime.now()
        if request.source.kind is AudioSourceKind.SILENT:
            return OutputDecision(False, "no_audio_source")
        if not request.targets:
            return OutputDecision(False, "no_output_target")
        if self.enforce_deadline and request.deadline is not None and current_time > request.deadline:
            return OutputDecision(False, "deadline_expired")
        if self.policy is not None:
            try:
                policy_result = self.policy(request, current_time)
            except Exception as exc:
                # A failed condition check must never start unexpected audio.
                return OutputDecision(False, f"policy_error:{type(exc).__name__}")
            if isinstance(policy_result, OutputDecision):
                return policy_result
            if policy_result is False:
                return OutputDecision(False, "policy_rejected")
        return OutputDecision(True, "")


class PlaybackRequestFactory:
    """Create immutable pipeline requests from the legacy alert plan payload."""

    _PRIORITY_BY_PHASE = {
        "COUNTDOWN_SEQUENCE": 100,
        "SPAWN_CONFIRMED": 90,
        "SPAWN_SOON": 80,
        "FIXED_PRE_ALERT": 70,
        "PRE_ALERT": 60,
        "VOICE_COMMAND": 50,
    }

    def __init__(self, resolver: AudioSourceResolver | None = None) -> None:
        self.resolver = resolver or AudioSourceResolver()

    def create_legacy_request(self, payload: dict[str, Any]) -> PlaybackRequest:
        copied_payload = dict(payload)
        target_time = copied_payload.get("target_time")
        if not isinstance(target_time, datetime):
            target_time = datetime.now()
        start_time = copied_payload.get("earliest_play_at")
        if not isinstance(start_time, datetime):
            offset_seconds = max(0, int(copied_payload.get("offset_sec") or 0))
            start_time = target_time - timedelta(seconds=offset_seconds)
        deadline = copied_payload.get("expires_at")
        if not isinstance(deadline, datetime):
            deadline = None
        phase = str(copied_payload.get("phase") or "GENERAL").strip().upper() or "GENERAL"
        source = self.resolver.resolve(copied_payload.get("clip_paths") or (), copied_payload.get("fallback_text"))
        raw_targets = copied_payload.get("output_targets") or (OutputTarget.LEGACY.value,)
        targets = frozenset(
            target
            for target in (
                _coerce_output_target(raw_target)
                for raw_target in (raw_targets if isinstance(raw_targets, (list, tuple, set, frozenset)) else (raw_targets,))
            )
            if target is not None
        )
        if not targets:
            targets = frozenset({OutputTarget.LEGACY})
        priority = self._PRIORITY_BY_PHASE.get(phase, 40)
        if bool(copied_payload.get("force_audio")):
            priority += 5
        return PlaybackRequest(
            request_id=str(copied_payload.get("pipeline_request_id") or uuid.uuid4().hex),
            phase=phase,
            priority=priority,
            lane=str(copied_payload.get("lane") or "center").strip() or "center",
            target_time=target_time,
            start_time=start_time,
            deadline=deadline,
            source=source,
            targets=targets,
            dedupe_key=str(copied_payload.get("dedupe_key") or "").strip(),
            generation=_safe_int(copied_payload.get("generation")),
            created_at=copied_payload.get("created_at") if isinstance(copied_payload.get("created_at"), datetime) else datetime.now(),
            payload=copied_payload,
        )


class PlaybackQueue:
    """Thread-safe queue with a narrow API suitable for GUI and Discord."""

    def __init__(self) -> None:
        self._queue: queue.Queue[PlaybackRequest | None] = queue.Queue()

    def put(self, request: PlaybackRequest) -> None:
        self._queue.put(request)

    def get(self, timeout: float | None = None) -> PlaybackRequest | None:
        return self._queue.get(timeout=timeout)

    def task_done(self) -> None:
        self._queue.task_done()

    def close(self) -> None:
        self._queue.put(None)

    def clear(self) -> None:
        with self._queue.mutex:
            self._queue.queue.clear()


class PlaybackScheduler:
    """Collect a small batch and apply output policy before routing it.

    ``compatibility_order`` keeps the established broker's arrival ordering
    during migration.  The request still carries priority/start_time so the
    scheduler can become authoritative after parity tests are complete.
    """

    def __init__(self, evaluator: OutputConditionEvaluator | None = None, *, compatibility_order: bool = True) -> None:
        self.evaluator = evaluator or OutputConditionEvaluator()
        self.compatibility_order = bool(compatibility_order)
        self.last_rejections: list[tuple[PlaybackRequest, OutputDecision]] = []

    def collect(self, playback_queue: PlaybackQueue, *, timeout: float, collect_window: float) -> list[PlaybackRequest] | None:
        first = playback_queue.get(timeout=timeout)
        if first is None:
            playback_queue.task_done()
            return None
        batch = [first]
        deadline = time.monotonic() + max(0.0, float(collect_window))
        while time.monotonic() < deadline:
            try:
                item = playback_queue.get(timeout=min(0.05, max(0.0, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if item is None:
                playback_queue.task_done()
                break
            batch.append(item)
            playback_queue.task_done()
        accepted: list[PlaybackRequest] = []
        self.last_rejections = []
        for request in batch:
            decision = self.evaluator.evaluate(request)
            if decision.allowed:
                accepted.append(request)
            else:
                self.last_rejections.append((request, decision))
        if not self.compatibility_order:
            accepted.sort(key=lambda request: (request.start_time, -request.priority, request.created_at, request.request_id))
        return accepted


class OutputRouter:
    """Dispatch a request to registered local/Discord/legacy adapters."""

    def __init__(self) -> None:
        self._handlers: dict[OutputTarget, Callable[[PlaybackRequest], None]] = {}

    def register(self, target: OutputTarget, handler: Callable[[PlaybackRequest], None]) -> None:
        self._handlers[target] = handler

    def route(self, request: PlaybackRequest) -> None:
        for target in request.targets:
            handler = self._handlers.get(target)
            if handler is not None:
                handler(request)


class AudioPipeline:
    """Composition root used by both the GUI and a future Discord adapter."""

    def __init__(
        self,
        *,
        compatibility_order: bool = True,
        evaluator: OutputConditionEvaluator | None = None,
    ) -> None:
        self.factory = PlaybackRequestFactory()
        self.queue = PlaybackQueue()
        self.scheduler = PlaybackScheduler(evaluator=evaluator, compatibility_order=compatibility_order)
        self.router = OutputRouter()

    def submit_legacy(self, payload: dict[str, Any]) -> PlaybackRequest:
        request = self.factory.create_legacy_request(payload)
        self.queue.put(request)
        return request

    def close(self) -> None:
        self.queue.close()

    def clear(self) -> None:
        self.queue.clear()


def _coerce_output_target(value: object) -> OutputTarget | None:
    try:
        return OutputTarget(str(value or "").strip().casefold())
    except ValueError:
        return None


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
