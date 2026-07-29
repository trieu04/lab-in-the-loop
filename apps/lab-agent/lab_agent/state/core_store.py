"""Tenant-scoped facade for attempt lifecycle and canvas writer leases."""

from __future__ import annotations

import sqlite3

from lab_agent.state import attempts, attempts_retry, leases
from lab_agent.state.attempts_retry import AttemptNotQuarantinedError
from lab_agent.state.models import CanvasLease, Clock, RandomSource, WorkflowAttempt


class CoreStoreMixin:
    """Expose canonical tenant/canvas state paths without duplicating scope guards."""

    conn: sqlite3.Connection
    clock: Clock
    rng: RandomSource
    tenant_id: str

    def _require_canvas_scope(self, canvas_id: str) -> None: ...

    def ensure_attempt(self, canvas_id: str, trigger_id: str) -> WorkflowAttempt:
        self._require_canvas_scope(canvas_id)
        return attempts.ensure_attempt(
            self.conn, clock=self.clock, tenant_id=self.tenant_id,
            canvas_id=canvas_id, trigger_id=trigger_id,
        )

    def get_attempt(self, canvas_id: str, trigger_id: str) -> WorkflowAttempt | None:
        self._require_canvas_scope(canvas_id)
        return attempts.get_attempt(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, trigger_id=trigger_id,
        )

    def lease_due_attempt(
        self, canvas_id: str, trigger_id: str, *, lease_owner: str, lease_ttl_seconds: float,
    ) -> WorkflowAttempt | None:
        self._require_canvas_scope(canvas_id)
        return attempts.lease_due_attempt(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            trigger_id=trigger_id, lease_owner=lease_owner, lease_ttl_seconds=lease_ttl_seconds,
        )

    def mark_attempt_completed(
        self, canvas_id: str, trigger_id: str, *, lease_owner: str,
    ) -> bool:
        self._require_canvas_scope(canvas_id)
        return attempts.mark_completed(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            trigger_id=trigger_id, lease_owner=lease_owner,
        )

    def acquire_canvas_lease(
        self, canvas_id: str, *, runtime_instance_id: str, ttl_seconds: float,
    ) -> CanvasLease:
        self._require_canvas_scope(canvas_id)
        return leases.acquire_or_renew_lease(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            runtime_instance_id=runtime_instance_id, ttl_seconds=ttl_seconds,
        )

    def release_canvas_lease(self, canvas_id: str, *, runtime_instance_id: str) -> bool:
        self._require_canvas_scope(canvas_id)
        return leases.release_lease(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id,
            runtime_instance_id=runtime_instance_id,
        )

    def get_canvas_lease(self, canvas_id: str) -> CanvasLease | None:
        self._require_canvas_scope(canvas_id)
        return leases.get_lease(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id)

    def mark_attempt_failed(
        self, canvas_id: str, trigger_id: str, *, lease_owner: str, error: str,
        base_seconds: float, max_seconds: float, max_attempts: int,
    ) -> WorkflowAttempt:
        self._require_canvas_scope(canvas_id)
        return attempts_retry.mark_failed(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id, trigger_id=trigger_id,
            lease_owner=lease_owner, error=error, base_seconds=base_seconds,
            max_seconds=max_seconds, max_attempts=max_attempts, rng=self.rng,
        )

    def reset_quarantined_attempt(self, canvas_id: str, trigger_id: str) -> WorkflowAttempt:
        self._require_canvas_scope(canvas_id)
        return attempts_retry.reset_quarantined(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id, trigger_id=trigger_id,
        )

    def list_quarantined_attempts(self, canvas_id: str | None = None) -> list[WorkflowAttempt]:
        if canvas_id is not None:
            self._require_canvas_scope(canvas_id)
        return attempts_retry.list_quarantined(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id,
        )


__all__ = ["AttemptNotQuarantinedError", "CoreStoreMixin"]
