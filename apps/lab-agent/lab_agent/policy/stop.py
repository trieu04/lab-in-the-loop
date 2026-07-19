"""Explicit, auditable stop rules for an experiment run (FR-LITL-013).

The model's own ``LoopDecision`` is the normal stop (UC step 3); this policy is
the set of harness backstops that halt a run the model would otherwise keep
driving: the round cap, the committed token/cost budget, wall-clock elapsed
time, and a no-progress streak (consecutive rounds whose result signature does
not change). Evaluation is pure, so the reason is deterministic and rendered on
the closed node.
"""

from __future__ import annotations

import hashlib

from lab_agent.models.governance import Budget, StopReason


def result_signature(result_text: str) -> str:
    """A stable signature of a round's result, for no-progress detection.

    Whitespace-normalized and hashed so incidental formatting differences do
    not read as progress; content stays out of the returned value (a digest).
    """
    normalized = " ".join(result_text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class StopPolicy:
    """Config-driven backstops evaluated once per round before more work runs."""

    def __init__(
        self,
        *,
        max_rounds: int | None,
        wall_time_budget_seconds: float | None,
        no_progress_rounds: int,
    ) -> None:
        self._max_rounds = max_rounds
        self._wall_time = wall_time_budget_seconds
        self._no_progress_rounds = no_progress_rounds

    def evaluate(
        self,
        *,
        rounds: int,
        elapsed_seconds: float,
        no_progress_streak: int,
        budget: Budget | None = None,
    ) -> StopReason | None:
        """Return the first tripped stop reason, or ``None`` to continue.

        Budget exhaustion is checked first (it is the hardest limit), then the
        round cap, wall-clock, and finally the no-progress streak.
        """
        if budget is not None:
            exhausted = budget.exhausted_reason
            if exhausted is not None:
                return exhausted
        if self._max_rounds is not None and rounds >= self._max_rounds:
            return StopReason.MAX_ROUNDS
        if self._wall_time is not None and elapsed_seconds >= self._wall_time:
            return StopReason.WALL_TIME
        if self._no_progress_rounds and no_progress_streak >= self._no_progress_rounds:
            return StopReason.NO_PROGRESS
        return None


__all__ = ["StopPolicy", "result_signature"]
