"""Tenant-qualified bookkeeping for orchestrator-drawn connectors."""

from __future__ import annotations

import sqlite3

from lab_agent.state.models import Clock, OrchestratorEdge


def _tenant(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("tenant_id must be non-empty")
    return value


def _row_to_edge(row: sqlite3.Row) -> OrchestratorEdge:
    return OrchestratorEdge(
        canvas_id=row["canvas_id"], connector_id=row["connector_id"], kind=row["kind"],
        round=row["round"], created_at=row["created_at"], tenant_id=row["tenant_id"],
    )


def record_edge(
    conn: sqlite3.Connection, *, clock: Clock, canvas_id: str, connector_id: str,
    kind: str, round: int, tenant_id: str = "default",
) -> OrchestratorEdge:
    """Upsert one connector without crossing tenant/canvas identity."""
    tenant, now = _tenant(tenant_id), clock()
    conn.execute(
        "INSERT INTO orchestrator_edges (tenant_id, canvas_id, connector_id, kind, round, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(tenant_id, canvas_id, connector_id) DO UPDATE "
        "SET kind=excluded.kind, round=excluded.round",
        (tenant, canvas_id, connector_id, kind, round, now),
    )
    edge = get_edge(conn, tenant_id=tenant, canvas_id=canvas_id, connector_id=connector_id)
    if edge is None:  # pragma: no cover - just upserted above
        raise RuntimeError(f"edge {canvas_id}/{connector_id} missing immediately after record")
    return edge


def get_edge(
    conn: sqlite3.Connection, *, canvas_id: str, connector_id: str,
    tenant_id: str = "default",
) -> OrchestratorEdge | None:
    row = conn.execute(
        "SELECT * FROM orchestrator_edges WHERE tenant_id=? AND canvas_id=? AND connector_id=?",
        (_tenant(tenant_id), canvas_id, connector_id),
    ).fetchone()
    return _row_to_edge(row) if row is not None else None


def list_edges(
    conn: sqlite3.Connection, *, canvas_id: str, tenant_id: str = "default",
) -> list[OrchestratorEdge]:
    rows = conn.execute(
        "SELECT * FROM orchestrator_edges WHERE tenant_id=? AND canvas_id=? "
        "ORDER BY round, connector_id", (_tenant(tenant_id), canvas_id),
    ).fetchall()
    return [_row_to_edge(row) for row in rows]


__all__ = ["get_edge", "list_edges", "record_edge"]
