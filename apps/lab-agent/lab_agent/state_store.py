import random
import sqlite3
import time
from pathlib import Path
from typing import Any

from lab_agent.notifications import NotificationEnvelope
from lab_agent.state import (
    attempts,
    attempts_retry,
    audit,
    budget_reservations,
    connection,
    edges,
    intents,
    leases,
    notification_outbox,
)
from lab_agent.state.attempts_retry import AttemptNotQuarantinedError, StaleLeaseError
from lab_agent.state.audit import (
    MAX_PAYLOAD_BYTES,
    AuditChainTamperError,
    AuditPayloadTooLargeError,
    payload_size_bytes,
)
from lab_agent.state.connection import MigrationChecksumError, MigrationOrderError
from lab_agent.state.execution_store import ExecutionStoreMixin
from lab_agent.state.gate_evidence import GateEvidenceConflictError
from lab_agent.state.gate_evidence_store import GateEvidenceStoreMixin
from lab_agent.state.intents import IntentHashMismatchError
from lab_agent.state.leases import LeaseHeldByOtherError
from lab_agent.state.loop_continuations import LoopContinuationStoreMixin
from lab_agent.state.models import (
    AttemptStatus,
    AuditEvent,
    CanvasLease,
    Clock,
    IntentStatus,
    OrchestratorEdge,
    RandomSource,
    SideEffectIntent,
    WorkflowAttempt,
)

__all__ = [
    "MAX_PAYLOAD_BYTES",
    "AttemptNotQuarantinedError", "AttemptStatus", "AuditChainTamperError", "AuditEvent",
    "AuditPayloadTooLargeError", "CanvasLease", "GateEvidenceConflictError",
    "IntentHashMismatchError", "IntentStatus",
    "LeaseHeldByOtherError", "MigrationChecksumError", "MigrationOrderError", "OrchestratorEdge",
    "SideEffectIntent", "StaleLeaseError", "StateStore", "ExecutionStoreMixin", "WorkflowAttempt", "payload_size_bytes"]
