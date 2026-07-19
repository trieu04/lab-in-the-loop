"""Durable intent boundaries for governed provider calls."""

from __future__ import annotations

import pytest

from lab_agent.adapters.base import (
    AdapterResponse,
    AmbiguousProviderError,
    DeterministicProviderError,
    TransientProviderError,
    Usage,
)
from lab_agent.config import Settings
from lab_agent.model_gateway import (
    ModelCallInFlightError,
    PostResponseBudgetExceededError,
    build_context,
    governed_generate,
)
from lab_agent.model_request import request_digest
from lab_agent.models.governance import TaskStage
from lab_agent.recovery import idempotency_key
from tests.test_model_gateway import MESSAGES, PRICING, SpyAdapter


def _settings(**overrides: object) -> Settings:
    values = {
        "openai_api_key": "key", "pricing_version": "v1", "model_pricing": PRICING,
        "provider_endpoints": {"openai": "https://openai.example"},
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _context(store, adapter: SpyAdapter, **overrides: object):
    return build_context(
        store, _settings(**overrides), "canvas", {"openai": adapter}, [{"classification": "internal"}]
    )


async def test_reconciled_intent_is_never_redispatched(store):
    adapter = SpyAdapter([AdapterResponse(parsed={"ok": True})])
    context = _context(store, adapter)
    await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    with pytest.raises(RuntimeError, match="already reconciled"):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    assert adapter.calls == 1


async def test_next_retry_at_prevents_early_redispatch(store, clock):
    adapter = SpyAdapter([TransientProviderError("rate limited")])
    context = _context(store, adapter)
    with pytest.raises(TransientProviderError):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    with pytest.raises(RuntimeError, match="not due"):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    assert adapter.calls == 1
    clock.advance(10.0)
    with pytest.raises(TransientProviderError):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    assert adapter.calls == 2


async def test_deterministic_errors_do_not_schedule_retry(store):
    adapter = SpyAdapter([DeterministicProviderError("bad credentials")])
    context = _context(store, adapter)
    with pytest.raises(DeterministicProviderError):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    intent = store.list_incomplete_intents("canvas")[0]
    assert intent.next_retry_at is None


async def test_ambiguous_errors_are_visible_without_status_reconciliation(store):
    adapter = SpyAdapter([AmbiguousProviderError("timed out", "provider-request")])
    context = _context(store, adapter)
    with pytest.raises(AmbiguousProviderError):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    intent = store.list_incomplete_intents("canvas")[0]
    assert intent.next_retry_at is None
    assert intent.external_id == "provider-request"
    assert "timed out" not in (intent.last_error or "")
    with pytest.raises(ModelCallInFlightError, match="reconciliation unsupported"):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    assert adapter.calls == 1


async def test_reconciled_overshoot_stops_before_orchestration_receives_response(store) -> None:
    class Reconciler:
        calls = 0
        reconciliations = 0

        async def generate(self, *args: object, **kwargs: object) -> AdapterResponse:
            self.calls += 1
            raise AmbiguousProviderError("request outcome uncertain", "provider-request")

        async def reconcile_submission(
            self, *, idempotency_key: str, request_id: str | None
        ) -> AdapterResponse | None:
            self.reconciliations += 1
            return AdapterResponse(
                parsed={"ok": True},
                usage=Usage("openai", "gpt-4o-mini", total_tokens=2_000),
            )

    adapter = Reconciler()
    context = build_context(
        store,
        _settings(run_token_budget=1_000, model_max_output_tokens=1),
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )
    digest = request_digest(MESSAGES, None, None, "result")
    key = idempotency_key("canvas", "model_call:unscoped:setup:round:0", digest)
    store.prepare_intent(idempotency_key=key, canvas_id="canvas", kind="model_call", input_hash=digest)
    store.mark_intent_submitted(key)

    with pytest.raises(PostResponseBudgetExceededError):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)

    assert adapter.calls == 0
    assert adapter.reconciliations == 1
