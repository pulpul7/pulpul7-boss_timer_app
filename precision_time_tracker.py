"""Monotonic ROI tick measurements, independent of OCR and the scheduler.

Countdown displays are assumed to FLOOR remaining time.  A transition from
R to R-1 therefore occurs with R seconds remaining, not R-1.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median


@dataclass(frozen=True)
class PrecisionConfig:
    interval: float = 0.5  # Change only here to try 0.25 seconds.
    duration: float = 65.0
    buffer_samples: int = 32
    second_ticks: int = 4
    pixel_delta: int = 35
    changed_pixels: int = 6
    max_gap: float = 0.8
    candidate_spread: float = 0.65

    @classmethod
    def from_rate(cls, rate):
        from schedule_precision import normalize_capture_rate
        rate = normalize_capture_rate(rate)
        # Retain the same bounded ~16 second pre-OCR history at every rate.
        return cls(interval=1.0/rate,buffer_samples=16*rate)


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    def contains(self, other):
        return (self.left <= other.left < other.right <= self.right
                and self.top <= other.top < other.bottom <= self.bottom)


@dataclass(frozen=True)
class Pixels:
    rect: Rect
    rgb: bytes

    def crop(self, rect: Rect):
        if not self.rect.contains(rect):
            raise ValueError("ROI outside captured candidate")
        stride = self.rect.width * 3
        x = (rect.left - self.rect.left) * 3
        data = b"".join(self.rgb[y * stride + x:y * stride + x + rect.width * 3]
                        for y in range(rect.top - self.rect.top, rect.bottom - self.rect.top))
        return Pixels(rect, data)


@dataclass(frozen=True)
class Sample:
    start: float
    end: float
    pixels: Pixels

    @property
    def at(self):
        return (self.start + self.end) / 2.0


@dataclass(frozen=True)
class Tick:
    lower: float
    upper: float

    @property
    def at(self):
        return (self.lower + self.upper) / 2.0

    @property
    def uncertainty(self):
        return (self.upper - self.lower) / 2.0


class TickBuffer:
    def __init__(self, capacity):
        self.samples = deque(maxlen=capacity)
        self.overflow = False

    def append(self, sample):
        if len(self.samples) == self.samples.maxlen:
            self.overflow = True
        self.samples.append(sample)


class RoiChangeDetector:
    def __init__(self, config: PrecisionConfig):
        self.config = config
        self.previous = None

    def feed(self, sample: Sample):
        previous, self.previous = self.previous, sample
        if previous is None:
            return None
        if sample.at <= previous.at:
            raise ValueError("non-monotonic capture")
        if sample.at - previous.at > self.config.max_gap:
            raise ValueError("capture gap too large: possible missed tick")
        a, b = previous.pixels.rgb, sample.pixels.rgb
        if len(a) != len(b):
            raise ValueError("ROI size changed")
        changed = sum(max(abs(a[i+j] - b[i+j]) for j in range(3)) > self.config.pixel_delta
                      for i in range(0, len(a), 3))
        if changed >= self.config.changed_pixels:
            return Tick(previous.start, sample.end)
        return None


@dataclass
class BossTimeTracker:
    name: str
    remaining: int
    mode: str
    config: PrecisionConfig
    ticks: list[Tick] = field(default_factory=list)
    candidates: list[float] = field(default_factory=list)
    error: str = ""

    @property
    def enough(self):
        return len(self.ticks) >= (1 if self.mode == "minute" else self.config.second_ticks)

    def add(self, tick: Tick):
        self.ticks.append(tick)
        # At the first minute change the boundary remaining time is exactly
        # the initially displayed whole-minute value (including the hours).
        candidate = tick.at + self.remaining - (len(self.ticks)-1 if self.mode == "second" else 0)
        self.candidates.append(candidate)
        if max(self.candidates) - min(self.candidates) > self.config.candidate_spread:
            self.error = "inconsistent ticks (animation or missed update)"
        return candidate

    def verify_remaining(self, observed):
        expected = self.remaining - (60 if self.mode == "minute" else len(self.ticks))
        return observed == expected


@dataclass
class GameClockTracker:
    initial: datetime
    ticks: list[Tick] = field(default_factory=list)

    def anchors(self):
        return [tick.at - (i + 1) for i, tick in enumerate(self.ticks)]

    def to_game_time(self, at: float):
        return self.initial + timedelta(seconds=at - median(self.anchors()))


def precision_result(boss: BossTimeTracker, clock: GameClockTracker):
    target = clock.to_game_time(median(boss.candidates))
    # Clock and countdown phase errors both contribute.  500 ms sampling
    # alone does NOT guarantee +/-250 ms absolute game time.
    uncertainty = max(t.uncertainty for t in boss.ticks) + max(t.uncertainty for t in clock.ticks)
    anchor = median(clock.anchors())
    # Clock sampling can finish long before a minute rollover.  Compare phase
    # to the extrapolated one-second grid, not to an old observed tick.
    offsets = [(t.at-anchor+0.5) % 1.0-0.5 for t in boss.ticks]
    return {
        "boss_name": boss.name, "mode": boss.mode,
        "target_datetime": target.isoformat(timespec="microseconds"),
        "uncertainty_seconds": uncertainty,
        "candidate_monotonic": list(boss.candidates),
        "candidate_game_times": [clock.to_game_time(c).isoformat(timespec="microseconds") for c in boss.candidates],
        "ticks": [dict(lower=t.lower, upper=t.upper, midpoint=t.at) for t in boss.ticks],
        "clock_offsets_seconds": offsets,
        "display_assumption": "floor", "status": "measured",
    }
