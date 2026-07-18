"""Durable SQLite (WAL) ledger for lab-agent -- the public facade.

Canvas remains workflow truth (docs/system-architecture.md § harness
boundary); this store only records restart-safe evidence about canvas work:
workflow attempts (leasing, retry/backoff, quarantine), a single-writer
canvas lease, side-effect intents (the crash-safe outbox recovery.py uses),
orchestrator edges, and an append-only hash-chained audit log.

``StateStore`` is a thin, typed delegator over ``lab_agent.state.*``; it
holds the one SQLite connection and the injected clock/rng, and does no
business logic of its own.
"""

from __future__ import annotations

import random
import sqlite3
import time
from pathlib import Path
from typing import Any

from lab_agent.state import attempts, attempts_retry, audit, connection, edges, intents, leases
from lab_agent.state.attempts_retry import AttemptNotQuarantinedError, StaleLeaseError
from lab_agent.state.audit import (
    MAX_PAYLOAD_BYTES,
    AuditChainTamperError,
    AuditPayloadTooLargeError,
    payload_size_bytes,
)
from lab_agent.state.connection import MigrationChecksumError, MigrationOrderError
from lab_agent.state.intents import IntentHashMismatchError
from lab_agent.state.leases import LeaseHeldByOtherError
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
    "AuditPayloadTooLargeError", "CanvasLease", "IntentHashMismatchError", "IntentStatus",
    "LeaseHeldByOtherError", "MigrationChecksumError", "MigrationOrderError", "OrchestratorEdge",
    "SideEffectIntent", "StaleLeaseError", "StateStore", "WorkflowAttempt", "payload_size_bytes",
]


class StateStore:
    """SQLite-backed durable ledger for one lab-agent process.

    Opens the connection and applies any un-applied migrations immediately
    (idempotent -- safe on every process startup). ``clock``/``rng`` are
    injected so lease expiry, backoff jitter, and audit timestamps are
    deterministic in tests.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: Clock = time.time,
        rng: RandomSource = random.random,
        migrations_dir: Path | None = None,
    ) -> None:
        self.clock = clock
        self.rng = rng
        self.conn: sqlite3.Connection = connection.connect(db_path)
        connection.migrate(self.conn, clock=self.clock, migrations_dir=migrations_dir)

    def close(self) -> None:
        self.conn.close()

    def integrity_check(self) -> list[str]:
        """Run ``PRAGMA integrity_check``; empty list means healthy."""
        return connection.integrity_check(self.conn)

    def backup(self, destination: str | Path) -> None:
        """Consistent hot copy via SQLite's backup API (safe to run under WAL)."""
        dest_conn = sqlite3.connect(str(destination))
        try:
            self.conn.backup(dest_conn)
        finally:
            dest_conn.close()

    # ── workflow attempts (task: durable attempts + quarantine) ──────
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

    # ── canvas writer lease (task: per-canvas writer leases) ─────────
    def acquire_canvas_lease(self, canvas_id: str, *, runtime_instance_id: str, ttl_seconds: float) -> CanvasLease:
        return leases.acquire_or_renew_lease(
            self.conn, clock=self.clock, canvas_id=canvas_id,
            runtime_instance_id=runtime_instance_id, ttl_seconds=ttl_seconds,
        )

    def release_canvas_lease(self, canvas_id: str, *, runtime_instance_id: str) -> bool:
        return leases.release_lease(self.conn, canvas_id=canvas_id, runtime_instance_id=runtime_instance_id)

    def get_canvas_lease(self, canvas_id: str) -> CanvasLease | None:
        return leases.get_lease(self.conn, canvas_id=canvas_id)

    # ── side-effect intents (task: side-effect intent recovery) ──────
    def prepare_intent(self, *, idempotency_key: str, canvas_id: str, kind: str, input_hash: str) -> SideEffectIntent:
        return intents.prepare_intent(
            self.conn, clock=self.clock, idempotency_key=idempotency_key,
            canvas_id=canvas_id, kind=kind, input_hash=input_hash,
        )

    def mark_intent_executed(self, idempotency_key: str, *, external_id: str) -> SideEffectIntent:
        return intents.mark_executed(self.conn, clock=self.clock, idempotency_key=idempotency_key, external_id=external_id)

    def mark_intent_reconciled(self, idempotency_key: str, *, external_id: str | None = None) -> SideEffectIntent:
        return intents.mark_reconciled(self.conn, clock=self.clock, idempotency_key=idempotency_key, external_id=external_id)

    def mark_intent_failed(self, idempotency_key: str, *, error: str, next_retry_at: float | None = None) -> SideEffectIntent:
        return intents.mark_failed(
            self.conn, clock=self.clock, idempotency_key=idempotency_key, error=error, next_retry_at=next_retry_at
        )

    def get_intent(self, idempotency_key: str) -> SideEffectIntent | None:
        return intents.get_intent(self.conn, idempotency_key=idempotency_key)

    def list_incomplete_intents(self, canvas_id: str | None = None) -> list[SideEffectIntent]:
        return intents.list_incomplete(self.conn, canvas_id=canvas_id)

    # ── orchestrator edges ────────────────────────────────────────────
    def record_edge(self, canvas_id: str, connector_id: str, *, kind: str, round: int) -> OrchestratorEdge:
        return edges.record_edge(
            self.conn, clock=self.clock, canvas_id=canvas_id, connector_id=connector_id, kind=kind, round=round
        )

    def get_edge(self, canvas_id: str, connector_id: str) -> OrchestratorEdge | None:
        return edges.get_edge(self.conn, canvas_id=canvas_id, connector_id=connector_id)

    def list_edges(self, canvas_id: str) -> list[OrchestratorEdge]:
        return edges.list_edges(self.conn, canvas_id=canvas_id)

    # ── audit chain (task: hashed audit event chain) ─────────────────
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
