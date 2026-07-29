"""``workflow_attempts`` core lifecycle: create, lease, complete."""

from __future__ import annotations

import sqlite3

from lab_agent.state.models import AttemptStatus, Clock, WorkflowAttempt


def _row_to_attempt(row: sqlite3.Row) -> WorkflowAttempt:
    return WorkflowAttempt(
        canvas_id=row["canvas_id"], trigger_id=row["trigger_id"],
        status=AttemptStatus(row["status"]), attempt_count=row["attempt_count"],
        lease_owner=row["lease_owner"], lease_expires_at=row["lease_expires_at"],
        next_retry_at=row["next_retry_at"], last_error=row["last_error"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        completed_at=row["completed_at"], tenant_id=row["tenant_id"],
    )


def get_attempt(
    conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, trigger_id: str,
) -> WorkflowAttempt | None:
    row = conn.execute(
        "SELECT * FROM workflow_attempts WHERE tenant_id=? AND canvas_id=? AND trigger_id=?",
        (tenant_id, canvas_id, trigger_id),
    ).fetchone()
    return _row_to_attempt(row) if row is not None else None


def ensure_attempt(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, canvas_id: str, trigger_id: str,
) -> WorkflowAttempt:
    now = clock()
    conn.execute(
        "INSERT OR IGNORE INTO workflow_attempts "
        "(tenant_id, canvas_id, trigger_id, status, attempt_count, created_at, updated_at) "
        "VALUES (?, ?, ?, 'pending', 0, ?, ?)",
        (tenant_id, canvas_id, trigger_id, now, now),
    )
    attempt = get_attempt(
        conn, tenant_id=tenant_id, canvas_id=canvas_id, trigger_id=trigger_id,
    )
    if attempt is None:  # pragma: no cover - INSERT OR IGNORE guarantees a row
        raise RuntimeError(f"attempt {canvas_id}/{trigger_id} missing after ensure")
    return attempt


def _is_due(row: sqlite3.Row, now: float) -> bool:
    status = row["status"]
    if status == AttemptStatus.PENDING.value:
        return True
    if status == AttemptStatus.FAILED.value:
        return row["next_retry_at"] is None or row["next_retry_at"] <= now
    return status == AttemptStatus.RUNNING.value and row["lease_expires_at"] is not None and row["lease_expires_at"] <= now


def lease_due_attempt(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    tenant_id: str,
    canvas_id: str,
    trigger_id: str,
    lease_owner: str,
    lease_ttl_seconds: float,
) -> WorkflowAttempt | None:
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM workflow_attempts WHERE tenant_id=? AND canvas_id=? AND trigger_id=?",
            (tenant_id, canvas_id, trigger_id),
        ).fetchone()
        if row is None or not _is_due(row, now):
            conn.execute("ROLLBACK")
            return None
        conn.execute(
            "UPDATE workflow_attempts SET status='running', lease_owner=?, lease_expires_at=?, "
            "attempt_count=attempt_count+1, updated_at=? "
            "WHERE tenant_id=? AND canvas_id=? AND trigger_id=?",
            (lease_owner, now + lease_ttl_seconds, now, tenant_id, canvas_id, trigger_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get_attempt(conn, tenant_id=tenant_id, canvas_id=canvas_id, trigger_id=trigger_id)


def mark_completed(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, canvas_id: str,
    trigger_id: str, lease_owner: str,
) -> bool:
    now = clock()
    changed = conn.execute(
        "UPDATE workflow_attempts SET status='completed', completed_at=?, lease_owner=NULL, "
        "lease_expires_at=NULL, updated_at=? WHERE tenant_id=? AND canvas_id=? "
        "AND trigger_id=? AND lease_owner=? AND status='running'",
        (now, now, tenant_id, canvas_id, trigger_id, lease_owner),
    )
    return changed.rowcount > 0


__all__ = ["ensure_attempt", "get_attempt", "lease_due_attempt", "mark_completed"]
