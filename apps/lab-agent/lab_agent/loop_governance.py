"""Loop-level stop governance: the backstops the model's own decision does not
cover (FR-LITL-013).

Kept out of :mod:`lab_agent.orchestrator` so the loop driver stays under the
200-line budget and so the stop logic (round cap, committed budget, wall-clock,
no-progress streak) is unit-testable without running a whole loop. A
:class:`StopTracker` is created only when a run is governed; the ungoverned
(legacy) loop path is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import GovernanceContext, LocalityDeniedError
from lab_agent.models.experiment import LoopDecision
from lab_agent.models.governance import StopReason
from lab_agent.orchestrator_support import LoopSummary, write_closed_node
from lab_agent.policy import (
    BudgetExceededError,
    PostResponseBudgetExceededError,
    build_stop_policy,
    result_signature,
)
from lab_agent.policy.stop import StopPolicy
from lab_agent.state_store import StateStore

log = structlog.get_logger(__name__)


@dataclass
class StopTracker:
    """Per-run state for evaluating the harness stop backstops each round."""

    gov: GovernanceContext
    stop_policy: StopPolicy
    start_time: float
    prev_signature: str = ""
    streak: int = 0
    _seen: bool = field(default=False, repr=False)

    def observe_result(self, result_text: str) -> None:
        """Fold this round's result into the no-progress streak.

        The streak counts consecutive rounds whose (whitespace-normalized)
        result signature repeats; a changed signature resets it to one.
        """
        signature = result_signature(result_text)
        if self._seen and signature == self.prev_signature:
            self.streak += 1
        else:
            self.streak = 1
        self.prev_signature = signature
        self._seen = True

    def check(self, rounds: int) -> StopReason | None:
        """Return the tripped stop reason for the current state, or ``None``.

        Committed budget (run or canvas) is consulted first, then the round
        cap, wall-clock elapsed time, and finally the no-progress streak.
        """
        budget_reason = self.gov.budget.stop_reason()
        if budget_reason is not None:
            return budget_reason
        elapsed = self.gov.store.clock() - self.start_time
        return self.stop_policy.evaluate(
            rounds=rounds, elapsed_seconds=elapsed, no_progress_streak=self.streak,
        )


def make_stop_tracker(gov: GovernanceContext) -> StopTracker:
    """Build a :class:`StopTracker` bound to the governed run's start time."""
    return StopTracker(
        gov=gov, stop_policy=build_stop_policy(gov.settings), start_time=gov.store.clock()
    )


def stop_decision(reason: StopReason) -> LoopDecision:
    """A synthetic ``LoopDecision`` for a harness stop, so the closed node renders
    a STOP with the distinct reason (no model call is made to produce it)."""
    return LoopDecision(proceed=False, reason=f"harness stop: {reason.value}")


async def close_with_reason(
    mcp: MCPClient, settings: Settings, store: StateStore, summary: LoopSummary, *, canvas_id: str,
    result_id: str, round_index: int, reason: StopReason,
) -> bool:
    """Write the one terminal closure and its audit record."""
    summary.closed_id = await write_closed_node(
        mcp, store, settings, canvas_id=canvas_id, decision=stop_decision(reason),
        reason=reason.value, backstop=True, round_index=round_index, result_id=result_id,
    )
    summary.stopped_reason = reason.value
    store.append_audit_event(
        canvas_id, "loop_stopped", {"reason": reason.value, "rounds": summary.rounds},
        round=round_index,
    )
    return True


def governance_stop_reason(
    error: BudgetExceededError | LocalityDeniedError,
) -> StopReason:
    """Map a governance denial to the closure reason shown to operators."""
    if isinstance(error, (PostResponseBudgetExceededError, LocalityDeniedError)):
        return error.reason
    return StopReason.RESERVATION_DENIAL


async def close_governance_error(
    mcp: MCPClient,
    settings: Settings,
    store: StateStore,
    summary: LoopSummary,
    *,
    canvas_id: str,
    result_id: str,
    round_index: int,
    error: BudgetExceededError | LocalityDeniedError,
) -> LoopSummary:
    """Close a loop after locality, reservation, or committed-usage denial."""
    reason = governance_stop_reason(error)
    await close_with_reason(
        mcp, settings, store, summary, canvas_id=canvas_id, result_id=result_id,
        round_index=round_index, reason=reason,
    )
    return summary


async def evaluate_and_close(
    mcp: MCPClient,
    settings: Settings,
    store: StateStore,
    tracker: StopTracker,
    gov: GovernanceContext,
    summary: LoopSummary,
    *,
    canvas_id: str,
    result_text: str,
    result_id: str,
    round_index: int,
    observe: bool = True,
) -> bool:
    """Evaluate the harness stop backstops; on a stop, write and audit the closed
    node and return ``True`` (no further provider/canvas writes follow).

    The distinct reason (token/cost/wall-time/no-progress/max-rounds) is rendered
    on the closed node and recorded as a ``loop_stopped`` audit event.
    """
    gov.round_index = round_index
    if observe:
        tracker.observe_result(result_text)
    reason = tracker.check(summary.rounds)
    if reason is None:
        return False
    log.info("experiment_loop_stopped", canvas_id=canvas_id, rounds=summary.rounds, reason=reason.value)
    return await close_with_reason(
        mcp, settings, store, summary, canvas_id=canvas_id, result_id=result_id,
        round_index=round_index, reason=reason,
    )


__all__ = [
    "StopTracker", "close_governance_error", "close_with_reason", "evaluate_and_close",
    "governance_stop_reason", "make_stop_tracker", "stop_decision",
]
