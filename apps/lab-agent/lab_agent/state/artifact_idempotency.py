"""Restart-idempotent creation in one tenant/canvas artifact scope."""

from __future__ import annotations

import sqlite3
from typing import Any

from lab_agent.state.artifact_rows import _dump, _row_to_record
from lab_agent.state.artifacts import get_artifact
from lab_agent.state.models import ArtifactRecord, Clock


class ArtifactIdempotencyConflictError(RuntimeError):
    """An idempotency key names another artifact type in its tenant/canvas."""


def get_artifact_by_idempotency_key(
    conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, idempotency_key: str
) -> ArtifactRecord | None:
    row = conn.execute(
        "SELECT * FROM artifacts WHERE tenant_id=? AND canvas_id=? AND idempotency_key=?",
        (tenant_id, canvas_id, idempotency_key),
    ).fetchone()
    return _row_to_record(row) if row is not None else None


def get_or_create_artifact(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, opaque_id: str, idempotency_key: str,
    canvas_id: str, artifact_type: str, state: str, round: int, payload: dict[str, Any],
    metadata: dict[str, Any], provenance: dict[str, Any], content_hash: str,
) -> tuple[ArtifactRecord, bool]:
    """Atomically return or create a tenant/canvas/idempotency-key artifact."""
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = get_artifact_by_idempotency_key(
            conn, tenant_id=tenant_id, canvas_id=canvas_id, idempotency_key=idempotency_key
        )
        if existing is not None:
            if existing.artifact_type != artifact_type:
                raise ArtifactIdempotencyConflictError(
                    f"idempotency key {idempotency_key!r} is bound to {existing.artifact_type!r}, "
                    f"not {artifact_type!r}"
                )
            conn.execute("COMMIT")
            return existing, False
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
    created = get_artifact(conn, tenant_id=tenant_id, opaque_id=opaque_id)
    if created is None:  # pragma: no cover
        raise RuntimeError(f"artifact {opaque_id} missing immediately after get-or-create")
    return created, True


__all__ = ["ArtifactIdempotencyConflictError", "get_artifact_by_idempotency_key", "get_or_create_artifact"]
