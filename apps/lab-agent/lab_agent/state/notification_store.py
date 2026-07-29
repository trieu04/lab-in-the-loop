"""Tenant-scoped StateStore facade for durable notification delivery."""

from __future__ import annotations

import sqlite3

from lab_agent.notifications import NotificationEnvelope
from lab_agent.state import notification_outbox
from lab_agent.state.models import Clock, RandomSource


class NotificationStoreMixin:
    """Bind notification enqueue, query, claim, and admin updates to one tenant."""

    conn: sqlite3.Connection
    clock: Clock
    rng: RandomSource
    tenant_id: str

    def _require_canvas_scope(self, canvas_id: str) -> None: ...
    def _allowed_canvas_ids(self) -> tuple[str, ...] | None: ...

    def _scoped_notification(self, logical_key: str) -> notification_outbox.NotificationOutboxRecord | None:
        record = notification_outbox.get(
            self.conn, tenant_id=self.tenant_id, logical_key=logical_key
        )
        if record is not None:
            self._require_canvas_scope(record.canvas_id)
        return record

    def enqueue_notification(self, envelope: NotificationEnvelope) -> notification_outbox.NotificationOutboxRecord:
        self._require_canvas_scope(envelope.canvas_id)
        if envelope.tenant_id not in {"default", self.tenant_id}:
            raise PermissionError("notification tenant does not match state-store tenant")
        scoped = envelope if envelope.tenant_id == self.tenant_id else NotificationEnvelope(
            canvas_id=envelope.canvas_id, trigger_id=envelope.trigger_id,
            closure_id=envelope.closure_id, round_index=envelope.round_index,
            reason=envelope.reason, tenant_id=self.tenant_id,
        )
        metadata = {
            "trigger_id": scoped.trigger_id, "closure_id": scoped.closure_id,
            "round_index": scoped.round_index, "reason": scoped.reason,
        }
        return notification_outbox.enqueue(
            self.conn, clock=self.clock, tenant_id=self.tenant_id,
            logical_key=scoped.logical_key, canvas_id=scoped.canvas_id,
            closure_metadata=metadata, message_id=scoped.message_id,
        )

    def get_notification(self, logical_key: str) -> notification_outbox.NotificationOutboxRecord | None:
        return self._scoped_notification(logical_key)

    def list_notification_records(
        self, *, canvas_id: str | None = None,
        status: notification_outbox.NotificationStatus | None = None,
    ) -> list[notification_outbox.NotificationOutboxRecord]:
        if canvas_id is not None:
            self._require_canvas_scope(canvas_id)
        return notification_outbox.list_records(
            self.conn,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            allowed_canvas_ids=self._allowed_canvas_ids() if canvas_id is None else None,
            status=status,
        )

    def lease_due_notification(self, logical_key: str, *, lease_owner: str, lease_ttl_seconds: float, reconciliation_window_seconds: float) -> notification_outbox.NotificationOutboxRecord | None:
        self._scoped_notification(logical_key)
        return notification_outbox.lease_due(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, logical_key=logical_key,
            lease_owner=lease_owner, lease_ttl_seconds=lease_ttl_seconds,
            reconciliation_window_seconds=reconciliation_window_seconds,
        )

    def lease_ambiguous_notification(self, logical_key: str, *, lease_owner: str, lease_ttl_seconds: float) -> notification_outbox.NotificationOutboxRecord | None:
        self._scoped_notification(logical_key)
        return notification_outbox.lease_ambiguous_reconciliation(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, logical_key=logical_key,
            lease_owner=lease_owner, lease_ttl_seconds=lease_ttl_seconds,
        )

    def mark_notification_sent(self, logical_key: str, lease_owner: str, generation: int) -> notification_outbox.NotificationOutboxRecord:
        self._scoped_notification(logical_key)
        return notification_outbox.mark_sent(self.conn, clock=self.clock, tenant_id=self.tenant_id, logical_key=logical_key, lease_owner=lease_owner, lease_generation=generation)

    def mark_notification_ambiguous(self, logical_key: str, lease_owner: str, generation: int, window: float) -> notification_outbox.NotificationOutboxRecord:
        self._scoped_notification(logical_key)
        return notification_outbox.mark_ambiguous(self.conn, clock=self.clock, tenant_id=self.tenant_id, logical_key=logical_key, lease_owner=lease_owner, lease_generation=generation, reconciliation_window_seconds=window)

    def retry_notification(self, logical_key: str, lease_owner: str, generation: int, *, base_seconds: float, max_seconds: float, max_attempts: int) -> notification_outbox.NotificationOutboxRecord:
        self._scoped_notification(logical_key)
        return notification_outbox.mark_transient_retry(self.conn, clock=self.clock, rng=self.rng, tenant_id=self.tenant_id, logical_key=logical_key, lease_owner=lease_owner, lease_generation=generation, base_seconds=base_seconds, max_seconds=max_seconds, max_attempts=max_attempts)

    def quarantine_notification(self, logical_key: str, lease_owner: str, generation: int, category: notification_outbox.FailureCategory) -> notification_outbox.NotificationOutboxRecord:
        self._scoped_notification(logical_key)
        return notification_outbox.quarantine(self.conn, clock=self.clock, tenant_id=self.tenant_id, logical_key=logical_key, lease_owner=lease_owner, lease_generation=generation, failure_category=category)

    def reset_quarantined_notification(self, logical_key: str) -> notification_outbox.NotificationOutboxRecord:
        self._scoped_notification(logical_key)
        return notification_outbox.reset_quarantined(self.conn, clock=self.clock, tenant_id=self.tenant_id, logical_key=logical_key)

    def expire_stale_notifications(self) -> None:
        from lab_agent.state import notification_queries

        notification_queries.expire_stale(
            self.conn,
            now=self.clock(),
            tenant_id=self.tenant_id,
            allowed_canvas_ids=self._allowed_canvas_ids(),
        )

    def list_due_notification_keys(self, *, limit: int) -> list[str]:
        from lab_agent.state import notification_queries

        return notification_queries.list_due_keys(
            self.conn,
            now=self.clock(),
            limit=limit,
            tenant_id=self.tenant_id,
            allowed_canvas_ids=self._allowed_canvas_ids(),
        )


__all__ = ["NotificationStoreMixin"]
