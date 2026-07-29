"""Tenant-bound facade over the durable SQLite state families."""

from __future__ import annotations

import random
import sqlite3
import time
from pathlib import Path
from typing import Any

from lab_agent.state import audit, connection, edges
from lab_agent.state.attempts_retry import AttemptNotQuarantinedError, StaleLeaseError
from lab_agent.state.audit import (
    MAX_PAYLOAD_BYTES,
    AuditChainTamperError,
    AuditPayloadTooLargeError,
    payload_size_bytes,
)
from lab_agent.state.budget_store import BudgetStoreMixin
from lab_agent.state.connection import MigrationChecksumError, MigrationOrderError
from lab_agent.state.core_store import CoreStoreMixin
from lab_agent.state.execution_store import ExecutionStoreMixin
from lab_agent.state.gate_evidence import GateEvidenceConflictError
from lab_agent.state.gate_evidence_store import GateEvidenceStoreMixin
from lab_agent.state.intent_store import IntentStoreMixin
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
from lab_agent.state.notification_store import NotificationStoreMixin
from lab_agent.state.result_generations import ResultGenerationStoreMixin
from lab_agent.state.tenant_scope import TenantScopeStoreMixin
from lab_agent.tenant import TenantContext

__all__ = [
    "MAX_PAYLOAD_BYTES", "AttemptNotQuarantinedError", "AttemptStatus",
    "AuditChainTamperError", "AuditEvent", "AuditPayloadTooLargeError", "CanvasLease",
    "GateEvidenceConflictError", "IntentHashMismatchError", "IntentStatus",
    "LeaseHeldByOtherError", "MigrationChecksumError", "MigrationOrderError",
    "OrchestratorEdge", "SideEffectIntent", "StaleLeaseError", "StateStore",
    "ExecutionStoreMixin", "WorkflowAttempt", "payload_size_bytes",
]


class StateStore(
    TenantScopeStoreMixin,
    CoreStoreMixin,
    BudgetStoreMixin,
    IntentStoreMixin,
    NotificationStoreMixin,
    GateEvidenceStoreMixin,
    ExecutionStoreMixin,
    LoopContinuationStoreMixin,
    ResultGenerationStoreMixin,
):
    """SQLite state ledger bound to an optional immutable tenant context."""

    def __init__(
        self, db_path: str | Path, *, clock: Clock = time.time,
        rng: RandomSource = random.random, migrations_dir: Path | None = None,
        tenant_context: TenantContext | None = None,
    ) -> None:
        self.clock, self.rng = clock, rng
        self._configure_tenant_scope(tenant_context)
        self.conn: sqlite3.Connection = connection.connect(db_path)
        connection.migrate(self.conn, clock=self.clock, migrations_dir=migrations_dir)

    def bind_tenant_context(self, tenant_context: TenantContext | None) -> None:
        """Compatibility no-op; store scope must have been chosen at construction."""
        if self.tenant_context != tenant_context:
            raise RuntimeError("state-store tenant scope is immutable")

    def close(self) -> None:
        self.conn.close()

    def integrity_check(self) -> list[str]:
        return connection.integrity_check(self.conn)

    def backup(self, destination: str | Path) -> None:
        destination_connection = sqlite3.connect(str(destination))
        try:
            self.conn.backup(destination_connection)
        finally:
            destination_connection.close()

    def record_edge(self, canvas_id: str, connector_id: str, *, kind: str, round: int) -> OrchestratorEdge:
        self._require_canvas_scope(canvas_id)
        return edges.record_edge(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            connector_id=connector_id, kind=kind, round=round,
        )

    def get_edge(self, canvas_id: str, connector_id: str) -> OrchestratorEdge | None:
        self._require_canvas_scope(canvas_id)
        return edges.get_edge(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, connector_id=connector_id,
        )

    def list_edges(self, canvas_id: str) -> list[OrchestratorEdge]:
        self._require_canvas_scope(canvas_id)
        return edges.list_edges(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id)

    def append_audit_event(
        self, canvas_id: str, event: str, payload: dict[str, Any], *, round: int | None = None,
    ) -> AuditEvent:
        self._require_canvas_scope(canvas_id)
        return audit.append_event(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            event=event, payload=payload, round=round,
        )

    def verify_audit_chain(self) -> None:
        """Verify the legacy prefix and this store's tenant-local v2 suffix."""
        audit.verify_chain(self.conn, tenant_id=self.tenant_id)

    def verify_all_audit_chains(self) -> None:
        """Verify every tenant's chain for an explicitly global operator runtime."""
        audit.verify_all_chains(self.conn)

    def list_audit_events(self, canvas_id: str | None = None) -> list[AuditEvent]:
        if canvas_id is not None:
            self._require_canvas_scope(canvas_id)
            return audit.list_events(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id)
        allowed = self._allowed_canvas_ids()
        if allowed is None:
            return audit.list_events(self.conn, tenant_id=self.tenant_id)
        return [event for scope in allowed for event in audit.list_events(
            self.conn, tenant_id=self.tenant_id, canvas_id=scope
        )]

    def find_terminal_event(self, canvas_id: str, trigger_id: str) -> AuditEvent | None:
        self._require_canvas_scope(canvas_id)
        return audit.find_terminal_event(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, trigger_id=trigger_id,
        )
