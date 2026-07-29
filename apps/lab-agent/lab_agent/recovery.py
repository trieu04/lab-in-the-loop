"""Crash-safe side-effect execution over :class:`~lab_agent.state_store.StateStore`.

canvus-mcp has no ``update_*`` tools and its creates are not idempotent
(phase-02 scope notes), so a caller cannot assume retrying a write is safe on
its own. :func:`reconcile_or_execute` makes one logical mutation safe across a
restart at every crash boundary:

1. persist a ``side_effect_intents`` row *before* touching the canvas/provider
   (so a crash before this point simply retries from scratch next time);
2. call the caller-supplied ``live_probe`` to check whether the effect
   already exists (recovers a crash *after* a prior run's write but before it
   was reconciled -- the exact window an un-audited retry would duplicate);
3. execute the mutation only if the probe finds nothing;
4. reconcile the stored external id either way, and never mark anything
   reconciled/executed on a failure.

Deliberately has no dependency on any particular MCP client or fake -- the
probe/executor are plain injected async callables so this module stays
generic and unit-testable on its own.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from lab_agent.state import intents as intents_repo
from lab_agent.state.models import Clock, IntentStatus

#: Returns the external id of an already-live effect, or ``None`` if it does
#: not exist yet. Must not raise for "not found" -- only for real probe errors.
LiveProbe = Callable[[], Awaitable[str | None]]

#: Performs the mutation and returns its new external id.
Executor = Callable[[], Awaitable[str]]


@dataclass(frozen=True)
class ReconcileResult:
    """Outcome of :func:`reconcile_or_execute`."""

    external_id: str
    already_reconciled: bool


def idempotency_key(
    canvas_id: str, kind: str, discriminator: str, *, tenant_id: str = "default",
) -> str:
    """Stable tenant-qualified key for one logical canvas/provider mutation."""
    raw = f"{tenant_id}|{canvas_id}|{kind}|{discriminator}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def legacy_idempotency_key(canvas_id: str, kind: str, discriminator: str) -> str:
    """Return the unqualified pre-P7b key for recovery probes only."""
    raw = f"{canvas_id}|{kind}|{discriminator}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def input_hash(payload: dict[str, Any]) -> str:
    """Stable hash of a mutation's logical input, for duplicate-key detection."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def reconcile_or_execute(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    canvas_id: str,
    kind: str,
    key: str,
    payload: dict[str, Any],
    live_probe: LiveProbe,
    execute: Executor,
    tenant_id: str = "default",
) -> ReconcileResult:
    """Execute one mutation exactly once across restarts.

    ``key`` is the stable idempotency key (see :func:`idempotency_key``);
    ``payload`` is hashed with :func:`input_hash` and compared against any
    existing intent under the same key -- a mismatch raises
    :class:`~lab_agent.state.intents.IntentHashMismatchError` (fail closed,
    never an ambiguous replay).
    """
    digest = input_hash(payload)
    intent = intents_repo.prepare_intent(
        conn, clock=clock, tenant_id=tenant_id, idempotency_key=key, canvas_id=canvas_id, kind=kind, input_hash=digest
    )
    if intent.status == IntentStatus.RECONCILED:
        return ReconcileResult(external_id=intent.external_id or "", already_reconciled=True)

    try:
        existing_id = await live_probe()
    except Exception as exc:  # noqa: BLE001 - probe failure must not claim completion
        intents_repo.mark_failed(
            conn, clock=clock, tenant_id=tenant_id, canvas_id=canvas_id, idempotency_key=key,
            error=f"live_probe failed: {exc}",
        )
        raise

    if existing_id:
        intent = intents_repo.mark_reconciled(
            conn, clock=clock, tenant_id=tenant_id, canvas_id=canvas_id, idempotency_key=key, external_id=existing_id,
        )
        return ReconcileResult(external_id=existing_id, already_reconciled=True)

    try:
        external_id = await execute()
    except Exception as exc:  # noqa: BLE001 - execution failure must not claim completion
        intents_repo.mark_failed(
            conn, clock=clock, tenant_id=tenant_id, canvas_id=canvas_id, idempotency_key=key, error=str(exc),
        )
        raise

    intents_repo.mark_executed(
        conn, clock=clock, tenant_id=tenant_id, canvas_id=canvas_id, idempotency_key=key, external_id=external_id,
    )
    intents_repo.mark_reconciled(
        conn, clock=clock, tenant_id=tenant_id, canvas_id=canvas_id, idempotency_key=key, external_id=external_id,
    )
    return ReconcileResult(external_id=external_id, already_reconciled=False)


__all__ = ["Executor", "LiveProbe", "ReconcileResult", "idempotency_key", "legacy_idempotency_key", "input_hash", "reconcile_or_execute"]
