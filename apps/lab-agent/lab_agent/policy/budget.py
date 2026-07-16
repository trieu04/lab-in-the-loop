"""Per-run and per-canvas budgets backed by a durable reservation ledger."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from lab_agent.models.governance import Budget, CostEstimate, StopReason, Usage, UsageStatus
from lab_agent.state.budget_reservations import BudgetReservationConflictError
from lab_agent.state_store import StateStore

_COMMIT_EVENT = "budget_committed"


class BudgetExceededError(RuntimeError):
    """Raised when a reservation would breach the run or canvas budget."""


class PostResponseBudgetExceededError(BudgetExceededError):
    """Actual committed usage exhausted a budget after provider success."""

    def __init__(self, reason: StopReason) -> None:
        self.reason = reason
        super().__init__(f"actual provider usage exhausted {reason.value}")


@dataclass
class Reservation:
    """A stable durable hold, keyed by the model-call intent/attempt identity."""

    reservation_id: str
    tokens: int
    cost_usd: float
    settled: bool = False


class GovernanceBudget:
    """Enforce budget envelopes with transactional, restart-safe accounting."""

    def __init__(
        self, store: StateStore, canvas_id: str, run: Budget, canvas: Budget, run_id: str
    ) -> None:
        self._store = store
        self._canvas_id = canvas_id
        self._run = run
        self._canvas = canvas
        self._run_id = run_id
        self._refresh()

    @classmethod
    def for_canvas(
        cls, store: StateStore, settings: object, canvas_id: str, *, run_id: str | None = None
    ) -> GovernanceBudget:
        """Restore committed and active holds from SQLite for both envelopes."""
        return cls(
            store, canvas_id,
            Budget(
                token_limit=getattr(settings, "run_token_budget", None),
                cost_limit_usd=getattr(settings, "run_cost_budget_usd", None),
            ),
            Budget(
                token_limit=getattr(settings, "canvas_token_budget", None),
                cost_limit_usd=getattr(settings, "canvas_cost_budget_usd", None),
            ),
            run_id or f"ephemeral:{uuid4()}",
        )

    def reserve(
        self, estimated_tokens: int, estimated_cost: CostEstimate, *,
        reservation_id: str | None = None, intent_key: str | None = None,
    ) -> Reservation:
        """Persist an idempotent hold before a provider can receive the request."""
        if estimated_cost.status is UsageStatus.UNAVAILABLE:
            self._store.append_audit_event(
                self._canvas_id, "budget_denied", {"est_tokens": estimated_tokens, "reason": "cost_unavailable"}
            )
            raise BudgetExceededError("reservation denied because cost is unavailable")
        key = reservation_id or f"legacy:{uuid4()}"
        try:
            record = self._store.reserve_budget(
                reservation_id=key, intent_key=intent_key or key, canvas_id=self._canvas_id,
                run_id=self._run_id, tokens=estimated_tokens, cost_usd=estimated_cost.cost_usd,
                run_token_limit=self._run.token_limit, run_cost_limit=self._run.cost_limit_usd,
                canvas_token_limit=self._canvas.token_limit, canvas_cost_limit=self._canvas.cost_limit_usd,
            )
        except BudgetReservationConflictError as exc:
            self._refresh()
            self._store.append_audit_event(
                self._canvas_id, "budget_denied",
                {"est_tokens": estimated_tokens, "est_cost_usd": round(estimated_cost.cost_usd, 6)},
            )
            raise BudgetExceededError(str(exc)) from exc
        self._refresh()
        return Reservation(
            reservation_id=record.reservation_id, tokens=record.estimated_tokens,
            cost_usd=record.estimated_cost_usd, settled=record.status != "reserved",
        )

    def release(self, reservation: Reservation) -> None:
        """Durably release a known-not-dispatched hold exactly once."""
        if reservation.settled:
            return
        try:
            self._store.settle_budget(reservation.reservation_id, action="released")
        except BudgetReservationConflictError:
            reservation.settled = True
            self._refresh()
            return
        reservation.settled = True
        self._refresh()

    def commit(self, reservation: Reservation, usage: Usage, cost: CostEstimate) -> None:
        """Durably settle actual usage; duplicate commits do not double count."""
        if reservation.settled:
            return
        record, transitioned = self._store.settle_budget(
            reservation.reservation_id, action="committed", actual_tokens=usage.total_tokens,
            actual_cost_usd=cost.cost_usd,
        )
        reservation.settled = True
        self._refresh()
        if transitioned:
            self._store.append_audit_event(
                self._canvas_id, _COMMIT_EVENT,
                {**usage.audit_metadata, "reservation_id": record.reservation_id,
                 "cost_usd": cost.cost_usd, "pricing_version": cost.pricing_version,
                 "cost_status": cost.status.value},
            )

    def stop_reason(self) -> StopReason | None:
        return self._run.exhausted_reason or self._canvas.exhausted_reason

    def raise_if_exhausted(self) -> None:
        """Raise before model output reaches any subsequent canvas-write path."""
        reason = self.stop_reason()
        if reason is not None:
            raise PostResponseBudgetExceededError(reason)

    @property
    def run(self) -> Budget:
        return self._run

    def _refresh(self) -> None:
        run = self._store.budget_totals(self._canvas_id, self._run_id)
        canvas = self._store.budget_totals(self._canvas_id)
        self._assign(self._run, run)
        self._assign(self._canvas, canvas)

    @staticmethod
    def _assign(budget: Budget, values: tuple[int, float, int, float]) -> None:
        budget.tokens_committed, budget.cost_committed_usd, budget.tokens_reserved, budget.cost_reserved_usd = values


__all__ = ["BudgetExceededError", "GovernanceBudget", "PostResponseBudgetExceededError", "Reservation"]
