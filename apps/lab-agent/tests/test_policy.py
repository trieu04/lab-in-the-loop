"""Unit tests for the governance policy stack (no network, no store).

Covers deterministic routing (preferred + fallback + no-eligible), fail-closed
locality (restricted/unknown/unlisted denial), versioned cost estimation
(unknown pricing), the ``Budget`` reservation arithmetic, and every stop reason.
"""

from __future__ import annotations

import pytest

from lab_agent.config import Settings
from lab_agent.models.governance import (
    Budget,
    CostEstimate,
    DataClassification,
    StopReason,
    TaskContext,
    TaskStage,
    Usage,
    UsageStatus,
)
from lab_agent.policy import GovernanceBudget, estimate_cost, result_signature
from lab_agent.policy.budget import BudgetExceededError
from lab_agent.policy.locality import LocalityPolicy
from lab_agent.policy.routing import NoEligibleProviderError, RoutingTable
from lab_agent.policy.stop import StopPolicy

MODELS = {"openai": "gpt-4o-mini", "claude": "claude-sonnet-4-5"}


# ── routing ─────────────────────────────────────────────────────────
def test_routing_selects_preferred_provider():
    table = RoutingTable(
        {"setup": ["openai", "claude"]}, default_provider="openai", provider_models=MODELS
    )
    decision = table.select(TaskStage.SETUP, ["openai", "claude"])
    assert decision.provider == "openai"
    assert decision.model == "gpt-4o-mini"
    assert decision.fallback is False


def test_routing_falls_back_when_preferred_unavailable():
    table = RoutingTable(
        {"setup": ["openai", "claude"]}, default_provider="openai", provider_models=MODELS
    )
    decision = table.select(TaskStage.SETUP, ["claude"])  # openai not authorized
    assert decision.provider == "claude"
    assert decision.fallback is True
    assert "openai_unavailable" in decision.reason


def test_routing_uses_default_when_stage_absent():
    table = RoutingTable({}, default_provider="claude", provider_models=MODELS)
    assert table.select(TaskStage.ANALYSIS, ["claude"]).provider == "claude"


def test_routing_raises_when_no_eligible_provider():
    table = RoutingTable({"setup": ["openai"]}, default_provider="openai", provider_models=MODELS)
    with pytest.raises(NoEligibleProviderError):
        table.select(TaskStage.SETUP, ["claude"])  # only claude allowed, not preferred


# ── locality ────────────────────────────────────────────────────────
def test_task_context_defaults_missing_source_metadata_to_unknown():
    assert TaskContext.from_source_metadata([]).classifications == (DataClassification.UNKNOWN,)


def test_task_context_reads_evidence_source_classification():
    context = TaskContext.from_source_metadata([{"data_classification": "restricted"}])
    assert context.classifications == (DataClassification.RESTRICTED,)


def _locality() -> LocalityPolicy:
    return LocalityPolicy(
        {"openai": ["public", "internal"], "claude": ["public"]},
        {"openai": "https://openai.example", "claude": "https://claude.example"},
    )


def test_locality_allows_approved_classification():
    auth = _locality().authorize("openai", [DataClassification.INTERNAL])
    assert auth.allowed is True


def test_locality_denies_restricted_content():
    auth = _locality().authorize("openai", [DataClassification.RESTRICTED])
    assert auth.allowed is False
    assert "restricted" in auth.reason


def test_locality_denies_unknown_sensitive_content():
    assert _locality().authorize("claude", [DataClassification.UNKNOWN]).allowed is False


def test_locality_denies_unlisted_provider():
    assert _locality().authorize("mystery", [DataClassification.PUBLIC]).allowed is False


def test_locality_denies_provider_without_approved_endpoint():
    policy = LocalityPolicy({"openai": ["internal"]}, {})
    assert policy.authorize("openai", [DataClassification.INTERNAL]).allowed is False


def test_locality_most_restrictive_class_governs():
    mixed = [DataClassification.PUBLIC, DataClassification.RESTRICTED]
    assert _locality().authorize("openai", mixed).allowed is False


def test_locality_allowed_providers_preserves_order_and_filters():
    allowed = _locality().allowed_providers(
        ["openai", "claude"], [DataClassification.INTERNAL]
    )
    assert allowed == ["openai"]  # claude not approved for internal


