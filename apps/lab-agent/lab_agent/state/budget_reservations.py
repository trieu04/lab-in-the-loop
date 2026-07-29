"""Atomic durable accounting for governed model-call budget reservations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from lab_agent.state.models import Clock


class BudgetReservationConflictError(RuntimeError):
    """A stable reservation identity was reused with incompatible accounting."""


@dataclass(frozen=True)
class BudgetReservation:
    reservation_id: str
    intent_key: str
    canvas_id: str
    run_id: str
    estimated_tokens: int
    estimated_cost_usd: float
    actual_tokens: int | None
    actual_cost_usd: float | None
    status: str
    tenant_id: str = "default"


def _tenant(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("tenant_id must be non-empty")
    return value


def _record(row: sqlite3.Row) -> BudgetReservation:
    return BudgetReservation(
        reservation_id=row["reservation_id"], intent_key=row["intent_key"],
        canvas_id=row["canvas_id"], run_id=row["run_id"],
        estimated_tokens=row["estimated_tokens"], estimated_cost_usd=row["estimated_cost_usd"],
        actual_tokens=row["actual_tokens"], actual_cost_usd=row["actual_cost_usd"],
        status=row["status"], tenant_id=row["tenant_id"],
    )


def totals(
    conn: sqlite3.Connection, *, canvas_id: str, run_id: str | None,
    tenant_id: str = "default",
) -> tuple[int, float, int, float]:
    """Return committed then reserved totals, optionally scoped to one run."""
    tenant = _tenant(tenant_id)
    where = "tenant_id=? AND canvas_id=?" if run_id is None else "tenant_id=? AND canvas_id=? AND run_id=?"
    args: tuple[str, ...] = (tenant, canvas_id) if run_id is None else (tenant, canvas_id, run_id)
    row = conn.execute(
        "SELECT COALESCE(SUM(CASE WHEN status='committed' THEN actual_tokens ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN status='committed' THEN actual_cost_usd ELSE 0 END), 0), "
        "COALESCE(SUM(CASE WHEN status='reserved' THEN estimated_tokens ELSE 0 END), 0), "
        f"COALESCE(SUM(CASE WHEN status='reserved' THEN estimated_cost_usd ELSE 0 END), 0) FROM budget_reservations WHERE {where}",
        args,
    ).fetchone()
    return int(row[0]), float(row[1]), int(row[2]), float(row[3])


def reserve(
    conn: sqlite3.Connection, *, clock: Clock, reservation_id: str, intent_key: str,
    canvas_id: str, run_id: str, tokens: int, cost_usd: float,
    run_token_limit: int | None, run_cost_limit: float | None,
    canvas_token_limit: int | None, canvas_cost_limit: float | None,
    tenant_id: str = "default",
) -> BudgetReservation:
    """Create one hold after atomically rechecking tenant/canvas limits."""
    tenant = _tenant(tenant_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM budget_reservations WHERE tenant_id=? AND canvas_id=? AND reservation_id=?",
            (tenant, canvas_id, reservation_id),
        ).fetchone()
        if row is not None:
            record = _record(row)
            inputs = (intent_key, canvas_id, run_id, tokens, cost_usd)
            if (record.intent_key, record.canvas_id, record.run_id, record.estimated_tokens,
                    record.estimated_cost_usd) != inputs:
                raise BudgetReservationConflictError("reservation identity has incompatible inputs")
            conn.execute("COMMIT")
            return record
        by_run = totals(conn, tenant_id=tenant, canvas_id=canvas_id, run_id=run_id)
        by_canvas = totals(conn, tenant_id=tenant, canvas_id=canvas_id, run_id=None)
        if _exceeds(by_run, tokens, cost_usd, run_token_limit, run_cost_limit) or _exceeds(
            by_canvas, tokens, cost_usd, canvas_token_limit, canvas_cost_limit
        ):
            raise BudgetReservationConflictError("reservation exceeds durable budget")
        now = clock()
        conn.execute(
            "INSERT INTO budget_reservations (tenant_id, reservation_id, intent_key, canvas_id, run_id, "
            "estimated_tokens, estimated_cost_usd, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'reserved', ?, ?)",
            (tenant, reservation_id, intent_key, canvas_id, run_id, tokens, cost_usd, now, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get(conn, reservation_id, canvas_id=canvas_id, tenant_id=tenant)


def settle(
    conn: sqlite3.Connection, *, clock: Clock, canvas_id: str, reservation_id: str, action: str,
    actual_tokens: int | None = None, actual_cost_usd: float | None = None,
    tenant_id: str = "default",
) -> tuple[BudgetReservation, bool]:
    """Commit or release a tenant-qualified hold exactly once."""
    tenant = _tenant(tenant_id)
    conn.execute("BEGIN IMMEDIATE")
    try:
        record = get(conn, reservation_id, canvas_id=canvas_id, tenant_id=tenant)
        if record.status == action:
            conn.execute("COMMIT")
            return record, False
        if record.status != "reserved":
            raise BudgetReservationConflictError(f"cannot {action} a {record.status} reservation")
        now = clock()
        if action == "committed":
            if actual_tokens is None or actual_cost_usd is None:
                raise ValueError("committed reservations require actual usage")
            conn.execute(
                "UPDATE budget_reservations SET status='committed', actual_tokens=?, actual_cost_usd=?, "
                "updated_at=?, settled_at=? WHERE tenant_id=? AND canvas_id=? AND reservation_id=?",
                (actual_tokens, actual_cost_usd, now, now, tenant, record.canvas_id, reservation_id),
            )
        else:
            conn.execute(
                "UPDATE budget_reservations SET status='released', updated_at=?, settled_at=? "
                "WHERE tenant_id=? AND canvas_id=? AND reservation_id=?",
                (now, now, tenant, record.canvas_id, reservation_id),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return get(conn, reservation_id, canvas_id=canvas_id, tenant_id=tenant), True


def get(
    conn: sqlite3.Connection, reservation_id: str, *, canvas_id: str, tenant_id: str = "default"
) -> BudgetReservation:
    tenant = _tenant(tenant_id)
    row = conn.execute(
        "SELECT * FROM budget_reservations WHERE tenant_id=? AND canvas_id=? AND reservation_id=?",
        (tenant, canvas_id, reservation_id),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"budget reservation {reservation_id!r} is missing")
    return _record(row)


def _exceeds(
    current: tuple[int, float, int, float], tokens: int, cost: float,
    token_limit: int | None, cost_limit: float | None,
) -> bool:
    committed_tokens, committed_cost, reserved_tokens, reserved_cost = current
    return ((token_limit is not None and committed_tokens + reserved_tokens + tokens > token_limit)
            or (cost_limit is not None and committed_cost + reserved_cost + cost > cost_limit))


__all__ = ["BudgetReservation", "BudgetReservationConflictError", "get", "reserve", "settle", "totals"]
