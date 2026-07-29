"""Scripted notification sink for E2E testing; implements the public NotificationSink contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from lab_agent.notifications import NotificationEnvelope, SendResult


class FakeSinkBehavior(StrEnum):
    """Deterministic delivery outcomes for E2E testing."""

    ACCEPT = "accept"
    TRANSIENT = "transient"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"
    DISABLED = "disabled"


@dataclass(frozen=True)
class FakeSinkDelivery:
    """Record of one attempted delivery."""

    logical_key: str
    canvas_id: str
    trigger_id: str
    reason: str
    round_index: int
    behavior: FakeSinkBehavior


@dataclass
class FakeNotificationSink:
    """Scripted sink that records deliveries and returns preset outcomes.

    Used only in integration tests; never shipped to production.
    Implements the public NotificationSink protocol."""

    behavior: FakeSinkBehavior = FakeSinkBehavior.ACCEPT
    deliveries: list[FakeSinkDelivery] = field(default_factory=list)
    _queue: dict[str, list[FakeSinkBehavior]] = field(default_factory=dict)

    def queue_behavior(self, logical_key: str, behavior: FakeSinkBehavior, times: int = 1) -> None:
        """Queue deterministic outcomes for a specific logical key."""
        self._queue.setdefault(logical_key, []).extend([behavior] * times)

    def send(self, envelope: NotificationEnvelope) -> SendResult:
        """Attempt one delivery and record it."""
        key = envelope.logical_key
        queued = self._queue.get(key)
        behavior = queued.pop(0) if queued else self.behavior
        self.deliveries.append(
            FakeSinkDelivery(
                logical_key=key,
                canvas_id=envelope.canvas_id,
                trigger_id=envelope.trigger_id,
                reason=envelope.reason,
                round_index=envelope.round_index,
                behavior=behavior,
            )
        )
        if behavior is FakeSinkBehavior.ACCEPT:
            return SendResult.accepted()
        if behavior is FakeSinkBehavior.TRANSIENT:
            return SendResult.transient()
        if behavior is FakeSinkBehavior.REJECTED:
            return SendResult.rejected()
        if behavior is FakeSinkBehavior.AMBIGUOUS:
            return SendResult.ambiguous()
        if behavior is FakeSinkBehavior.DISABLED:
            return SendResult.disabled()
        raise ValueError(f"unknown behavior: {behavior}")

    @property
    def delivery_count(self) -> int:
        """Total deliveries recorded."""
        return len(self.deliveries)

    def clear_deliveries(self) -> None:
        """Reset delivery log for next test."""
        self.deliveries.clear()


__all__ = ["FakeSinkBehavior", "FakeSinkDelivery", "FakeNotificationSink"]
