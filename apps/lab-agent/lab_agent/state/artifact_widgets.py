"""Tenant/canvas-qualified Browser widget mappings for artifacts."""

from __future__ import annotations

import sqlite3

from lab_agent.state.artifacts import require_owned_artifact
from lab_agent.state.models import ArtifactWidgetMapping, Clock


class ArtifactWidgetConflictError(RuntimeError):
    """A Browser widget mapping would change or collide in its scope."""


def _row_to_mapping(row: sqlite3.Row) -> ArtifactWidgetMapping:
    return ArtifactWidgetMapping(
        tenant_id=row["tenant_id"], opaque_id=row["opaque_id"], canvas_id=row["canvas_id"],
        widget_id=row["widget_id"], created_at=row["created_at"], updated_at=row["updated_at"],
    )


def get_mapping(
    conn: sqlite3.Connection, *, tenant_id: str, opaque_id: str
) -> ArtifactWidgetMapping | None:
    row = conn.execute(
        "SELECT * FROM artifact_widgets WHERE tenant_id=? AND opaque_id=?", (tenant_id, opaque_id)
    ).fetchone()
    return _row_to_mapping(row) if row is not None else None


def get_by_widget(
    conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, widget_id: str
) -> ArtifactWidgetMapping | None:
    row = conn.execute(
        "SELECT * FROM artifact_widgets WHERE tenant_id=? AND canvas_id=? AND widget_id=?",
        (tenant_id, canvas_id, widget_id),
    ).fetchone()
    return _row_to_mapping(row) if row is not None else None


def map_widget(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, opaque_id: str, canvas_id: str,
    widget_id: str,
) -> ArtifactWidgetMapping:
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        require_owned_artifact(
            conn, tenant_id=tenant_id, opaque_id=opaque_id, canvas_id=canvas_id
        )
        existing = get_mapping(conn, tenant_id=tenant_id, opaque_id=opaque_id)
        if existing is not None:
            if existing.widget_id == widget_id:
                conn.execute("COMMIT")
                return existing
            raise ArtifactWidgetConflictError(
                f"artifact {opaque_id!r} is already mapped to widget {existing.widget_id!r}"
            )
        conflict = get_by_widget(
            conn, tenant_id=tenant_id, canvas_id=canvas_id, widget_id=widget_id
        )
        if conflict is not None:
            raise ArtifactWidgetConflictError(
                f"widget {widget_id!r} is already mapped to artifact {conflict.opaque_id!r}"
            )
        conn.execute(
            "INSERT INTO artifact_widgets (tenant_id, opaque_id, canvas_id, widget_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, opaque_id, canvas_id, widget_id, now, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    mapping = get_mapping(conn, tenant_id=tenant_id, opaque_id=opaque_id)
    if mapping is None:  # pragma: no cover
        raise RuntimeError(f"widget mapping for {opaque_id} missing immediately after map")
    return mapping


__all__ = ["ArtifactWidgetConflictError", "get_by_widget", "get_mapping", "map_widget"]