# ── pricing ─────────────────────────────────────────────────────────
def test_estimate_cost_prices_exact_usage():
    usage = Usage("openai", "gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000, total_tokens=2000)
    pricing = {"gpt-4o-mini": {"input_per_1k": 0.15, "output_per_1k": 0.60}}
    cost = estimate_cost(usage, pricing, "v1")
    assert cost.cost_usd == pytest.approx(0.75)
    assert cost.status is UsageStatus.EXACT
    assert cost.pricing_version == "v1"


def test_estimate_cost_unknown_pricing_is_unavailable():
    usage = Usage("openai", "unpriced-model", prompt_tokens=100, total_tokens=100)
    cost = estimate_cost(usage, {}, "v1")
    assert cost.status is UsageStatus.UNAVAILABLE
    assert cost.cost_usd == 0.0


def test_estimate_cost_unavailable_usage_stays_unavailable():
    usage = Usage.unavailable("openai", "gpt-4o-mini")
    cost = estimate_cost(usage, {"gpt-4o-mini": {"input_per_1k": 1.0}}, "v1")
    assert cost.status is UsageStatus.UNAVAILABLE


def test_estimate_cost_incomplete_pricing_is_unavailable():
    usage = Usage("openai", "gpt-4o-mini", prompt_tokens=100, completion_tokens=100)
    cost = estimate_cost(usage, {"gpt-4o-mini": {"input_per_1k": 1.0}}, "v1")
    assert cost.status is UsageStatus.UNAVAILABLE
    assert cost.cost_usd == 0.0


# ── budget arithmetic ───────────────────────────────────────────────
def test_budget_would_exceed_counts_committed_and_reserved():
    b = Budget(token_limit=100, tokens_committed=60, tokens_reserved=30)
    assert b.would_exceed(20, 0.0) is True
    assert b.would_exceed(10, 0.0) is False


def test_budget_exhausted_reason_tokens_then_cost():
    assert Budget(token_limit=10, tokens_committed=10).exhausted_reason is StopReason.TOKEN_BUDGET
    assert Budget(cost_limit_usd=1.0, cost_committed_usd=1.0).exhausted_reason is StopReason.COST_BUDGET
    assert Budget().exhausted_reason is None


def test_budget_denies_unknown_cost_with_configured_cost_limit(store):
    settings = Settings(run_cost_budget_usd=1.0)  # type: ignore[call-arg]
    budget = GovernanceBudget.for_canvas(store, settings, "canvas")
    unknown_cost = CostEstimate(0.0, "unset", UsageStatus.UNAVAILABLE)
    with pytest.raises(BudgetExceededError, match="cost is unavailable"):
        budget.reserve(10, unknown_cost)


# ── stop policy ─────────────────────────────────────────────────────
def _stop() -> StopPolicy:
    return StopPolicy(max_rounds=5, wall_time_budget_seconds=100.0, no_progress_rounds=3)


def test_stop_none_when_within_all_limits():
    assert _stop().evaluate(rounds=1, elapsed_seconds=1.0, no_progress_streak=1) is None


def test_stop_on_max_rounds():
    assert _stop().evaluate(rounds=5, elapsed_seconds=1.0, no_progress_streak=1) is StopReason.MAX_ROUNDS


def test_stop_on_wall_time():
    assert _stop().evaluate(rounds=1, elapsed_seconds=100.0, no_progress_streak=1) is StopReason.WALL_TIME


def test_stop_on_no_progress():
    assert _stop().evaluate(rounds=1, elapsed_seconds=1.0, no_progress_streak=3) is StopReason.NO_PROGRESS


def test_stop_budget_takes_priority():
    reason = _stop().evaluate(
        rounds=5, elapsed_seconds=1.0, no_progress_streak=1,
        budget=Budget(cost_limit_usd=1.0, cost_committed_usd=1.0),
    )
    assert reason is StopReason.COST_BUDGET


def test_result_signature_ignores_whitespace():
    assert result_signature("a  b\n c") == result_signature("a b c")
    assert result_signature("a b") != result_signature("a c")
