"""Generic crash-safe intent reconciliation, audit trail, and connector helper.

Wraps :func:`lab_agent.recovery.reconcile_or_execute` with the audit-event
bookkeeping every durable canvas write needs (audit events for intent
reconciled/failed), plus a durably-idempotent connector-drawing helper reused
by every ``write_*_durable`` function in ``durable_writes.py``. Split out of
that module to keep each source file under the 200-line budget.
"""

from __future__ import annotations

from typing import Any

from lab_agent import canvas_probe, nodes
from lab_agent.mcp_client import MCPClient
from lab_agent.recovery import LiveProbe, idempotency_key, reconcile_or_execute
from lab_agent.state_store import StateStore


async def reconcile_with_audit(
    mcp: MCPClient,
    store: StateStore,
    *,
    canvas_id: str,
    kind: str,
    discriminator: str,
    payload: dict[str, Any],
    round_index: int,
    live_probe: LiveProbe,
    execute: Any,
) -> str:
    """Run :func:`reconcile_or_execute` and append the matching audit event.

    Audit payloads carry ids/hashes/reasons only -- never the note body or
    any model output (docs/code-standards.md § audit log).
    """
    store._require_canvas_scope(canvas_id)
    key = idempotency_key(canvas_id, kind, discriminator, tenant_id=store.tenant_id)
    try:
        result = await reconcile_or_execute(
            store.conn, clock=store.clock, tenant_id=store.tenant_id, canvas_id=canvas_id,
            kind=kind, key=key, payload=payload, live_probe=live_probe, execute=execute,
        )
    except Exception as exc:
        store.append_audit_event(
            canvas_id, "intent_failed",
            {"kind": kind, "key": key[:16], "error": str(exc)[:200]},
            round=round_index,
        )
        raise
    store.append_audit_event(
        canvas_id, "intent_reconciled",
        {
            "kind": kind, "key": key[:16], "external_id": result.external_id,
            "already_reconciled": result.already_reconciled,
        },
        round=round_index,
    )
    return result.external_id


async def connect_durable(
    mcp: MCPClient,
    store: StateStore,
    *,
    canvas_id: str,
    src_id: str,
    dst_id: str,
    edge_kind: str,
    round_index: int,
) -> str:
    """Draw ``src_id -> dst_id`` at most once and record it as an orchestrator edge."""
    if not src_id or not dst_id:
        return ""
    discriminator = f"connector/{edge_kind}/{src_id}->{dst_id}"

    async def probe() -> str | None:
        return await canvas_probe.probe_connector(mcp, canvas_id=canvas_id, src_id=src_id, dst_id=dst_id)

    async def execute() -> str:
        return await nodes.connect(mcp, canvas_id, src_id, dst_id)

    connector_id = await reconcile_with_audit(
        mcp, store, canvas_id=canvas_id, kind="create_connector", discriminator=discriminator,
        payload={"src_id": src_id, "dst_id": dst_id, "edge_kind": edge_kind}, round_index=round_index,
        live_probe=probe, execute=execute,
    )
    if connector_id:
        store.record_edge(canvas_id, connector_id, kind=edge_kind, round=round_index)
    return connector_id


__all__ = ["connect_durable", "reconcile_with_audit"]
