"""Restart-idempotent artifact creation: ``get_or_create_artifact`` and its
``(canvas_id, idempotency_key)`` lookup. Split out of
:mod:`lab_agent.state.artifacts` so that module's straightforward CRUD
functions are not obscured by this compare-and-create transaction -- same
"sibling split module, import the private helper directly" idiom used by
:mod:`lab_agent.state.attempts`/:mod:`lab_agent.state.attempts_retry`.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab_agent.state.artifact_rows import _dump, _row_to_record
from lab_agent.state.artifacts import get_artifact
from lab_agent.state.models import ArtifactRecord, Clock


class ArtifactIdempotencyConflictError(RuntimeError):
    """Raised by :func:`get_or_create_artifact` when ``idempotency_key`` is
    already bound, on this canvas, to a different ``artifact_type`` --
    reusing a key for a different kind of artifact is a caller bug, never
    silently repurposed onto the existing record."""


def get_artifact_by_idempotency_key(
    conn: sqlite3.Connection, *, canvas_id: str, idempotency_key: str
) -> ArtifactRecord | None:
    """Indexed ``(canvas_id, idempotency_key)`` lookup -- no JSON scan, backed
    by ``idx_artifacts_canvas_idempotency``."""
    row = conn.execute(
        "SELECT * FROM artifacts WHERE canvas_id=? AND idempotency_key=?", (canvas_id, idempotency_key)
    ).fetchone()
    return _row_to_record(row) if row is not None else None


def get_or_create_artifact(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    opaque_id: str,
    idempotency_key: str,
    canvas_id: str,
    artifact_type: str,
    state: str,
    round: int,
    payload: dict[str, Any],
    metadata: dict[str, Any],
    provenance: dict[str, Any],
    content_hash: str,
) -> tuple[ArtifactRecord, bool]:
    """Atomically return the existing ``(canvas_id, idempotency_key)`` artifact
    or create exactly one. Returns ``(record, created)``. A crash after this
    commits but before the caller observes success is safe to retry: the
    replayed call finds the row already committed here and returns it
    unchanged -- it never appends a version merely because create replayed.

    Raises :class:`ArtifactIdempotencyConflictError` if the key is already
    bound, on this canvas, to a different ``artifact_type``.
    """
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM artifacts WHERE canvas_id=? AND idempotency_key=?", (canvas_id, idempotency_key)
        ).fetchone()
        if row is not None:
            existing = _row_to_record(row)
            if existing.artifact_type != artifact_type:
                # No manual ROLLBACK -- see state/artifacts.py::create_artifact:
                # raising lets the single `except Exception` below roll back once.
                raise ArtifactIdempotencyConflictError(
                    f"idempotency_key {idempotency_key!r} on canvas {canvas_id!r} is bound to "
                    f"artifact_type {existing.artifact_type!r}, not {artifact_type!r}"
                )
            conn.execute("COMMIT")
            return existing, False
        conn.execute(
            "INSERT INTO artifacts "
            "(opaque_id, canvas_id, idempotency_key, artifact_type, state, round, current_version, "
            "content_hash, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (opaque_id, canvas_id, idempotency_key, artifact_type, state, round, content_hash, now, now),
        )
        conn.execute(
            "INSERT INTO artifact_versions "
            "(opaque_id, version, payload_json, metadata_json, provenance_json, content_hash, created_at) "
            "VALUES (?, 1, ?, ?, ?, ?, ?)",
            (opaque_id, _dump(payload), _dump(metadata), _dump(provenance), content_hash, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    created = get_artifact(conn, opaque_id=opaque_id)
    if created is None:  # pragma: no cover - just inserted above
        raise RuntimeError(f"artifact {opaque_id} missing immediately after get_or_create_artifact")
    return created, True


__all__ = ["ArtifactIdempotencyConflictError", "get_artifact_by_idempotency_key", "get_or_create_artifact"]
