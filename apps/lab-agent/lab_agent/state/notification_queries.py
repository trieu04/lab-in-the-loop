"""Tenant-scoped bounded SQL selectors for notification delivery workers."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence


def _tenant(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("tenant_id must be non-empty")
    return value


def _canvas_scope(
    canvas_id: str | None, allowed_canvas_ids: Sequence[str] | None
) -> tuple[str, tuple[str, ...]]:
    if canvas_id is not None and allowed_canvas_ids is not None:
        raise ValueError("use canvas_id or allowed_canvas_ids, not both")
    if canvas_id is not None:
        return " AND canvas_id=?", (canvas_id,)
    if allowed_canvas_ids is None:
        return "", ()
    canvases = tuple(allowed_canvas_ids)
    if not canvases:
        return " AND 0", ()
    return f" AND canvas_id IN ({','.join('?' for _ in canvases)})", canvases


def list_due_keys(
    conn: sqlite3.Connection,
    *,
    now: float,
    limit: int,
    tenant_id: str = "default",
    canvas_id: str | None = None,
    allowed_canvas_ids: Sequence[str] | None = None,
) -> list[str]:
    """Select due retry keys first, then fresh keys for one tenant scope."""
    if limit < 1:
        raise ValueError("notification query limit must be positive")
    tenant = _tenant(tenant_id)
    scope, canvas_args = _canvas_scope(canvas_id, allowed_canvas_ids)
    retry_rows = conn.execute(
        "SELECT logical_key FROM notification_outbox WHERE tenant_id=?"
        + scope
        + " AND status='pending' AND next_retry_at IS NOT NULL AND next_retry_at<=? "
        "ORDER BY next_retry_at, created_at, logical_key LIMIT ?",
        (tenant, *canvas_args, now, limit),
    ).fetchall()
    keys = [str(row["logical_key"]) for row in retry_rows]
    if len(keys) == limit:
        return keys
    fresh_rows = conn.execute(
        "SELECT logical_key FROM notification_outbox WHERE tenant_id=?"
        + scope
        + " AND status='pending' AND next_retry_at IS NULL "
        "ORDER BY created_at, logical_key LIMIT ?",
        (tenant, *canvas_args, limit - len(keys)),
    ).fetchall()
    return keys + [str(row["logical_key"]) for row in fresh_rows]


def expire_stale(
    conn: sqlite3.Connection,
    *,
    now: float,
    tenant_id: str = "default",
    canvas_id: str | None = None,
    allowed_canvas_ids: Sequence[str] | None = None,
) -> None:
    """Advance expired sending/ambiguous rows without crossing tenant scope."""
    tenant = _tenant(tenant_id)
    scope, canvas_args = _canvas_scope(canvas_id, allowed_canvas_ids)
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE notification_outbox SET status='ambiguous', lease_owner=NULL, "
            "lease_expires_at=NULL, failure_category='smtp_ambiguous', updated_at=? "
            "WHERE tenant_id=?"
            + scope
            + " AND status='sending' AND lease_expires_at<=?",
            (now, tenant, *canvas_args, now),
        )
        conn.execute(
            "UPDATE notification_outbox SET status='quarantined', lease_owner=NULL, "
            "lease_expires_at=NULL, failure_category='smtp_ambiguous', updated_at=? "
            "WHERE tenant_id=?"
            + scope
            + " AND status='ambiguous' AND reconciliation_deadline<=? "
            "AND (lease_expires_at IS NULL OR lease_expires_at<=?)",
            (now, tenant, *canvas_args, now, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


__all__ = ["expire_stale", "list_due_keys"]
