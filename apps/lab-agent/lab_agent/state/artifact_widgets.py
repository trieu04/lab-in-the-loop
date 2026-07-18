"""``artifact_widgets``: the one Browser-widget mapping per artifact.

Canvas-scoped uniqueness: a given ``(canvas_id, widget_id)`` pair maps to at
most one artifact, and each artifact maps to at most one widget. Mapping the
same artifact to the same widget id again is a no-op -- restart reconciliation
must be idempotent; mapping it to a *different* widget, or claiming a widget
id another artifact in the same canvas already owns, is a conflict.
"""

from __future__ import annotations

import sqlite3

from lab_agent.state.artifacts import require_owned_artifact
from lab_agent.state.models import ArtifactWidgetMapping, Clock


class ArtifactWidgetConflictError(RuntimeError):
    """Raised when a widget-id mapping would silently change or collide with
    another artifact's mapping."""


def _row_to_mapping(row: sqlite3.Row) -> ArtifactWidgetMapping:
    return ArtifactWidgetMapping(
        opaque_id=row["opaque_id"],
        canvas_id=row["canvas_id"],
        widget_id=row["widget_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_mapping(conn: sqlite3.Connection, *, opaque_id: str) -> ArtifactWidgetMapping | None:
    row = conn.execute("SELECT * FROM artifact_widgets WHERE opaque_id=?", (opaque_id,)).fetchone()
    return _row_to_mapping(row) if row is not None else None


def get_by_widget(conn: sqlite3.Connection, *, canvas_id: str, widget_id: str) -> ArtifactWidgetMapping | None:
    row = conn.execute(
        "SELECT * FROM artifact_widgets WHERE canvas_id=? AND widget_id=?", (canvas_id, widget_id)
    ).fetchone()
    return _row_to_mapping(row) if row is not None else None


def map_widget(
    conn: sqlite3.Connection, *, clock: Clock, opaque_id: str, canvas_id: str, widget_id: str
) -> ArtifactWidgetMapping:
    """Idempotently map ``opaque_id`` to ``widget_id`` within ``canvas_id``.

    Re-mapping to the identical widget id is a no-op (restart-safe
    reconciliation). Raises :class:`ArtifactWidgetConflictError` if this
    artifact is already mapped to a *different* widget, or if another
    artifact in the same canvas already owns ``widget_id``. Raises
    :class:`~lab_agent.state.artifacts.ArtifactNotFoundError`/
    :class:`~lab_agent.state.artifacts.ArtifactCanvasScopeError` via
    :func:`~lab_agent.state.artifacts.require_owned_artifact`.
    """
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        require_owned_artifact(conn, opaque_id=opaque_id, canvas_id=canvas_id)
        existing = conn.execute("SELECT * FROM artifact_widgets WHERE opaque_id=?", (opaque_id,)).fetchone()
        if existing is not None:
            if existing["widget_id"] == widget_id:
                conn.execute("COMMIT")
                return _row_to_mapping(existing)
            # No manual ROLLBACK here -- see the matching comment in
            # state/intents.py::prepare_intent: raising lets the single
            # `except Exception` below roll back exactly once.
            raise ArtifactWidgetConflictError(
                f"artifact {opaque_id!r} is already mapped to widget {existing['widget_id']!r}"
            )
        conflict = conn.execute(
            "SELECT opaque_id FROM artifact_widgets WHERE canvas_id=? AND widget_id=?", (canvas_id, widget_id)
        ).fetchone()
        if conflict is not None:
            raise ArtifactWidgetConflictError(
                f"widget {widget_id!r} is already mapped to artifact {conflict['opaque_id']!r} "
                f"in canvas {canvas_id!r}"
            )
        conn.execute(
            "INSERT INTO artifact_widgets (opaque_id, canvas_id, widget_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (opaque_id, canvas_id, widget_id, now, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    mapping = get_mapping(conn, opaque_id=opaque_id)
    if mapping is None:  # pragma: no cover - just inserted above
        raise RuntimeError(f"widget mapping for {opaque_id} missing immediately after map_widget")
    return mapping


__all__ = [
    "ArtifactWidgetConflictError",
    "get_by_widget",
    "get_mapping",
    "map_widget",
]
