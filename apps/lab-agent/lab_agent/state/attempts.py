"""``workflow_attempts`` core lifecycle: create, lease, complete.

One row per ``(canvas_id, trigger_id)``. ``completed`` is a permanent
terminal state -- once set, the attempt is never leasable again. A ``running``
attempt whose lease has expired (crash, kill -9) becomes leasable again by a
new owner, exactly as if it were freshly failed; it never silently becomes
``completed``. Failure/backoff/quarantine live in
:mod:`lab_agent.state.attempts_retry` (kept separate to stay under the
per-module line budget).
"""

from __future__ import annotations

import sqlite3

from lab_agent.state.models import AttemptStatus, Clock, WorkflowAttempt


def _row_to_attempt(row: sqlite3.Row) -> WorkflowAttempt:
    return WorkflowAttempt(
        canvas_id=row["canvas_id"],
        trigger_id=row["trigger_id"],
        status=AttemptStatus(row["status"]),
        attempt_count=row["attempt_count"],
        lease_owner=row["lease_owner"],
        lease_expires_at=row["lease_expires_at"],
        next_retry_at=row["next_retry_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row["completed_at"],
    )


def get_attempt(conn: sqlite3.Connection, *, canvas_id: str, trigger_id: str) -> WorkflowAttempt | None:
    row = conn.execute(
        "SELECT * FROM workflow_attempts WHERE canvas_id=? AND trigger_id=?",
        (canvas_id, trigger_id),
    ).fetchone()
    return _row_to_attempt(row) if row is not None else None


def ensure_attempt(conn: sqlite3.Connection, *, clock: Clock, canvas_id: str, trigger_id: str) -> WorkflowAttempt:
    """Idempotently create a ``pending`` attempt row if none exists yet.

    A pre-existing row (whatever its status, including ``completed``) is left
    untouched -- this must never resurrect a permanently completed attempt.
    """
    now = clock()
    conn.execute(
        "INSERT OR IGNORE INTO workflow_attempts "
        "(canvas_id, trigger_id, status, attempt_count, created_at, updated_at) "
        "VALUES (?, ?, 'pending', 0, ?, ?)",
        (canvas_id, trigger_id, now, now),
    )
    attempt = get_attempt(conn, canvas_id=canvas_id, trigger_id=trigger_id)
    if attempt is None:  # pragma: no cover - INSERT OR IGNORE guarantees a row
        raise RuntimeError(f"attempt {canvas_id}/{trigger_id} missing immediately after ensure")
    return attempt


def _is_due(row: sqlite3.Row, now: float) -> bool:
    status = row["status"]
    if status == AttemptStatus.PENDING.value:
        return True
    if status == AttemptStatus.FAILED.value:
        return row["next_retry_at"] is None or row["next_retry_at"] <= now
    if status == AttemptStatus.RUNNING.value:
        return row["lease_expires_at"] is not None and row["lease_expires_at"] <= now
    return False  # completed, quarantined: never due


def lease_due_attempt(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    canvas_id: str,
    trigger_id: str,
    lease_owner: str,
    lease_ttl_seconds: float,
) -> WorkflowAttempt | None:
    """Atomically claim a due attempt for ``lease_owner``.

    "Due" means ``pending``, ``failed`` with an elapsed ``next_retry_at``, or
    ``running`` with an expired lease (a crashed prior owner). Returns
    ``None`` if the row is missing or not due (including ``completed``/
    ``quarantined``, which are never due).
    """
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM workflow_attempts WHERE canvas_id=? AND trigger_id=?",
            (canvas_id, trigger_id),
        ).fetchone()
        if row is None or not _is_due(row, now):
            conn.execute("ROLLBACK")
            return None
        conn.execute(
            "UPDATE workflow_attempts SET status='running', lease_owner=?, lease_expires_at=?, "
            "attempt_count=attempt_count+1, updated_at=? WHERE canvas_id=? AND trigger_id=?",
            (lease_owner, now + lease_ttl_seconds, now, canvas_id, trigger_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get_attempt(conn, canvas_id=canvas_id, trigger_id=trigger_id)


def mark_completed(conn: sqlite3.Connection, *, clock: Clock, canvas_id: str, trigger_id: str, lease_owner: str) -> bool:
    """Permanently complete an attempt still leased by ``lease_owner``.

    Returns ``False`` (does nothing) if the lease was reclaimed by a
    different owner in the meantime -- a stale completion must never clobber
    newer work.
    """
    now = clock()
    cur = conn.execute(
        "UPDATE workflow_attempts SET status='completed', completed_at=?, lease_owner=NULL, "
        "lease_expires_at=NULL, updated_at=? "
        "WHERE canvas_id=? AND trigger_id=? AND lease_owner=? AND status='running'",
        (now, now, canvas_id, trigger_id, lease_owner),
    )
    return cur.rowcount > 0


__all__ = ["ensure_attempt", "get_attempt", "lease_due_attempt", "mark_completed"]
