"""``canvas_leases``: a single live writer per canvas.

A runtime instance id is generated per process (never reusable configuration
-- see phase-02 scope notes), so two processes sharing a config file still
cannot share a lease by accident. Acquiring/renewing is the same call: a
live lease held by the caller's own instance id is simply extended; a live
lease held by a *different* instance id blocks.
"""

from __future__ import annotations

import sqlite3

from lab_agent.state.models import CanvasLease, Clock


class LeaseHeldByOtherError(RuntimeError):
    """Raised when a canvas lease is currently held by a different, still-live
    runtime instance."""


def _row_to_lease(row: sqlite3.Row) -> CanvasLease:
    return CanvasLease(
        canvas_id=row["canvas_id"],
        runtime_instance_id=row["runtime_instance_id"],
        acquired_at=row["acquired_at"],
        expires_at=row["expires_at"],
    )


def get_lease(conn: sqlite3.Connection, *, canvas_id: str) -> CanvasLease | None:
    row = conn.execute("SELECT * FROM canvas_leases WHERE canvas_id=?", (canvas_id,)).fetchone()
    return _row_to_lease(row) if row is not None else None


def acquire_or_renew_lease(
    conn: sqlite3.Connection, *, clock: Clock, canvas_id: str, runtime_instance_id: str, ttl_seconds: float
) -> CanvasLease:
    """Acquire a free/expired lease, or renew this instance's own live lease.

    Raises :class:`LeaseHeldByOtherError` if a different, still-live instance
    holds it -- the caller must skip this canvas rather than write.
    """
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM canvas_leases WHERE canvas_id=?", (canvas_id,)).fetchone()
        if row is not None and row["runtime_instance_id"] != runtime_instance_id and row["expires_at"] > now:
            # No manual ROLLBACK here -- see the matching comment in
            # state/intents.py::prepare_intent: raising lets the single
            # `except Exception` below roll back exactly once.
            raise LeaseHeldByOtherError(
                f"canvas {canvas_id} leased by {row['runtime_instance_id']!r} until {row['expires_at']}"
            )
        expires_at = now + ttl_seconds
        conn.execute(
            "INSERT INTO canvas_leases (canvas_id, runtime_instance_id, acquired_at, expires_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(canvas_id) DO UPDATE SET "
            "runtime_instance_id=excluded.runtime_instance_id, "
            "acquired_at=excluded.acquired_at, expires_at=excluded.expires_at",
            (canvas_id, runtime_instance_id, now, expires_at),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return CanvasLease(canvas_id=canvas_id, runtime_instance_id=runtime_instance_id, acquired_at=now, expires_at=expires_at)


def release_lease(conn: sqlite3.Connection, *, canvas_id: str, runtime_instance_id: str) -> bool:
    """Release a lease owned by ``runtime_instance_id``; a no-op (returns
    ``False``) if it is not the current owner."""
    cur = conn.execute(
        "DELETE FROM canvas_leases WHERE canvas_id=? AND runtime_instance_id=?",
        (canvas_id, runtime_instance_id),
    )
    return cur.rowcount > 0


__all__ = ["LeaseHeldByOtherError", "acquire_or_renew_lease", "get_lease", "release_lease"]
