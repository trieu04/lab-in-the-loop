"""Tenant- and canvas-qualified crash-safe side-effect intent persistence."""

from __future__ import annotations

import sqlite3

from lab_agent.state.models import Clock, IntentStatus, SideEffectIntent


class IntentHashMismatchError(RuntimeError):
    """An idempotency key was reused with incompatible input on one canvas."""


class IntentAlreadyClaimedError(RuntimeError):
    """Another worker owns or terminally settled this provider dispatch."""


def _tenant(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("tenant_id must be non-empty")
    return value


def _canvas(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("canvas_id must be non-empty")
    return value


def _row_to_intent(row: sqlite3.Row) -> SideEffectIntent:
    return SideEffectIntent(
        idempotency_key=row["idempotency_key"], canvas_id=row["canvas_id"], kind=row["kind"],
        input_hash=row["input_hash"], status=IntentStatus(row["status"]),
        external_id=row["external_id"], attempt_count=row["attempt_count"],
        next_retry_at=row["next_retry_at"], last_error=row["last_error"],
        created_at=row["created_at"], updated_at=row["updated_at"],
        reconciled_at=row["reconciled_at"], tenant_id=row["tenant_id"],
    )


def get_intent(
    conn: sqlite3.Connection, *, idempotency_key: str, canvas_id: str, tenant_id: str = "default",
) -> SideEffectIntent | None:
    row = conn.execute(
        "SELECT * FROM side_effect_intents WHERE tenant_id=? AND canvas_id=? AND idempotency_key=?",
        (_tenant(tenant_id), _canvas(canvas_id), idempotency_key),
    ).fetchone()
    return _row_to_intent(row) if row is not None else None


def prepare_intent(
    conn: sqlite3.Connection, *, clock: Clock, idempotency_key: str, canvas_id: str,
    kind: str, input_hash: str, tenant_id: str = "default",
) -> SideEffectIntent:
    """Persist an intent before a mutation, scoped to its caller's canvas."""
    tenant, canvas, now = _tenant(tenant_id), _canvas(canvas_id), clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        record = get_intent(conn, tenant_id=tenant, canvas_id=canvas, idempotency_key=idempotency_key)
        if record is not None:
            if (record.kind, record.input_hash) != (kind, input_hash):
                raise IntentHashMismatchError(
                    f"idempotency key {idempotency_key!r} already used with different input"
                )
            conn.execute("COMMIT")
            return record
        conn.execute(
            "INSERT INTO side_effect_intents (tenant_id, canvas_id, idempotency_key, kind, input_hash, "
            "status, attempt_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)",
            (tenant, canvas, idempotency_key, kind, input_hash, now, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return _require_intent(conn, idempotency_key, canvas, tenant)


def mark_submitted(
    conn: sqlite3.Connection, *, clock: Clock, idempotency_key: str, canvas_id: str,
    tenant_id: str = "default",
) -> SideEffectIntent:
    """Atomically claim one eligible tenant/canvas-qualified intent."""
    tenant, canvas = _tenant(tenant_id), _canvas(canvas_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        changed = conn.execute(
            "UPDATE side_effect_intents SET status='submitted', attempt_count=attempt_count+1, "
            "next_retry_at=NULL, last_error=NULL, updated_at=? WHERE tenant_id=? AND canvas_id=? "
            "AND idempotency_key=? AND status IN ('pending', 'failed')",
            (clock(), tenant, canvas, idempotency_key),
        ).rowcount
        if changed != 1:
            raise IntentAlreadyClaimedError(f"intent {idempotency_key!r} is not dispatchable")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return _require_intent(conn, idempotency_key, canvas, tenant)


def mark_executed(
    conn: sqlite3.Connection, *, clock: Clock, idempotency_key: str, canvas_id: str,
    external_id: str, tenant_id: str = "default",
) -> SideEffectIntent:
    return _mark(conn, clock, idempotency_key, canvas_id, tenant_id,
                 "status='executed', external_id=?, attempt_count=attempt_count+CASE WHEN status='submitted' THEN 0 ELSE 1 END", (external_id,))


def mark_reconciled(
    conn: sqlite3.Connection, *, clock: Clock, idempotency_key: str, canvas_id: str,
    external_id: str | None = None, tenant_id: str = "default",
) -> SideEffectIntent:
    if external_id is None:
        return _mark(conn, clock, idempotency_key, canvas_id, tenant_id, "status='reconciled', reconciled_at=?", ())
    return _mark(conn, clock, idempotency_key, canvas_id, tenant_id,
                 "status='reconciled', external_id=?, reconciled_at=?", (external_id,))


def mark_ambiguous(
    conn: sqlite3.Connection, *, clock: Clock, idempotency_key: str, canvas_id: str,
    error: str, external_id: str | None, tenant_id: str = "default",
) -> SideEffectIntent:
    return _mark(conn, clock, idempotency_key, canvas_id, tenant_id,
                 "status='failed', external_id=?, last_error=?, attempt_count=attempt_count+CASE WHEN status='submitted' THEN 0 ELSE 1 END", (external_id, error))


def mark_failed(
    conn: sqlite3.Connection, *, clock: Clock, idempotency_key: str, canvas_id: str,
    error: str, next_retry_at: float | None = None, tenant_id: str = "default",
) -> SideEffectIntent:
    return _mark(conn, clock, idempotency_key, canvas_id, tenant_id,
                 "status='failed', last_error=?, next_retry_at=?, attempt_count=attempt_count+CASE WHEN status='submitted' THEN 0 ELSE 1 END", (error, next_retry_at))


def list_incomplete(
    conn: sqlite3.Connection, *, canvas_id: str | None = None, tenant_id: str = "default",
) -> list[SideEffectIntent]:
    tenant = _tenant(tenant_id)
    where, values = ["tenant_id=?", "status != 'reconciled'"], [tenant]
    if canvas_id is not None:
        where.append("canvas_id=?")
        values.append(_canvas(canvas_id))
    rows = conn.execute(
        "SELECT * FROM side_effect_intents WHERE " + " AND ".join(where) + " ORDER BY created_at", values
    ).fetchall()
    return [_row_to_intent(row) for row in rows]


def _mark(
    conn: sqlite3.Connection, clock: Clock, key: str, canvas_id: str, tenant_id: str,
    sql: str, values: tuple[object, ...],
) -> SideEffectIntent:
    tenant, canvas, now = _tenant(tenant_id), _canvas(canvas_id), clock()
    if get_intent(conn, tenant_id=tenant, canvas_id=canvas, idempotency_key=key) is None:
        raise RuntimeError(f"intent {key!r} not found (was prepare_intent called first?)")
    reconciliation_values = (*values, now) if "reconciled_at=?" in sql else values
    conn.execute(
        f"UPDATE side_effect_intents SET {sql}, updated_at=? "
        "WHERE tenant_id=? AND canvas_id=? AND idempotency_key=?",
        (*reconciliation_values, now, tenant, canvas, key),
    )
    return _require_intent(conn, key, canvas, tenant)


def _require_intent(
    conn: sqlite3.Connection, key: str, canvas_id: str, tenant_id: str
) -> SideEffectIntent:
    intent = get_intent(conn, idempotency_key=key, canvas_id=canvas_id, tenant_id=tenant_id)
    if intent is None:
        raise RuntimeError(f"intent {key!r} not found (was prepare_intent called first?)")
    return intent


__all__ = [
    "IntentAlreadyClaimedError", "IntentHashMismatchError", "get_intent", "list_incomplete",
    "mark_ambiguous", "mark_executed", "mark_failed", "mark_reconciled", "mark_submitted",
    "prepare_intent",
]
