"""Bounded, content-free operational counters for the MCP process."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SAFE_LABELS = frozenset({"tool", "status", "error_class", "transport"})


@dataclass(frozen=True, slots=True)
class CounterSeries:
    """One safe counter series for local operator inspection."""

    name: str
    labels: tuple[tuple[str, str], ...]
    value: int


class InProcessCounters:
    """Thread-safe counters with a hard cap on label series."""

    def __init__(self, max_series: int = 512) -> None:
        if isinstance(max_series, bool) or not isinstance(max_series, int) or max_series < 1:
            raise ValueError("max_series must be a positive integer")
        self._max_series = max_series
        self._values: dict[tuple[str, tuple[tuple[str, str], ...]], int] = {}
        self._dropped = 0
        self._lock = threading.Lock()

    @property
    def dropped_series(self) -> int:
        with self._lock:
            return self._dropped

    def increment(self, name: str, *, amount: int = 1, **labels: object) -> bool:
        if not isinstance(name, str) or not _LABEL.fullmatch(name):
            raise ValueError("counter name must be a bounded identifier")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            raise ValueError("amount must be positive")
        safe_labels = tuple(
            sorted(
                (key, value)
                for key, value in labels.items()
                if key in _SAFE_LABELS and isinstance(value, str) and _LABEL.fullmatch(value)
            )
        )
        key = (name, safe_labels)
        with self._lock:
            if key not in self._values and len(self._values) >= self._max_series:
                self._dropped += 1
                return False
            self._values[key] = self._values.get(key, 0) + amount
        return True

    def snapshot(self) -> tuple[CounterSeries, ...]:
        with self._lock:
            rows = [
                CounterSeries(name, labels, value) for (name, labels), value in self._values.items()
            ]
        return tuple(sorted(rows, key=lambda row: (row.name, row.labels)))


__all__ = ["CounterSeries", "InProcessCounters"]
