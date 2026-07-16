"""``workflow_attempts`` failure path: durable backoff, quarantine, operator reset.

Split out from :mod:`lab_agent.state.attempts` (which owns create/lease/
complete) purely to stay under the per-module line budget -- both modules
operate on the same ``workflow_attempts`` table and rows.
"""

from __future__ import annotations

import sqlite3

from lab_agent.state.attempts import _row_to_attempt, get_attempt
from lab_agent.state.models import Clock, RandomSource, WorkflowAttempt


class StaleLeaseError(RuntimeError):
    """Raised when failing an attempt whose lease this caller no longer holds
    (reclaimed by another owner after expiry) -- the caller must not clobber
    newer work with a stale outcome."""


class AttemptNotQuarantinedError(RuntimeError):
    """Raised when an operator reset targets an attempt that is not
    currently quarantined."""


def compute_backoff(attempt_count: int, *, base_seconds: float, max_seconds: float, rng: RandomSource) -> float:
    """Full-jitter exponential backoff: ``uniform(0, min(max, base * 2**(n-1)))``.

    ``attempt_count`` is the number of attempts made so far (>= 1). Full jitter
    (AWS architecture-blog strategy) avoids retry storms better than capped
    backoff with a small additive jitter.
    """
    ceiling = min(max_seconds, base_seconds * (2 ** max(attempt_count - 1, 0)))
    return rng() * ceiling


def mark_failed(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    canvas_id: str,
    trigger_id: str,
    lease_owner: str,
    error: str,
    base_seconds: float,
    max_seconds: float,
    max_attempts: int,
    rng: RandomSource,
) -> WorkflowAttempt:
    """Record a failed attempt still leased by ``lease_owner``.

    Schedules a backoff retry, or quarantines once ``attempt_count`` reaches
    ``max_attempts``. Raises :class:`StaleLeaseError` if the lease was
    reclaimed by a different owner (caller must not report a stale outcome).
    """
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM workflow_attempts WHERE canvas_id=? AND trigger_id=? "
            "AND lease_owner=? AND status='running'",
            (canvas_id, trigger_id, lease_owner),
        ).fetchone()
        if row is None:
            # No manual ROLLBACK here -- see the matching comment in
            # state/intents.py::prepare_intent: raising lets the single
            # `except Exception` below roll back exactly once.
            raise StaleLeaseError(f"attempt {canvas_id}/{trigger_id} not leased by {lease_owner!r}")
        if row["attempt_count"] >= max_attempts:
            conn.execute(
                "UPDATE workflow_attempts SET status='quarantined', last_error=?, lease_owner=NULL, "
                "lease_expires_at=NULL, next_retry_at=NULL, updated_at=? "
                "WHERE canvas_id=? AND trigger_id=?",
                (error, now, canvas_id, trigger_id),
            )
        else:
            delay = compute_backoff(row["attempt_count"], base_seconds=base_seconds, max_seconds=max_seconds, rng=rng)
            conn.execute(
                "UPDATE workflow_attempts SET status='failed', last_error=?, lease_owner=NULL, "
                "lease_expires_at=NULL, next_retry_at=?, updated_at=? "
                "WHERE canvas_id=? AND trigger_id=?",
                (error, now + delay, now, canvas_id, trigger_id),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    attempt = get_attempt(conn, canvas_id=canvas_id, trigger_id=trigger_id)
    if attempt is None:  # pragma: no cover - row was just updated above
        raise RuntimeError(f"attempt {canvas_id}/{trigger_id} missing immediately after mark_failed")
    return attempt


def reset_quarantined(conn: sqlite3.Connection, *, clock: Clock, canvas_id: str, trigger_id: str) -> WorkflowAttempt:
    """Operator command: return a quarantined attempt to ``pending`` with a
    clean slate. Raises :class:`AttemptNotQuarantinedError` if it is not
    currently quarantined (explicit action, must not silently no-op)."""
    now = clock()
    cur = conn.execute(
        "UPDATE workflow_attempts SET status='pending', attempt_count=0, last_error=NULL, "
        "next_retry_at=NULL, lease_owner=NULL, lease_expires_at=NULL, updated_at=? "
        "WHERE canvas_id=? AND trigger_id=? AND status='quarantined'",
        (now, canvas_id, trigger_id),
    )
    if cur.rowcount == 0:
        raise AttemptNotQuarantinedError(f"attempt {canvas_id}/{trigger_id} is not quarantined")
    attempt = get_attempt(conn, canvas_id=canvas_id, trigger_id=trigger_id)
    if attempt is None:  # pragma: no cover - rowcount>0 guarantees existence
        raise RuntimeError(f"attempt {canvas_id}/{trigger_id} missing immediately after reset")
    return attempt


def list_quarantined(conn: sqlite3.Connection, *, canvas_id: str | None = None) -> list[WorkflowAttempt]:
    """Operator visibility: every quarantined attempt, optionally scoped to one canvas."""
    if canvas_id is None:
        rows = conn.execute(
            "SELECT * FROM workflow_attempts WHERE status='quarantined' ORDER BY canvas_id, trigger_id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM workflow_attempts WHERE status='quarantined' AND canvas_id=? ORDER BY trigger_id",
            (canvas_id,),
        ).fetchall()
    return [_row_to_attempt(row) for row in rows]


__all__ = [
    "AttemptNotQuarantinedError",
    "StaleLeaseError",
    "compute_backoff",
    "list_quarantined",
    "mark_failed",
    "reset_quarantined",
]
