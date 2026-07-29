"""``canvas_leases``: one live writer per tenant/canvas pair."""

from __future__ import annotations

import sqlite3

from lab_agent.state.models import CanvasLease, Clock


class LeaseHeldByOtherError(RuntimeError):
    """A different runtime owns a still-live tenant/canvas writer lease."""


def _row_to_lease(row: sqlite3.Row) -> CanvasLease:
    return CanvasLease(
        canvas_id=row["canvas_id"], runtime_instance_id=row["runtime_instance_id"],
        acquired_at=row["acquired_at"], expires_at=row["expires_at"],
        tenant_id=row["tenant_id"],
    )


def get_lease(
    conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str,
) -> CanvasLease | None:
    row = conn.execute(
        "SELECT * FROM canvas_leases WHERE tenant_id=? AND canvas_id=?",
        (tenant_id, canvas_id),
    ).fetchone()
    return _row_to_lease(row) if row is not None else None


def acquire_or_renew_lease(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, canvas_id: str,
    runtime_instance_id: str, ttl_seconds: float,
) -> CanvasLease:
    """Acquire or renew a writer lease without crossing tenant scope."""
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM canvas_leases WHERE tenant_id=? AND canvas_id=?",
            (tenant_id, canvas_id),
        ).fetchone()
        if row is not None and row["runtime_instance_id"] != runtime_instance_id and row["expires_at"] > now:
            raise LeaseHeldByOtherError(
                f"canvas {canvas_id} leased by {row['runtime_instance_id']!r} until {row['expires_at']}"
            )
        expires_at = now + ttl_seconds
        conn.execute(
            "INSERT INTO canvas_leases "
            "(tenant_id, canvas_id, runtime_instance_id, acquired_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(tenant_id, canvas_id) DO UPDATE SET "
            "runtime_instance_id=excluded.runtime_instance_id, "
            "acquired_at=excluded.acquired_at, expires_at=excluded.expires_at",
            (tenant_id, canvas_id, runtime_instance_id, now, expires_at),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return CanvasLease(canvas_id, runtime_instance_id, now, expires_at, tenant_id)


def release_lease(
    conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, runtime_instance_id: str,
) -> bool:
    changed = conn.execute(
        "DELETE FROM canvas_leases WHERE tenant_id=? AND canvas_id=? AND runtime_instance_id=?",
        (tenant_id, canvas_id, runtime_instance_id),
    )
    return changed.rowcount > 0


__all__ = ["LeaseHeldByOtherError", "acquire_or_renew_lease", "get_lease", "release_lease"]
