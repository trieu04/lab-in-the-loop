"""Governed-model-call tests: locality denial, usage/cost capture, durable
budgets, route fallback, and bounded ambiguous-timeout retry (no network).

Every call drives a real migrated ``StateStore`` (the ``store`` fixture) so the
side-effect-intent guard and audit trail are exercised the way production does.
"""

from __future__ import annotations

import pytest

from lab_agent.adapters.base import AdapterResponse, Usage
from lab_agent.config import Settings
from lab_agent.model_gateway import (
    GovernedAdapter,
    LocalityDeniedError,
    ModelCallInFlightError,
    build_context,
    governed_generate,
)
from lab_agent.models.governance import DataClassification, TaskStage
from lab_agent.policy import BudgetExceededError, GovernanceBudget

MESSAGES = [{"role": "user", "content": "run the setup"}]
PRICING = {"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}}


def _settings(**kw) -> Settings:
    base: dict = {
        "openai_api_key": "k", "pricing_version": "v1", "model_pricing": PRICING,
        "provider_endpoints": {"openai": "https://openai.example"},
    }
    base.update(kw)
    return Settings(**base)  # type: ignore[arg-type]


class SpyAdapter:
    """Records call count; replays canned responses/exceptions in order."""

    def __init__(self, results: list) -> None:
        self.results = list(results)
        self.calls = 0

    async def generate(self, messages, tools=None, response_schema=None, schema_name="result"):
        self.calls += 1
        item = self.results[min(self.calls - 1, len(self.results) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item


def _ctx(store, settings, adapter, **over):
    ctx = build_context(
        store, settings, "c", {"openai": adapter, **over.pop("adapters", {})},
        [{"classification": "internal"}],
    )
    for key, value in over.items():
        setattr(ctx, key, value)
    return ctx


def _events(store, name):
    return [e for e in store.list_audit_events("c") if e.event == name]


async def test_missing_source_classification_denied_before_provider_call(store):
    adapter = SpyAdapter([AdapterResponse(text="should not run")])
    settings = _settings()
    ctx = build_context(store, settings, "c", {"openai": adapter})
    with pytest.raises(LocalityDeniedError):
        await governed_generate(ctx, stage=TaskStage.SETUP, messages=MESSAGES)
    assert adapter.calls == 0


async def test_locality_denied_before_any_provider_call(store):
    adapter = SpyAdapter([AdapterResponse(text="should not run")])
    ctx = _ctx(store, _settings(), adapter, classifications=[DataClassification.RESTRICTED])
    with pytest.raises(LocalityDeniedError):
        await governed_generate(ctx, stage=TaskStage.SETUP, messages=MESSAGES)
    assert adapter.calls == 0  # provider never received restricted content
    assert len(_events(store, "locality_denied")) == 1


async def test_exact_usage_captured_and_budget_committed(store):
    usage = Usage("openai", "gpt-4o-mini", prompt_tokens=1000, completion_tokens=1000, total_tokens=2000)
    adapter = SpyAdapter([AdapterResponse(parsed={"ok": 1}, usage=usage)])
    ctx = _ctx(store, _settings(), adapter)
    await governed_generate(ctx, stage=TaskStage.SETUP, messages=MESSAGES, schema_name="ExperimentSetup")
    event = _events(store, "model_call")[0]
    assert event.payload["total_tokens"] == 2000
    assert event.payload["status"] == "exact"
    assert event.payload["cost_usd"] == pytest.approx(2.0)
    assert ctx.budget.run.tokens_committed == 2000


async def test_unknown_pricing_denies_cost_limited_call_before_dispatch(store):
    adapter = SpyAdapter([AdapterResponse(parsed={"ok": 1})])
    ctx = _ctx(store, _settings(model_pricing={}, run_cost_budget_usd=1.0), adapter)
    with pytest.raises(BudgetExceededError, match="cost is unavailable"):
        await governed_generate(ctx, stage=TaskStage.SETUP, messages=MESSAGES)
    assert adapter.calls == 0
    assert _events(store, "budget_denied")[0].payload["reason"] == "cost_unavailable"


async def test_missing_usage_consumes_auditable_estimate(store):
    adapter = SpyAdapter([AdapterResponse(parsed={"ok": 1})])  # no usage
    ctx = _ctx(store, _settings(), adapter)
    await governed_generate(ctx, stage=TaskStage.SETUP, messages=MESSAGES)
    event = _events(store, "model_call")[0]
    assert event.payload["status"] == "estimated"
    assert event.payload["cost_status"] == "estimated"
    assert ctx.budget.run.tokens_committed > 0


async def test_committed_budget_survives_reconstruction(store):
    usage = Usage("openai", "gpt-4o-mini", prompt_tokens=500, completion_tokens=0, total_tokens=500)
    adapter = SpyAdapter([AdapterResponse(parsed={"ok": 1}, usage=usage)])
    ctx = _ctx(store, _settings(), adapter)
    await governed_generate(ctx, stage=TaskStage.SETUP, messages=MESSAGES)
    rebuilt = GovernanceBudget.for_canvas(store, _settings(), "c")
    assert rebuilt.run.tokens_committed == 0  # a new run starts with a fresh envelope
    assert rebuilt.run.exhausted_reason is None
    # Canvas totals are reconstructed from the durable audit log.
    assert rebuilt._canvas.tokens_committed == 500  # type: ignore[attr-defined]


async def test_route_falls_back_when_preferred_adapter_is_not_constructed(store):
    settings = _settings(routing_table={"setup": ["claude", "openai"]})
    adapter = SpyAdapter([AdapterResponse(parsed={"ok": 1})])
    ctx = _ctx(store, settings, adapter)
    await governed_generate(ctx, stage=TaskStage.SETUP, messages=MESSAGES)
    event = _events(store, "model_call")[0]
    assert event.payload["provider"] == "openai"
    assert event.payload["fallback"] is True
    assert adapter.calls == 1


async def test_route_falls_back_when_preferred_denied_by_locality(store):
    settings = _settings(
        routing_table={"setup": ["claude", "openai"]},
        provider_data_classifications={"openai": ["public", "internal"], "claude": ["public"]},
    )
    adapter = SpyAdapter([AdapterResponse(parsed={"ok": 1})])
    ctx = _ctx(store, settings, adapter)  # classifications default [internal]
    await governed_generate(ctx, stage=TaskStage.SETUP, messages=MESSAGES)
    event = _events(store, "model_call")[0]
    assert event.payload["provider"] == "openai"
    assert event.payload["fallback"] is True


async def test_ambiguous_timeout_is_not_blindly_resubmitted(store):
    adapter = SpyAdapter([TimeoutError("provider timeout")])
    context = _ctx(store, _settings(), adapter)
    with pytest.raises(TimeoutError):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    assert context.budget.run.tokens_reserved > 0  # uncertain submission retains its durable hold
    with pytest.raises(ModelCallInFlightError, match="reconciliation unsupported"):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    assert adapter.calls == 1


async def test_duplicate_submit_prevented_after_ambiguous_failure(store):
    adapter = SpyAdapter([TimeoutError("always times out")])
    with pytest.raises(TimeoutError):
        await governed_generate(_ctx(store, _settings(), adapter), stage=TaskStage.SETUP, messages=MESSAGES)
    with pytest.raises(ModelCallInFlightError, match="reconciliation unsupported"):
        await governed_generate(_ctx(store, _settings(), adapter), stage=TaskStage.SETUP, messages=MESSAGES)
    assert adapter.calls == 1


async def test_governed_adapter_routes_generate_through_policy(store):
    usage = Usage("openai", "gpt-4o-mini", total_tokens=5)
    adapter = SpyAdapter([AdapterResponse(parsed={"ok": 1}, usage=usage)])
    governed = GovernedAdapter(_ctx(store, _settings(), adapter))
    resp = await governed.generate(MESSAGES, response_schema={"type": "object"}, schema_name="ExperimentSetup")
    assert resp.parsed == {"ok": 1}
    assert len(_events(store, "model_call")) == 1
