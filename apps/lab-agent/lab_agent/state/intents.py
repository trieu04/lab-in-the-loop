"""``side_effect_intents``: an outbox persisted before every canvas/provider
mutation, keyed for crash-safe replay.

canvus-mcp has no ``update_*``/idempotent create tools (phase-02 scope
notes), so a caller cannot assume retrying a write is itself safe -- the
intent row plus :mod:`lab_agent.recovery`'s live probe is what makes a
mutation safe to retry across a restart.
"""

from __future__ import annotations

import sqlite3

from lab_agent.state.models import Clock, IntentStatus, SideEffectIntent


class IntentHashMismatchError(RuntimeError):
    """Raised when an idempotency key is reused with a different input hash --
    fail closed rather than silently proceed with an ambiguous replay."""


def _row_to_intent(row: sqlite3.Row) -> SideEffectIntent:
    return SideEffectIntent(
        idempotency_key=row["idempotency_key"],
        canvas_id=row["canvas_id"],
        kind=row["kind"],
        input_hash=row["input_hash"],
        status=IntentStatus(row["status"]),
        external_id=row["external_id"],
        attempt_count=row["attempt_count"],
        next_retry_at=row["next_retry_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        reconciled_at=row["reconciled_at"],
    )


def get_intent(conn: sqlite3.Connection, *, idempotency_key: str) -> SideEffectIntent | None:
    row = conn.execute(
        "SELECT * FROM side_effect_intents WHERE idempotency_key=?", (idempotency_key,)
    ).fetchone()
    return _row_to_intent(row) if row is not None else None


def prepare_intent(
    conn: sqlite3.Connection, *, clock: Clock, idempotency_key: str, canvas_id: str, kind: str, input_hash: str
) -> SideEffectIntent:
    """Persist a ``pending`` intent before the mutation executes.

    Idempotent: re-preparing the same key with the same ``input_hash``
    returns the existing row untouched (whatever its current status). A
    different ``input_hash`` under the same key raises
    :class:`IntentHashMismatchError` -- fail closed.
    """
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM side_effect_intents WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if row is not None:
            if row["input_hash"] != input_hash:
                # No manual ROLLBACK here: raising propagates straight into the
                # `except Exception` below, which rolls back exactly once. A
                # second explicit ROLLBACK before that would fail with
                # "cannot rollback - no transaction is active" and mask this
                # exception with an OperationalError instead.
                raise IntentHashMismatchError(
                    f"idempotency key {idempotency_key!r} already used with a different input"
                )
            conn.execute("COMMIT")
            return _row_to_intent(row)
        conn.execute(
            "INSERT INTO side_effect_intents "
            "(idempotency_key, canvas_id, kind, input_hash, status, attempt_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
            (idempotency_key, canvas_id, kind, input_hash, now, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    intent = get_intent(conn, idempotency_key=idempotency_key)
    if intent is None:  # pragma: no cover - just inserted above
        raise RuntimeError(f"intent {idempotency_key} missing immediately after prepare")
    return intent


def mark_executed(conn: sqlite3.Connection, *, clock: Clock, idempotency_key: str, external_id: str) -> SideEffectIntent:
    """Record that the mutation ran and returned ``external_id`` (not yet reconciled)."""
    now = clock()
    conn.execute(
        "UPDATE side_effect_intents SET status='executed', external_id=?, "
        "attempt_count=attempt_count+1, updated_at=? WHERE idempotency_key=?",
        (external_id, now, idempotency_key),
    )
    return _require_intent(conn, idempotency_key)


def mark_reconciled(
    conn: sqlite3.Connection, *, clock: Clock, idempotency_key: str, external_id: str | None = None
) -> SideEffectIntent:
    """Record that the effect is confirmed live (via execution or a probe hit)."""
    now = clock()
    if external_id is None:
        conn.execute(
            "UPDATE side_effect_intents SET status='reconciled', reconciled_at=?, updated_at=? "
            "WHERE idempotency_key=?",
            (now, now, idempotency_key),
        )
    else:
        conn.execute(
            "UPDATE side_effect_intents SET status='reconciled', external_id=?, reconciled_at=?, updated_at=? "
            "WHERE idempotency_key=?",
            (external_id, now, now, idempotency_key),
        )
    return _require_intent(conn, idempotency_key)


def mark_failed(
    conn: sqlite3.Connection, *, clock: Clock, idempotency_key: str, error: str, next_retry_at: float | None = None
) -> SideEffectIntent:
    """Record a failed execution attempt without claiming completion."""
    now = clock()
    conn.execute(
        "UPDATE side_effect_intents SET status='failed', last_error=?, next_retry_at=?, "
        "attempt_count=attempt_count+1, updated_at=? WHERE idempotency_key=?",
        (error, next_retry_at, now, idempotency_key),
    )
    return _require_intent(conn, idempotency_key)


def list_incomplete(conn: sqlite3.Connection, *, canvas_id: str | None = None) -> list[SideEffectIntent]:
    """Every intent not yet ``reconciled`` (pending, executed, or failed)."""
    if canvas_id is None:
        rows = conn.execute(
            "SELECT * FROM side_effect_intents WHERE status != 'reconciled' ORDER BY created_at"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM side_effect_intents WHERE status != 'reconciled' AND canvas_id=? "
            "ORDER BY created_at",
            (canvas_id,),
        ).fetchall()
    return [_row_to_intent(row) for row in rows]


def _require_intent(conn: sqlite3.Connection, idempotency_key: str) -> SideEffectIntent:
    intent = get_intent(conn, idempotency_key=idempotency_key)
    if intent is None:
        raise RuntimeError(f"intent {idempotency_key} not found (was prepare_intent called first?)")
    return intent


__all__ = [
    "IntentHashMismatchError",
    "get_intent",
    "list_incomplete",
    "mark_executed",
    "mark_failed",
    "mark_reconciled",
    "prepare_intent",
]