class StateStore(GateEvidenceStoreMixin, ExecutionStoreMixin, LoopContinuationStoreMixin):
    def __init__(self, db_path: str | Path, *, clock: Clock = time.time, rng: RandomSource = random.random, migrations_dir: Path | None = None) -> None:
        self.clock, self.rng = clock, rng
        self.conn: sqlite3.Connection = connection.connect(db_path)
        connection.migrate(self.conn, clock=self.clock, migrations_dir=migrations_dir)
    def close(self) -> None:
        self.conn.close()
    def integrity_check(self) -> list[str]:
        return connection.integrity_check(self.conn)
    def backup(self, destination: str | Path) -> None:
        dest_conn = sqlite3.connect(str(destination))
        try:
            self.conn.backup(dest_conn)
        finally:
            dest_conn.close()
    def ensure_attempt(self, canvas_id: str, trigger_id: str) -> WorkflowAttempt:
        return attempts.ensure_attempt(self.conn, clock=self.clock, canvas_id=canvas_id, trigger_id=trigger_id)
    def get_attempt(self, canvas_id: str, trigger_id: str) -> WorkflowAttempt | None:
        return attempts.get_attempt(self.conn, canvas_id=canvas_id, trigger_id=trigger_id)
    def lease_due_attempt(
        self, canvas_id: str, trigger_id: str, *, lease_owner: str, lease_ttl_seconds: float
    ) -> WorkflowAttempt | None:
        return attempts.lease_due_attempt(
            self.conn, clock=self.clock, canvas_id=canvas_id, trigger_id=trigger_id,
            lease_owner=lease_owner, lease_ttl_seconds=lease_ttl_seconds,
        )
    def mark_attempt_completed(self, canvas_id: str, trigger_id: str, *, lease_owner: str) -> bool:
        return attempts.mark_completed(
            self.conn, clock=self.clock, canvas_id=canvas_id, trigger_id=trigger_id, lease_owner=lease_owner
        )
    def mark_attempt_failed(
        self,
        canvas_id: str,
        trigger_id: str,
        *,
        lease_owner: str,
        error: str,
        base_seconds: float,
        max_seconds: float,
        max_attempts: int,
    ) -> WorkflowAttempt:
        return attempts_retry.mark_failed(
            self.conn, clock=self.clock, canvas_id=canvas_id, trigger_id=trigger_id, lease_owner=lease_owner,
            error=error, base_seconds=base_seconds, max_seconds=max_seconds, max_attempts=max_attempts,
            rng=self.rng,
        )
    def reset_quarantined_attempt(self, canvas_id: str, trigger_id: str) -> WorkflowAttempt:
        return attempts_retry.reset_quarantined(self.conn, clock=self.clock, canvas_id=canvas_id, trigger_id=trigger_id)
    def list_quarantined_attempts(self, canvas_id: str | None = None) -> list[WorkflowAttempt]:
        return attempts_retry.list_quarantined(self.conn, canvas_id=canvas_id)
    def enqueue_notification(self, envelope: NotificationEnvelope) -> notification_outbox.NotificationOutboxRecord:
        metadata = {
            "trigger_id": envelope.trigger_id, "closure_id": envelope.closure_id,
            "round_index": envelope.round_index,
            "reason": envelope.reason,
        }
        return notification_outbox.enqueue(self.conn, clock=self.clock, logical_key=envelope.logical_key, canvas_id=envelope.canvas_id, closure_metadata=metadata, message_id=envelope.message_id)
    def get_notification(self, logical_key: str) -> notification_outbox.NotificationOutboxRecord | None:
        return notification_outbox.get(self.conn, logical_key=logical_key)
    def list_notification_records(self, *, canvas_id: str | None = None, status: notification_outbox.NotificationStatus | None = None) -> list[notification_outbox.NotificationOutboxRecord]:
        return notification_outbox.list_records(self.conn, canvas_id=canvas_id, status=status)
    def lease_due_notification(self, logical_key: str, *, lease_owner: str, lease_ttl_seconds: float, reconciliation_window_seconds: float) -> notification_outbox.NotificationOutboxRecord | None:
        return notification_outbox.lease_due(self.conn, clock=self.clock, logical_key=logical_key, lease_owner=lease_owner, lease_ttl_seconds=lease_ttl_seconds, reconciliation_window_seconds=reconciliation_window_seconds)
    def lease_ambiguous_notification(self, logical_key: str, *, lease_owner: str, lease_ttl_seconds: float) -> notification_outbox.NotificationOutboxRecord | None:
        return notification_outbox.lease_ambiguous_reconciliation(self.conn, clock=self.clock, logical_key=logical_key, lease_owner=lease_owner, lease_ttl_seconds=lease_ttl_seconds)
    def mark_notification_sent(self, logical_key: str, lease_owner: str, generation: int) -> notification_outbox.NotificationOutboxRecord:
        return notification_outbox.mark_sent(self.conn, clock=self.clock, logical_key=logical_key, lease_owner=lease_owner, lease_generation=generation)
    def mark_notification_ambiguous(self, logical_key: str, lease_owner: str, generation: int, window: float) -> notification_outbox.NotificationOutboxRecord:
        return notification_outbox.mark_ambiguous(self.conn, clock=self.clock, logical_key=logical_key, lease_owner=lease_owner, lease_generation=generation, reconciliation_window_seconds=window)
    def retry_notification(self, logical_key: str, lease_owner: str, generation: int, *, base_seconds: float, max_seconds: float, max_attempts: int) -> notification_outbox.NotificationOutboxRecord:
        return notification_outbox.mark_transient_retry(self.conn, clock=self.clock, rng=self.rng, logical_key=logical_key, lease_owner=lease_owner, lease_generation=generation, base_seconds=base_seconds, max_seconds=max_seconds, max_attempts=max_attempts)
    def quarantine_notification(self, logical_key: str, lease_owner: str, generation: int, category: notification_outbox.FailureCategory) -> notification_outbox.NotificationOutboxRecord:
        return notification_outbox.quarantine(self.conn, clock=self.clock, logical_key=logical_key, lease_owner=lease_owner, lease_generation=generation, failure_category=category)
    def reset_quarantined_notification(self, logical_key: str) -> notification_outbox.NotificationOutboxRecord:
        return notification_outbox.reset_quarantined(self.conn, clock=self.clock, logical_key=logical_key)
    def acquire_canvas_lease(self, canvas_id: str, *, runtime_instance_id: str, ttl_seconds: float) -> CanvasLease:
        return leases.acquire_or_renew_lease(
            self.conn, clock=self.clock, canvas_id=canvas_id,
            runtime_instance_id=runtime_instance_id, ttl_seconds=ttl_seconds,
        )
    def release_canvas_lease(self, canvas_id: str, *, runtime_instance_id: str) -> bool:
        return leases.release_lease(self.conn, canvas_id=canvas_id, runtime_instance_id=runtime_instance_id)
    def get_canvas_lease(self, canvas_id: str) -> CanvasLease | None:
        return leases.get_lease(self.conn, canvas_id=canvas_id)
    def budget_totals(self, canvas_id: str, run_id: str | None = None) -> tuple[int, float, int, float]:
        return budget_reservations.totals(self.conn, canvas_id=canvas_id, run_id=run_id)
    def reserve_budget(
        self, *, reservation_id: str, intent_key: str, canvas_id: str, run_id: str,
        tokens: int, cost_usd: float, run_token_limit: int | None, run_cost_limit: float | None,
        canvas_token_limit: int | None, canvas_cost_limit: float | None,
    ) -> budget_reservations.BudgetReservation:
        return budget_reservations.reserve(
            self.conn, clock=self.clock, reservation_id=reservation_id, intent_key=intent_key,
            canvas_id=canvas_id, run_id=run_id, tokens=tokens, cost_usd=cost_usd,
            run_token_limit=run_token_limit, run_cost_limit=run_cost_limit,
            canvas_token_limit=canvas_token_limit, canvas_cost_limit=canvas_cost_limit,
        )
    def settle_budget(
        self, reservation_id: str, *, action: str, actual_tokens: int | None = None,
        actual_cost_usd: float | None = None,
    ) -> tuple[budget_reservations.BudgetReservation, bool]:
        return budget_reservations.settle(
            self.conn, clock=self.clock, reservation_id=reservation_id, action=action,
            actual_tokens=actual_tokens, actual_cost_usd=actual_cost_usd,
        )
    def prepare_intent(self, *, idempotency_key: str, canvas_id: str, kind: str, input_hash: str) -> SideEffectIntent:
        return intents.prepare_intent(
            self.conn, clock=self.clock, idempotency_key=idempotency_key,
            canvas_id=canvas_id, kind=kind, input_hash=input_hash,
        )
    def mark_intent_submitted(self, idempotency_key: str) -> SideEffectIntent:
        return intents.mark_submitted(self.conn, clock=self.clock, idempotency_key=idempotency_key)
    def mark_intent_executed(self, idempotency_key: str, *, external_id: str) -> SideEffectIntent:
        return intents.mark_executed(self.conn, clock=self.clock, idempotency_key=idempotency_key, external_id=external_id)
    def mark_intent_ambiguous(
        self, idempotency_key: str, *, error: str, external_id: str | None
    ) -> SideEffectIntent:
        return intents.mark_ambiguous(
            self.conn, clock=self.clock, idempotency_key=idempotency_key, error=error, external_id=external_id
        )
    def mark_intent_reconciled(self, idempotency_key: str, *, external_id: str | None = None) -> SideEffectIntent:
        return intents.mark_reconciled(self.conn, clock=self.clock, idempotency_key=idempotency_key, external_id=external_id)
    def mark_intent_failed(self, idempotency_key: str, *, error: str, next_retry_at: float | None = None) -> SideEffectIntent:
        return intents.mark_failed(self.conn, clock=self.clock, idempotency_key=idempotency_key, error=error, next_retry_at=next_retry_at)
    def get_intent(self, idempotency_key: str) -> SideEffectIntent | None:
        return intents.get_intent(self.conn, idempotency_key=idempotency_key)
    def list_incomplete_intents(self, canvas_id: str | None = None) -> list[SideEffectIntent]:
        return intents.list_incomplete(self.conn, canvas_id=canvas_id)
    def record_edge(self, canvas_id: str, connector_id: str, *, kind: str, round: int) -> OrchestratorEdge:
        return edges.record_edge(
            self.conn, clock=self.clock, canvas_id=canvas_id, connector_id=connector_id, kind=kind, round=round
        )
    def get_edge(self, canvas_id: str, connector_id: str) -> OrchestratorEdge | None:
        return edges.get_edge(self.conn, canvas_id=canvas_id, connector_id=connector_id)
    def list_edges(self, canvas_id: str) -> list[OrchestratorEdge]:
        return edges.list_edges(self.conn, canvas_id=canvas_id)
    def append_audit_event(
        self, canvas_id: str, event: str, payload: dict[str, Any], *, round: int | None = None
    ) -> AuditEvent:
        return audit.append_event(
            self.conn, clock=self.clock, canvas_id=canvas_id, event=event, payload=payload, round=round
        )
    def verify_audit_chain(self) -> None:
        audit.verify_chain(self.conn)
    def list_audit_events(self, canvas_id: str | None = None) -> list[AuditEvent]:
        return audit.list_events(self.conn, canvas_id=canvas_id)
    def find_terminal_event(self, canvas_id: str, trigger_id: str) -> AuditEvent | None:
        return audit.find_terminal_event(self.conn, canvas_id=canvas_id, trigger_id=trigger_id)
