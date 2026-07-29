"""Tenant-qualified canonical artifact and version persistence."""

from __future__ import annotations

import sqlite3
from typing import Any

from lab_agent.state.artifact_rows import _dump, _row_to_record, _row_to_version
from lab_agent.state.models import ArtifactRecord, ArtifactVersionRecord, Clock


class ArtifactNotFoundError(RuntimeError):
    """An internal mutation targeted a missing artifact."""


class ArtifactCanvasScopeError(RuntimeError):
    """An internal mutation targeted an artifact outside its tenant/canvas."""


class ArtifactAlreadyExistsError(RuntimeError):
    """Creation attempted to reuse an opaque id within a tenant."""


def get_artifact(conn: sqlite3.Connection, *, tenant_id: str, opaque_id: str) -> ArtifactRecord | None:
    row = conn.execute(
        "SELECT * FROM artifacts WHERE tenant_id=? AND opaque_id=?", (tenant_id, opaque_id)
    ).fetchone()
    return _row_to_record(row) if row is not None else None


def require_owned_artifact(
    conn: sqlite3.Connection, *, tenant_id: str, opaque_id: str, canvas_id: str
) -> ArtifactRecord:
    record = get_artifact(conn, tenant_id=tenant_id, opaque_id=opaque_id)
    if record is None:
        raise ArtifactNotFoundError(f"artifact {opaque_id!r} does not exist")
    if record.canvas_id != canvas_id:
        raise ArtifactCanvasScopeError(f"artifact {opaque_id!r} does not belong to canvas {canvas_id!r}")
    return record


def get_version(
    conn: sqlite3.Connection, *, tenant_id: str, opaque_id: str, version: int
) -> ArtifactVersionRecord | None:
    row = conn.execute(
        "SELECT * FROM artifact_versions WHERE tenant_id=? AND opaque_id=? AND version=?",
        (tenant_id, opaque_id, version),
    ).fetchone()
    return _row_to_version(row) if row is not None else None


def list_versions(conn: sqlite3.Connection, *, tenant_id: str, opaque_id: str) -> list[ArtifactVersionRecord]:
    rows = conn.execute(
        "SELECT * FROM artifact_versions WHERE tenant_id=? AND opaque_id=? ORDER BY version",
        (tenant_id, opaque_id),
    ).fetchall()
    return [_row_to_version(row) for row in rows]


def create_artifact(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, opaque_id: str, idempotency_key: str,
    canvas_id: str, artifact_type: str, state: str, round: int, payload: dict[str, Any],
    metadata: dict[str, Any], provenance: dict[str, Any], content_hash: str,
) -> ArtifactRecord:
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if get_artifact(conn, tenant_id=tenant_id, opaque_id=opaque_id) is not None:
            raise ArtifactAlreadyExistsError(f"artifact {opaque_id!r} already exists")
        conn.execute(
            "INSERT INTO artifacts (tenant_id, opaque_id, canvas_id, idempotency_key, artifact_type, "
            "state, round, current_version, content_hash, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
            (tenant_id, opaque_id, canvas_id, idempotency_key, artifact_type, state, round, content_hash, now, now),
        )
        conn.execute(
            "INSERT INTO artifact_versions (tenant_id, opaque_id, version, payload_json, metadata_json, "
            "provenance_json, content_hash, created_at) VALUES (?, ?, 1, ?, ?, ?, ?, ?)",
            (tenant_id, opaque_id, _dump(payload), _dump(metadata), _dump(provenance), content_hash, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    record = get_artifact(conn, tenant_id=tenant_id, opaque_id=opaque_id)
    if record is None:  # pragma: no cover
        raise RuntimeError(f"artifact {opaque_id} missing immediately after create")
    return record


def append_version(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, opaque_id: str, canvas_id: str,
    state: str, round: int | None, payload: dict[str, Any], metadata: dict[str, Any],
    provenance: dict[str, Any], content_hash: str,
) -> ArtifactRecord:
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        record = require_owned_artifact(conn, tenant_id=tenant_id, opaque_id=opaque_id, canvas_id=canvas_id)
        version, next_round = record.current_version + 1, record.round if round is None else round
        conn.execute(
            "INSERT INTO artifact_versions (tenant_id, opaque_id, version, payload_json, metadata_json, "
            "provenance_json, content_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, opaque_id, version, _dump(payload), _dump(metadata), _dump(provenance), content_hash, now),
        )
        conn.execute(
            "UPDATE artifacts SET state=?, round=?, current_version=?, content_hash=?, updated_at=? "
            "WHERE tenant_id=? AND opaque_id=?",
            (state, next_round, version, content_hash, now, tenant_id, opaque_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    updated = get_artifact(conn, tenant_id=tenant_id, opaque_id=opaque_id)
    if updated is None:  # pragma: no cover
        raise RuntimeError(f"artifact {opaque_id} missing immediately after append")
    return updated


__all__ = [
    "ArtifactAlreadyExistsError", "ArtifactCanvasScopeError", "ArtifactNotFoundError", "append_version",
    "create_artifact", "get_artifact", "get_version", "list_versions", "require_owned_artifact",
]
