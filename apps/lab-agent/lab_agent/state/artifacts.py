"""``artifacts``/``artifact_versions``: the canonical generated-artifact ledger.

Canvus Browser widgets are a capability-protected *view* over this data
(phase-03 plan); this ledger is the source of truth for payload, metadata,
provenance, state, and version history. Versions are append-only -- updating
an artifact never rewrites or deletes a prior version row, so safety-relevant
history cannot be silently lost.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab_agent.state.artifact_rows import _dump, _row_to_record, _row_to_version
from lab_agent.state.models import ArtifactRecord, ArtifactVersionRecord, Clock


class ArtifactNotFoundError(RuntimeError):
    """Raised by an internal (orchestrator-only) mutation when ``opaque_id``
    does not exist -- the caller holds a stale/incorrect reference. Read paths
    reachable from an adversarial request instead fail closed by returning
    ``None``/``False`` (see :mod:`lab_agent.artifact_store`), never this."""


class ArtifactCanvasScopeError(RuntimeError):
    """Raised by an internal (orchestrator-only) mutation when ``canvas_id``
    does not match the artifact's recorded canvas -- an opaque id is never
    valid across canvases."""


class ArtifactAlreadyExistsError(RuntimeError):
    """Raised by :func:`create_artifact` when ``opaque_id`` is already in use
    -- creation never silently overwrites an existing artifact."""


def get_artifact(conn: sqlite3.Connection, *, opaque_id: str) -> ArtifactRecord | None:
    row = conn.execute("SELECT * FROM artifacts WHERE opaque_id=?", (opaque_id,)).fetchone()
    return _row_to_record(row) if row is not None else None


def require_owned_artifact(conn: sqlite3.Connection, *, opaque_id: str, canvas_id: str) -> ArtifactRecord:
    """Fetch-and-verify for internal mutating callers (tokens/widgets/versions):
    fails loud on a missing id or a canvas mismatch, never silently narrows scope."""
    record = get_artifact(conn, opaque_id=opaque_id)
    if record is None:
        raise ArtifactNotFoundError(f"artifact {opaque_id!r} does not exist")
    if record.canvas_id != canvas_id:
        raise ArtifactCanvasScopeError(f"artifact {opaque_id!r} does not belong to canvas {canvas_id!r}")
    return record


def get_version(conn: sqlite3.Connection, *, opaque_id: str, version: int) -> ArtifactVersionRecord | None:
    row = conn.execute(
        "SELECT * FROM artifact_versions WHERE opaque_id=? AND version=?", (opaque_id, version)
    ).fetchone()
    return _row_to_version(row) if row is not None else None


def list_versions(conn: sqlite3.Connection, *, opaque_id: str) -> list[ArtifactVersionRecord]:
    rows = conn.execute(
        "SELECT * FROM artifact_versions WHERE opaque_id=? ORDER BY version", (opaque_id,)
    ).fetchall()
    return [_row_to_version(row) for row in rows]


def create_artifact(
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
) -> ArtifactRecord:
    """Create the canonical row + version 1 in one transaction.

    Raises :class:`ArtifactAlreadyExistsError` if ``opaque_id`` is already in
    use -- creation never silently overwrites; :func:`append_version` is the
    only way to record a new version of an existing artifact. Callers that
    need restart-idempotent creation (retry-safe across a crash) should use
    :func:`get_or_create_artifact` instead.
    """
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute("SELECT 1 FROM artifacts WHERE opaque_id=?", (opaque_id,)).fetchone()
        if existing is not None:
            # No manual ROLLBACK here -- see the matching comment in
            # state/intents.py::prepare_intent: raising lets the single
            # `except Exception` below roll back exactly once.
            raise ArtifactAlreadyExistsError(f"artifact {opaque_id!r} already exists")
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
    record = get_artifact(conn, opaque_id=opaque_id)
    if record is None:  # pragma: no cover - just inserted above
        raise RuntimeError(f"artifact {opaque_id} missing immediately after create")
    return record


def append_version(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    opaque_id: str,
    canvas_id: str,
    state: str,
    round: int | None,
    payload: dict[str, Any],
    metadata: dict[str, Any],
    provenance: dict[str, Any],
    content_hash: str,
) -> ArtifactRecord:
    """Append a new immutable version and bump the ``artifacts`` pointer.

    Never rewrites or deletes a prior ``artifact_versions`` row. Raises
    :class:`ArtifactNotFoundError`/:class:`ArtifactCanvasScopeError` (see
    :func:`require_owned_artifact`).
    """
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        record = require_owned_artifact(conn, opaque_id=opaque_id, canvas_id=canvas_id)
        next_version = record.current_version + 1
        next_round = record.round if round is None else round
        conn.execute(
            "INSERT INTO artifact_versions "
            "(opaque_id, version, payload_json, metadata_json, provenance_json, content_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (opaque_id, next_version, _dump(payload), _dump(metadata), _dump(provenance), content_hash, now),
        )
        conn.execute(
            "UPDATE artifacts SET state=?, round=?, current_version=?, content_hash=?, updated_at=? "
            "WHERE opaque_id=?",
            (state, next_round, next_version, content_hash, now, opaque_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    updated = get_artifact(conn, opaque_id=opaque_id)
    if updated is None:  # pragma: no cover - just updated above
        raise RuntimeError(f"artifact {opaque_id} missing immediately after append_version")
    return updated


__all__ = [
    "ArtifactAlreadyExistsError",
    "ArtifactCanvasScopeError",
    "ArtifactNotFoundError",
    "append_version",
    "create_artifact",
    "get_artifact",
    "get_version",
    "list_versions",
    "require_owned_artifact",
]
