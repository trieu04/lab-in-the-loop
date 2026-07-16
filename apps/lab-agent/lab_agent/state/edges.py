"""``orchestrator_edges``: bookkeeping for connectors the orchestrator drew.

Pure record-keeping (loop round/kind per connector), not a safety-critical
outbox -- re-recording the same ``(canvas_id, connector_id)`` updates
``kind``/``round`` in place.
"""

from __future__ import annotations

import sqlite3

from lab_agent.state.models import Clock, OrchestratorEdge


def _row_to_edge(row: sqlite3.Row) -> OrchestratorEdge:
    return OrchestratorEdge(
        canvas_id=row["canvas_id"],
        connector_id=row["connector_id"],
        kind=row["kind"],
        round=row["round"],
        created_at=row["created_at"],
    )


def record_edge(
    conn: sqlite3.Connection, *, clock: Clock, canvas_id: str, connector_id: str, kind: str, round: int
) -> OrchestratorEdge:
    """Upsert one orchestrator-drawn connector's kind/round."""
    now = clock()
    conn.execute(
        "INSERT INTO orchestrator_edges (canvas_id, connector_id, kind, round, created_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(canvas_id, connector_id) DO UPDATE SET kind=excluded.kind, round=excluded.round",
        (canvas_id, connector_id, kind, round, now),
    )
    edge = get_edge(conn, canvas_id=canvas_id, connector_id=connector_id)
    if edge is None:  # pragma: no cover - just upserted above
        raise RuntimeError(f"edge {canvas_id}/{connector_id} missing immediately after record")
    return edge


def get_edge(conn: sqlite3.Connection, *, canvas_id: str, connector_id: str) -> OrchestratorEdge | None:
    row = conn.execute(
        "SELECT * FROM orchestrator_edges WHERE canvas_id=? AND connector_id=?",
        (canvas_id, connector_id),
    ).fetchone()
    return _row_to_edge(row) if row is not None else None


def list_edges(conn: sqlite3.Connection, *, canvas_id: str) -> list[OrchestratorEdge]:
    rows = conn.execute(
        "SELECT * FROM orchestrator_edges WHERE canvas_id=? ORDER BY round, connector_id", (canvas_id,)
    ).fetchall()
    return [_row_to_edge(row) for row in rows]


__all__ = ["get_edge", "list_edges", "record_edge"]
