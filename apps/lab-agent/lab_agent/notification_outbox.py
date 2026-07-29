"""Queue and drain terminal notifications outside workflow/model mutations."""

from __future__ import annotations

from dataclasses import dataclass, replace

import structlog

from lab_agent.models.governance import TerminalStopEvent
from lab_agent.notification_smtp import SMTPConfiguration, SMTPNotificationSink
from lab_agent.notifications import (
    NotificationEnvelope,
    NotificationSink,
    SendDisposition,
    SendResult,
)
from lab_agent.state.notification_outbox import (
    FailureCategory,
    NotificationOutboxRecord,
    NotificationStatus,
    StaleNotificationLeaseError,
)
from lab_agent.state_store import StateStore

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class NotificationDrainCounts:
    """Safe counters emitted by one independent outbox drain."""
    sent: int = 0
    retried: int = 0
    quarantined: int = 0
    ambiguous: int = 0

    @property
    def total(self) -> int:
        return self.sent + self.retried + self.quarantined + self.ambiguous

    @property
    def fields(self) -> dict[str, int]:
        return {"sent": self.sent, "retried": self.retried, "quarantined": self.quarantined, "ambiguous": self.ambiguous}


class NotificationOutbox:
    """Durable notification lifecycle; it never changes workflow closure state."""
    def __init__(
        self, store: StateStore, configuration: SMTPConfiguration, runtime_instance_id: str, *,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 300.0,
        max_attempts: int = 5,
        reconciliation_seconds: float = 3600.0,
        lease_ttl_seconds: float = 60.0,
        batch_size: int = 1,
        sink: NotificationSink | None = None,
    ) -> None:
        if not runtime_instance_id or retry_base_seconds <= 0 or retry_max_seconds <= 0 or max_attempts < 1 or reconciliation_seconds <= 0 or lease_ttl_seconds <= 0 or batch_size < 1:
            raise ValueError("notification delivery settings are invalid")
        self._store = store
        self._configuration = configuration
        self._runtime_instance_id = runtime_instance_id
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._max_attempts = max_attempts
        self._reconciliation_seconds = reconciliation_seconds
        self._lease_ttl_seconds, self._batch_size = lease_ttl_seconds, batch_size
        self._sink = sink or SMTPNotificationSink(configuration)

    @property
    def enabled(self) -> bool:
        return self._configuration.enabled

    def enqueue_terminal(self, event: TerminalStopEvent) -> bool:
        """Idempotently queue only a completed, eligible terminal closure."""
        if not self.enabled or not event.notification_eligible or not event.closure_id:
            return False
        envelope = NotificationEnvelope(
            canvas_id=event.canvas_id,
            trigger_id=event.trigger_id,
            closure_id=event.closure_id,
            round_index=event.round_index,
            reason=event.reason.value,
        )
        self._store.enqueue_notification(envelope)
        return True

    def drain(self) -> NotificationDrainCounts:
        """Lease and settle due rows; ambiguous deliveries are never resent."""
        if not self.enabled:
            return NotificationDrainCounts()
        counts = NotificationDrainCounts()
        self._store.expire_stale_notifications()
        for logical_key in self._store.list_due_notification_keys(limit=self._batch_size):
            leased = self._store.lease_due_notification(
                logical_key,
                lease_owner=self._runtime_instance_id,
                lease_ttl_seconds=self._lease_ttl_seconds,
                reconciliation_window_seconds=self._reconciliation_seconds,
            )
            if leased is not None:
                counts = self._deliver(leased, counts)
        if counts.total:
            log.info("notification_outbox_drained", **counts.fields)
        return counts

    def _deliver(
        self, record: NotificationOutboxRecord, counts: NotificationDrainCounts
    ) -> NotificationDrainCounts:
        envelope = _envelope_from(record)
        if envelope is None:
            self._store.quarantine_notification(
                record.logical_key,
                self._runtime_instance_id,
                record.lease_generation,
                FailureCategory.IDEMPOTENCY_CONFLICT,
            )
            log.warning("notification_metadata_invalid", failure_category=FailureCategory.IDEMPOTENCY_CONFLICT.value)
            return replace(counts, quarantined=counts.quarantined + 1)
        result = self._send(envelope)
        try:
            return self._settle(record, record.lease_generation, result, counts)
        except StaleNotificationLeaseError:
            log.info("notification_lease_lost")
            return counts

    def _send(self, envelope: NotificationEnvelope) -> SendResult | None:
        try:
            return self._sink.send(envelope)
        except Exception:  # The sink boundary must not change a closed workflow.
            log.warning("notification_sink_failure", failure_category=FailureCategory.SMTP_TRANSIENT.value)
            return None

    def _settle(self, record: NotificationOutboxRecord, generation: int, result: SendResult | None, counts: NotificationDrainCounts) -> NotificationDrainCounts:
        key = record.logical_key
        if result is not None and result.disposition is SendDisposition.ACCEPTED:
            self._store.mark_notification_sent(key, self._runtime_instance_id, generation)
            return replace(counts, sent=counts.sent + 1)
        if result is not None and result.disposition is SendDisposition.AMBIGUOUS:
            self._store.mark_notification_ambiguous(
                key, self._runtime_instance_id, generation, self._reconciliation_seconds
            )
            return replace(counts, ambiguous=counts.ambiguous + 1)
        if result is not None and result.disposition is SendDisposition.REJECTED:
            self._store.quarantine_notification(
                key, self._runtime_instance_id, generation, FailureCategory.SMTP_REJECTED
            )
            return replace(counts, quarantined=counts.quarantined + 1)
        retried = self._store.retry_notification(
            key,
            self._runtime_instance_id,
            generation,
            base_seconds=self._retry_base_seconds,
            max_seconds=self._retry_max_seconds,
            max_attempts=self._max_attempts,
        )
        if retried.status is NotificationStatus.QUARANTINED:
            return replace(counts, quarantined=counts.quarantined + 1)
        return replace(counts, retried=counts.retried + 1)


def _envelope_from(record: NotificationOutboxRecord) -> NotificationEnvelope | None:
    metadata = record.closure_metadata
    trigger_id = metadata.get("trigger_id")
    closure_id = metadata.get("closure_id")
    round_index = metadata.get("round_index")
    reason = metadata.get("reason")
    if not isinstance(trigger_id, str) or not isinstance(closure_id, str):
        return None
    if isinstance(round_index, bool) or not isinstance(round_index, int) or not isinstance(reason, str):
        return None
    return NotificationEnvelope(
        canvas_id=record.canvas_id,
        trigger_id=trigger_id,
        closure_id=closure_id,
        round_index=round_index,
        reason=reason,
        tenant_id=record.tenant_id,
    )


def enqueue_terminal_notification(store: StateStore, configuration: SMTPConfiguration, event: TerminalStopEvent) -> bool:
    """Keep closure integration independent from an SMTP delivery attempt."""
    try:
        return NotificationOutbox(store, configuration, "terminal-closure").enqueue_terminal(event)
    except Exception:
        log.warning("notification_enqueue_failure", failure_category=FailureCategory.IDEMPOTENCY_CONFLICT.value)
        return False


__all__ = ["NotificationDrainCounts", "NotificationOutbox", "enqueue_terminal_notification"]
