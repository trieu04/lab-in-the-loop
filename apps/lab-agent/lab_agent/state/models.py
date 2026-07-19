"""Typed records and status enums for the durable harness ledger.

Canvas remains workflow truth (docs/system-architecture.md § harness
boundary); these types describe attempts/leases/intents/edges/audit evidence
about canvas work, never a second competing state machine.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: Injected wall-clock source, epoch seconds. Production uses ``time.time``;
#: tests inject a deterministic fake so lease/backoff/audit timing is exact.
Clock = Callable[[], float]

#: Injected uniform-random source in ``[0, 1)``. Production uses
#: ``random.random``; tests inject a deterministic fake for exact jitter.
RandomSource = Callable[[], float]


class AttemptStatus(StrEnum):
    """Lifecycle of one ``workflow_attempts`` row (canvas_id, trigger_id)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class IntentStatus(StrEnum):
    """Lifecycle of one ``side_effect_intents`` row (idempotency_key)."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    EXECUTED = "executed"
    RECONCILED = "reconciled"
    FAILED = "failed"


@dataclass(frozen=True)
class WorkflowAttempt:
    """A durable record of one trigger's execution attempts on a canvas."""

    canvas_id: str
    trigger_id: str
    status: AttemptStatus
    attempt_count: int
    lease_owner: str | None
    lease_expires_at: float | None
    next_retry_at: float | None
    last_error: str | None
    created_at: float
    updated_at: float
    completed_at: float | None


@dataclass(frozen=True)
class CanvasLease:
    """A single-writer lease held by one runtime instance for one canvas."""

    canvas_id: str
    runtime_instance_id: str
    acquired_at: float
    expires_at: float


@dataclass(frozen=True)
class SideEffectIntent:
    """A durable record of one canvas/provider mutation, keyed for replay-safety."""

    idempotency_key: str
    canvas_id: str
    kind: str
    input_hash: str
    status: IntentStatus
    external_id: str | None
    attempt_count: int
    next_retry_at: float | None
    last_error: str | None
    created_at: float
    updated_at: float
    reconciled_at: float | None


@dataclass(frozen=True)
class OrchestratorEdge:
    """A recorded connector the orchestrator drew, for loop-graph bookkeeping."""

    canvas_id: str
    connector_id: str
    kind: str
    round: int
    created_at: float


@dataclass(frozen=True)
class AuditEvent:
    """One append-only, hash-chained safety/audit record."""

    id: int
    sequence: int
    canvas_id: str
    round: int | None
    event: str
    payload: dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: float


@dataclass(frozen=True)
class ArtifactRecord:
    """A durable ``artifacts`` row -- the current-version pointer for one
    generated artifact. Full version history lives in
    :class:`ArtifactVersionRecord`; this row never carries payload content."""

    opaque_id: str
    canvas_id: str
    idempotency_key: str
    artifact_type: str
    state: str
    round: int
    current_version: int
    content_hash: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class ArtifactVersionRecord:
    """One immutable, append-only ``artifact_versions`` row."""

    opaque_id: str
    version: int
    payload: dict[str, Any]
    metadata: dict[str, Any]
    provenance: dict[str, Any]
    content_hash: str
    created_at: float


@dataclass(frozen=True)
class ArtifactWidgetMapping:
    """The one Browser widget id mapped to a generated artifact."""

    opaque_id: str
    canvas_id: str
    widget_id: str
    created_at: float
    updated_at: float


__all__ = [
    "ArtifactRecord",
    "ArtifactVersionRecord",
    "ArtifactWidgetMapping",
    "AttemptStatus",
    "AuditEvent",
    "CanvasLease",
    "Clock",
    "IntentStatus",
    "OrchestratorEdge",
    "RandomSource",
    "SideEffectIntent",
    "WorkflowAttempt",
]
