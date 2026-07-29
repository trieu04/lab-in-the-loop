"""``workflow_attempts`` failure path: durable backoff, quarantine, reset."""

from __future__ import annotations

import sqlite3

from lab_agent.state.attempts import _row_to_attempt, get_attempt
from lab_agent.state.models import Clock, RandomSource, WorkflowAttempt


class StaleLeaseError(RuntimeError):
    """The caller no longer owns the claimed attempt lease."""


class AttemptNotQuarantinedError(RuntimeError):
    """An operator reset target was not quarantined."""


def compute_backoff(
    attempt_count: int, *, base_seconds: float, max_seconds: float, rng: RandomSource,
) -> float:
    return rng() * min(max_seconds, base_seconds * (2 ** max(attempt_count - 1, 0)))


def mark_failed(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, canvas_id: str,
    trigger_id: str, lease_owner: str, error: str, base_seconds: float,
    max_seconds: float, max_attempts: int, rng: RandomSource,
) -> WorkflowAttempt:
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM workflow_attempts WHERE tenant_id=? AND canvas_id=? "
            "AND trigger_id=? AND lease_owner=? AND status='running'",
            (tenant_id, canvas_id, trigger_id, lease_owner),
        ).fetchone()
        if row is None:
            raise StaleLeaseError(
                f"attempt {canvas_id}/{trigger_id} not leased by {lease_owner!r}"
            )
        if row["attempt_count"] >= max_attempts:
            conn.execute(
                "UPDATE workflow_attempts SET status='quarantined', last_error=?, "
                "lease_owner=NULL, lease_expires_at=NULL, next_retry_at=NULL, updated_at=? "
                "WHERE tenant_id=? AND canvas_id=? AND trigger_id=?",
                (error, now, tenant_id, canvas_id, trigger_id),
            )
        else:
            delay = compute_backoff(
                row["attempt_count"], base_seconds=base_seconds,
                max_seconds=max_seconds, rng=rng,
            )
            conn.execute(
                "UPDATE workflow_attempts SET status='failed', last_error=?, "
                "lease_owner=NULL, lease_expires_at=NULL, next_retry_at=?, updated_at=? "
                "WHERE tenant_id=? AND canvas_id=? AND trigger_id=?",
                (error, now + delay, now, tenant_id, canvas_id, trigger_id),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    attempt = get_attempt(
        conn, tenant_id=tenant_id, canvas_id=canvas_id, trigger_id=trigger_id,
    )
    if attempt is None:  # pragma: no cover - row was updated above
        raise RuntimeError(f"attempt {canvas_id}/{trigger_id} missing after failure")
    return attempt


def reset_quarantined(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, canvas_id: str, trigger_id: str,
) -> WorkflowAttempt:
    changed = conn.execute(
        "UPDATE workflow_attempts SET status='pending', attempt_count=0, last_error=NULL, "
        "next_retry_at=NULL, lease_owner=NULL, lease_expires_at=NULL, updated_at=? "
        "WHERE tenant_id=? AND canvas_id=? AND trigger_id=? AND status='quarantined'",
        (clock(), tenant_id, canvas_id, trigger_id),
    )
    if changed.rowcount == 0:
        raise AttemptNotQuarantinedError(f"attempt {canvas_id}/{trigger_id} is not quarantined")
    attempt = get_attempt(
        conn, tenant_id=tenant_id, canvas_id=canvas_id, trigger_id=trigger_id,
    )
    if attempt is None:  # pragma: no cover - rowcount guarantees existence
        raise RuntimeError(f"attempt {canvas_id}/{trigger_id} missing after reset")
    return attempt


def list_quarantined(
    conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str | None = None,
) -> list[WorkflowAttempt]:
    if canvas_id is None:
        rows = conn.execute(
            "SELECT * FROM workflow_attempts WHERE tenant_id=? AND status='quarantined' "
            "ORDER BY canvas_id, trigger_id", (tenant_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM workflow_attempts WHERE tenant_id=? AND status='quarantined' "
            "AND canvas_id=? ORDER BY trigger_id", (tenant_id, canvas_id),
        ).fetchall()
    return [_row_to_attempt(row) for row in rows]


__all__ = [
    "AttemptNotQuarantinedError", "StaleLeaseError", "compute_backoff",
    "list_quarantined", "mark_failed", "reset_quarantined",
]
