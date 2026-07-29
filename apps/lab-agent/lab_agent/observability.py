"""Safe, bounded observability helpers for local process telemetry."""

from __future__ import annotations

import math
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol, TypeAlias, TypeGuard

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_FIELDS = frozenset({"stage", "adapter", "status", "error_class"})
Scalar: TypeAlias = str | int | float | bool
MetricKey: TypeAlias = tuple[str, str, tuple[tuple[str, Scalar], ...]]

class EventLogger(Protocol):
    """Minimal structured logger contract used by :func:`emit_event`."""

    def info(self, event: str, **fields: object) -> object:
        """Write one structured info event."""

@dataclass(frozen=True, slots=True)
class EventContext:
    """Required scope fields for an operational event or metric."""

    run_id: str
    tenant_id: str
    canvas_id: str
    round: int | None = None
    stage: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("run_id", "tenant_id", "canvas_id"):
            _require_label(getattr(self, field_name), field_name)
        if self.round is not None and (
            isinstance(self.round, bool) or not isinstance(self.round, int) or self.round < 0
        ):
            raise ValueError("round must be a non-negative integer")
        if self.stage is not None:
            _require_label(self.stage, "stage")

@dataclass(frozen=True, slots=True)
class TimerStats:
    """Aggregate duration information for one bounded metric series."""

    count: int
    total_seconds: float
    minimum_seconds: float
    maximum_seconds: float

@dataclass(frozen=True, slots=True)
class MetricSeries:
    """One safe metric series suitable for local operator inspection."""

    kind: str
    name: str
    labels: tuple[tuple[str, Scalar], ...]
    count: int
    timer: TimerStats | None = None

def safe_event(event: str, context: EventContext, **fields: object) -> dict[str, Scalar]:
    """Build an allowlisted event payload, dropping content and credential fields."""

    _require_label(event, "event")
    values: dict[str, Scalar] = {
        "event": event,
        "run_id": context.run_id,
        "tenant_id": context.tenant_id,
        "canvas_id": context.canvas_id,
    }
    if context.round is not None:
        values["round"] = context.round
    if context.stage is not None:
        values["stage"] = context.stage
    for name, value in fields.items():
        if name in _SAFE_FIELDS and isinstance(value, str) and _LABEL.fullmatch(value):
            values[name] = value
        elif name == "duration_ms" and _is_duration(value):
            values[name] = value
    return values

def emit_event(
    logger: EventLogger, event: str, context: EventContext, **fields: object
) -> dict[str, Scalar]:
    """Emit one safe structured event and return the exact emitted payload."""

    payload = safe_event(event, context, **fields)
    logger.info(event, **{name: value for name, value in payload.items() if name != "event"})
    return payload

class InProcessMetrics:
    """Thread-safe counters and timers with a hard cap on metric series."""

    def __init__(self, max_series: int = 1024, clock: Callable[[], float] = time.monotonic) -> None:
        if isinstance(max_series, bool) or not isinstance(max_series, int) or max_series < 1:
            raise ValueError("max_series must be a positive integer")
        self._max_series = max_series
        self._clock = clock
        self._counters: dict[MetricKey, int] = {}
        self._timers: dict[MetricKey, TimerStats] = {}
        self._dropped_series = 0
        self._lock = threading.Lock()

    @property
    def dropped_series(self) -> int:
        """Return new-series recordings discarded because the cap was reached."""

        with self._lock:
            return self._dropped_series

    def increment(
        self, name: str, context: EventContext, amount: int = 1, **labels: object
    ) -> bool:
        """Increment a bounded counter and return whether it was accepted."""

        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            raise ValueError("amount must be positive")
        key = _metric_key("counter", name, context, labels)
        with self._lock:
            if not self._accept_series(key):
                return False
            self._counters[key] = self._counters.get(key, 0) + amount
        return True

    def observe_duration(
        self, name: str, seconds: float, context: EventContext, **labels: object
    ) -> bool:
        """Record one non-negative duration and return whether it was accepted."""

        if not _is_duration(seconds):
            raise ValueError("duration must be a finite non-negative number")
        key = _metric_key("timer", name, context, labels)
        with self._lock:
            if not self._accept_series(key):
                return False
            previous = self._timers.get(key)
            if previous is None:
                self._timers[key] = TimerStats(1, float(seconds), float(seconds), float(seconds))
            else:
                self._timers[key] = TimerStats(
                    previous.count + 1,
                    previous.total_seconds + float(seconds),
                    min(previous.minimum_seconds, float(seconds)),
                    max(previous.maximum_seconds, float(seconds)),
                )
        return True

    @contextmanager
    def timer(self, name: str, context: EventContext, **labels: object) -> Iterator[None]:
        """Time a block without retaining its content or exception details."""

        start = self._clock()
        try:
            yield
        finally:
            self.observe_duration(name, max(0.0, self._clock() - start), context, **labels)

    def counter_value(self, name: str, context: EventContext, **labels: object) -> int:
        """Return a counter value without creating a new metric series."""

        key = _metric_key("counter", name, context, labels)
        with self._lock:
            return self._counters.get(key, 0)

    def timer_stats(self, name: str, context: EventContext, **labels: object) -> TimerStats | None:
        """Return timer aggregates without creating a new metric series."""

        key = _metric_key("timer", name, context, labels)
        with self._lock:
            return self._timers.get(key)

    def snapshot(self) -> tuple[MetricSeries, ...]:
        """Return a bounded, safe snapshot of counters and timers."""

        with self._lock:
            counters = [
                MetricSeries(kind, name, labels, count)
                for (kind, name, labels), count in self._counters.items()
            ]
            timers = [
                MetricSeries(kind, name, labels, stats.count, stats)
                for (kind, name, labels), stats in self._timers.items()
            ]
        return tuple(
            sorted(counters + timers, key=lambda item: (item.kind, item.name, item.labels))
        )

    def _accept_series(self, key: MetricKey) -> bool:
        if key in self._counters or key in self._timers:
            return True
        if len(self._counters) + len(self._timers) >= self._max_series:
            self._dropped_series += 1
            return False
        return True

def _metric_key(
    kind: str, name: str, context: EventContext, labels: dict[str, object]
) -> MetricKey:
    _require_label(name, "metric name")
    fields = safe_event(name, context, **labels)
    dimensions = tuple(
        (key, value) for key, value in fields.items() if key != "event"
    )
    return kind, name, dimensions

def _require_label(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        raise ValueError(f"{field_name} must be a bounded identifier")

def _is_duration(value: object) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )

__all__ = [
    "EventContext",
    "EventLogger",
    "InProcessMetrics",
    "MetricSeries",
    "TimerStats",
    "emit_event",
    "safe_event",
]
