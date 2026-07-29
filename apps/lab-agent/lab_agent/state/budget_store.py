"""Tenant-scoped StateStore facade for durable budget reservations."""

from __future__ import annotations

import sqlite3

from lab_agent.state import budget_reservations
from lab_agent.state.models import Clock


class BudgetStoreMixin:
    """Expose budget accounting without permitting tenant/canvas escape."""

    conn: sqlite3.Connection
    clock: Clock
    tenant_id: str

    def _require_canvas_scope(self, canvas_id: str) -> None: ...

    def budget_totals(
        self, canvas_id: str, run_id: str | None = None,
    ) -> tuple[int, float, int, float]:
        self._require_canvas_scope(canvas_id)
        return budget_reservations.totals(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, run_id=run_id,
        )

    def reserve_budget(
        self, *, reservation_id: str, intent_key: str, canvas_id: str, run_id: str,
        tokens: int, cost_usd: float, run_token_limit: int | None,
        run_cost_limit: float | None, canvas_token_limit: int | None,
        canvas_cost_limit: float | None,
    ) -> budget_reservations.BudgetReservation:
        self._require_canvas_scope(canvas_id)
        return budget_reservations.reserve(
            self.conn, clock=self.clock, tenant_id=self.tenant_id,
            reservation_id=reservation_id, intent_key=intent_key, canvas_id=canvas_id,
            run_id=run_id, tokens=tokens, cost_usd=cost_usd,
            run_token_limit=run_token_limit, run_cost_limit=run_cost_limit,
            canvas_token_limit=canvas_token_limit, canvas_cost_limit=canvas_cost_limit,
        )

    def settle_budget(
        self, reservation_id: str, *, canvas_id: str | None = None, action: str,
        actual_tokens: int | None = None, actual_cost_usd: float | None = None,
    ) -> tuple[budget_reservations.BudgetReservation, bool]:
        if canvas_id is None:
            rows = self.conn.execute(
                "SELECT canvas_id FROM budget_reservations WHERE tenant_id=? AND reservation_id=?",
                (self.tenant_id, reservation_id),
            ).fetchall()
            if len(rows) != 1:
                raise RuntimeError(f"budget reservation {reservation_id!r} is missing")
            canvas_id = str(rows[0]["canvas_id"])
        self._require_canvas_scope(canvas_id)
        return budget_reservations.settle(
            self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id,
            reservation_id=reservation_id, action=action, actual_tokens=actual_tokens,
            actual_cost_usd=actual_cost_usd,
        )


__all__ = ["BudgetStoreMixin"]
