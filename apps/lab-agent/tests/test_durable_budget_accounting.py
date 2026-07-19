"""Crash-safe durable reservation accounting regressions."""

from __future__ import annotations

import pytest

from lab_agent.config import Settings
from lab_agent.models.governance import CostEstimate, Usage, UsageStatus
from lab_agent.policy.budget import BudgetExceededError, GovernanceBudget
from lab_agent.state_store import StateStore


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "run_token_budget": 10,
        "canvas_token_budget": 10,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _cost(value: float = 1.0) -> CostEstimate:
    return CostEstimate(value, "v1", UsageStatus.ESTIMATED)


def test_restart_restores_reserved_hold_and_blocks_limit_bypass(tmp_path, clock, rng) -> None:
    db_path = tmp_path / "state.db"
    first = StateStore(db_path, clock=clock, rng=rng)
    budget = GovernanceBudget.for_canvas(first, _settings(), "canvas", run_id="run")
    budget.reserve(8, _cost(), reservation_id="intent:1", intent_key="intent")
    first.close()

    restarted = StateStore(db_path, clock=clock, rng=rng)
    try:
        rebuilt = GovernanceBudget.for_canvas(restarted, _settings(), "canvas", run_id="run")
        assert rebuilt.run.tokens_reserved == 8
        with pytest.raises(BudgetExceededError):
            rebuilt.reserve(3, _cost(), reservation_id="intent:2", intent_key="intent-2")
    finally:
        restarted.close()


def test_reserve_commit_and_release_are_idempotent_by_reservation_identity(store) -> None:
    budget = GovernanceBudget.for_canvas(store, _settings(), "canvas", run_id="run")
    reservation = budget.reserve(4, _cost(), reservation_id="intent:1", intent_key="intent")
    again = budget.reserve(4, _cost(), reservation_id="intent:1", intent_key="intent")
    assert again.reservation_id == reservation.reservation_id

    usage = Usage("openai", "model", total_tokens=6)
    budget.commit(reservation, usage, _cost(2.0))
    budget.commit(again, usage, _cost(2.0))
    assert budget.run.tokens_committed == 6
    assert budget.run.tokens_reserved == 0

    released = budget.reserve(2, _cost(), reservation_id="intent:2", intent_key="intent-2")
    budget.release(released)
    budget.release(released)
    assert budget.run.tokens_reserved == 0


def test_commit_after_restart_is_not_double_counted(tmp_path, clock, rng) -> None:
    db_path = tmp_path / "state.db"
    first = StateStore(db_path, clock=clock, rng=rng)
    budget = GovernanceBudget.for_canvas(first, _settings(), "canvas", run_id="run")
    reservation = budget.reserve(4, _cost(), reservation_id="intent:1", intent_key="intent")
    budget.commit(reservation, Usage("openai", "model", total_tokens=5), _cost(2.0))
    first.close()

    restarted = StateStore(db_path, clock=clock, rng=rng)
    try:
        rebuilt = GovernanceBudget.for_canvas(restarted, _settings(), "canvas", run_id="run")
        replay = rebuilt.reserve(4, _cost(), reservation_id="intent:1", intent_key="intent")
        rebuilt.commit(replay, Usage("openai", "model", total_tokens=5), _cost(2.0))
        assert rebuilt.run.tokens_committed == 5
        assert rebuilt.run.cost_committed_usd == 2.0
    finally:
        restarted.close()
