"""SQLite repository functions for tenant-scoped loop continuations."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from lab_agent.state.loop_continuation_models import (
    SHA256_HEX,
    LoopContinuation,
    LoopContinuationLineageError,
    run_id,
)
from lab_agent.state.models import Clock


def _from_row(row: sqlite3.Row) -> LoopContinuation:
    return LoopContinuation(**{key: row[key] for key in LoopContinuation.__dataclass_fields__})


def get(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    canvas_id: str,
    loop_scope: str,
) -> LoopContinuation | None:
    row = conn.execute(
        "SELECT * FROM loop_continuations WHERE tenant_id=? AND canvas_id=? AND loop_scope=?",
        (tenant_id, canvas_id, loop_scope),
    ).fetchone()
    return _from_row(row) if row is not None else None


def ensure(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    tenant_id: str,
    canvas_id: str,
    loop_scope: str,
    round_index: int,
    setup_id: str,
    result_id: str,
    branch_setup_id: str | None = None,
) -> LoopContinuation:
    now = clock()
    candidate = LoopContinuation(
        tenant_id=tenant_id,
        canvas_id=canvas_id,
        loop_scope=loop_scope,
        run_id=run_id(tenant_id, canvas_id, loop_scope),
        started_at=now,
        round_index=round_index,
        observed_round=0,
        previous_result_signature="",
        no_progress_streak=0,
        predecessor_setup_id=setup_id,
        predecessor_result_id=result_id,
        branch_setup_id=branch_setup_id or setup_id,
        updated_at=now,
    )
    columns = tuple(candidate.__dict__)
    conn.execute(
        f"INSERT OR IGNORE INTO loop_continuations ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        tuple(candidate.__dict__.values()),
    )
    current = get(conn, tenant_id=tenant_id, canvas_id=canvas_id, loop_scope=loop_scope)
    if current is None:  # pragma: no cover - INSERT OR IGNORE guarantees a row
        raise RuntimeError("loop continuation missing immediately after ensure")
    return current


def save(conn: sqlite3.Connection, *, clock: Clock, value: LoopContinuation) -> LoopContinuation:
    updated = replace(value, updated_at=clock())
    columns = tuple(updated.__dict__)
    assignments = ", ".join(f"{name}=excluded.{name}" for name in columns[3:])
    conn.execute(
        f"INSERT INTO loop_continuations ({', '.join(columns)}) VALUES "
        f"({', '.join('?' for _ in columns)}) ON CONFLICT(tenant_id, canvas_id, loop_scope) "
        f"DO UPDATE SET {assignments}",
        tuple(updated.__dict__.values()),
    )
    return updated


def resolve(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    tenant_id: str,
    canvas_id: str,
    setup_id: str,
    result_id: str,
    round_index: int,
) -> LoopContinuation:
    rows = conn.execute(
        "SELECT * FROM loop_continuations WHERE tenant_id=? AND canvas_id=? "
        "ORDER BY updated_at, loop_scope",
        (tenant_id, canvas_id),
    ).fetchall()
    candidates = []
    for row in rows:
        item = _from_row(row)
        setups = {item.branch_setup_id, item.predecessor_setup_id, item.staged_setup_id}
        results = {item.predecessor_result_id, item.staged_result_id}
        if setup_id in setups or result_id in results:
            candidates.append(item)
    if len(candidates) > 1:
        raise LoopContinuationLineageError("multiple continuations match this loop lineage")
    if candidates:
        item = candidates[0]
        if (
            item.branch_setup_id
            and item.branch_setup_id != setup_id
            and setup_id
            not in {
                item.predecessor_setup_id,
                item.staged_setup_id,
            }
        ):
            raise LoopContinuationLineageError("continuation branch identity mismatch")
        return item
    scope = f"setup:{setup_id}"
    if get(conn, tenant_id=tenant_id, canvas_id=canvas_id, loop_scope=scope) is not None:
        raise LoopContinuationLineageError("existing continuation does not match loop lineage")
    return ensure(
        conn,
        clock=clock,
        tenant_id=tenant_id,
        canvas_id=canvas_id,
        loop_scope=scope,
        round_index=round_index,
        setup_id=setup_id,
        result_id=result_id,
        branch_setup_id=setup_id,
    )


def list_staged(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    canvas_id: str,
) -> list[LoopContinuation]:
    rows = conn.execute(
        "SELECT * FROM loop_continuations WHERE tenant_id=? AND canvas_id=? "
        "AND staged_setup_id<>'' AND staged_proposal_hash<>'' ORDER BY updated_at, loop_scope",
        (tenant_id, canvas_id),
    ).fetchall()
    return [_from_row(row) for row in rows]


def record_result(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    tenant_id: str,
    canvas_id: str,
    setup_id: str,
    round_index: int,
    result_id: str,
    proposal_hash: str,
) -> LoopContinuation | None:
    if not result_id or len(result_id) > 200 or not SHA256_HEX.fullmatch(proposal_hash):
        raise ValueError("result continuation evidence is invalid")
    row = conn.execute(
        "SELECT * FROM loop_continuations WHERE tenant_id=? AND canvas_id=? "
        "AND staged_setup_id=? AND round_index=? AND staged_proposal_hash=? "
        "ORDER BY updated_at DESC LIMIT 1",
        (tenant_id, canvas_id, setup_id, round_index, proposal_hash),
    ).fetchone()
    return (
        save(conn, clock=clock, value=replace(_from_row(row), staged_result_id=result_id))
        if row
        else None
    )


__all__ = ["ensure", "get", "list_staged", "record_result", "resolve", "save"]
