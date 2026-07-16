"""Bounded SQL selectors for notification delivery workers."""

from __future__ import annotations

import sqlite3


def list_due_keys(conn: sqlite3.Connection, *, now: float, limit: int) -> list[str]:
    """Select due retry keys first, then fresh keys, with index-bounded LIMITs."""
    if limit < 1:
        raise ValueError("notification query limit must be positive")
    retry_rows = conn.execute(
        "SELECT logical_key FROM notification_outbox "
        "WHERE status='pending' AND next_retry_at IS NOT NULL AND next_retry_at<=? "
        "ORDER BY next_retry_at, created_at, logical_key LIMIT ?", (now, limit)
    ).fetchall()
    keys = [str(row["logical_key"]) for row in retry_rows]
    if len(keys) == limit:
        return keys
    fresh_rows = conn.execute(
        "SELECT logical_key FROM notification_outbox "
        "WHERE status='pending' AND next_retry_at IS NULL "
        "ORDER BY created_at, logical_key LIMIT ?", (limit - len(keys),)
    ).fetchall()
    return keys + [str(row["logical_key"]) for row in fresh_rows]


def expire_stale(conn: sqlite3.Connection, *, now: float) -> None:
    """Advance only expired sending/ambiguous rows without loading their JSON."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE notification_outbox SET status='ambiguous', lease_owner=NULL, "
            "lease_expires_at=NULL, failure_category='smtp_ambiguous', updated_at=? "
            "WHERE status='sending' AND lease_expires_at<=?",
            (now, now),
        )
        conn.execute(
            "UPDATE notification_outbox SET status='quarantined', lease_owner=NULL, "
            "lease_expires_at=NULL, failure_category='smtp_ambiguous', updated_at=? "
            "WHERE status='ambiguous' AND reconciliation_deadline<=? "
            "AND (lease_expires_at IS NULL OR lease_expires_at<=?)",
            (now, now, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


__all__ = ["expire_stale", "list_due_keys"]
