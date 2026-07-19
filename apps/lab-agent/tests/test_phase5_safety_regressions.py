"""Regressions for Phase 5 persistence and unavailable-usage boundaries."""

from __future__ import annotations

import json

import pytest

from lab_agent.adapters.base import AdapterResponse, AmbiguousProviderError, ToolSpec
from lab_agent.config import Settings
from lab_agent.model_gateway import build_context, governed_generate
from lab_agent.model_request import estimate_usage
from lab_agent.models.governance import TaskStage
from lab_agent.policy import BudgetExceededError
from lab_agent.watch_attempts import process_trigger

PRICING = {"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}}
MESSAGES = [{"role": "user", "content": "ordinary prompt"}]


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openai_api_key": "test-key",
        "model_pricing": PRICING,
        "pricing_version": "test-v1",
        "provider_endpoints": {"openai": "https://openai.example"},
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class FailingAdapter:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    async def generate(self, *args: object, **kwargs: object) -> AdapterResponse:
        self.calls += 1
        raise self.error


async def test_process_trigger_never_persists_provider_error_or_prompt(store) -> None:
    prompt = "PROMPT-5A9C-AFTER-REVIEW"
    provider_error = "PROVIDER-ERROR-4B2D-AFTER-REVIEW"
    adapter = FailingAdapter(AmbiguousProviderError(provider_error, "provider-request"))
    context = build_context(
        store,
        _settings(),
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )

    async def work() -> tuple[bool, str]:
        await governed_generate(
            context,
            stage=TaskStage.SETUP,
            messages=[{"role": "user", "content": prompt}],
        )
        return True, ""

    assert not await process_trigger(
        store,
        canvas_id="canvas",
        trigger_id="trigger",
        runtime_instance_id="worker",
        settings=_settings(),
        work=work,
    )

    attempt = store.get_attempt("canvas", "trigger")
    intent = store.list_incomplete_intents("canvas")[0]
    persisted = json.dumps(
        {
            "attempt": attempt.last_error if attempt else None,
            "intent": {
                "input_hash": intent.input_hash,
                "last_error": intent.last_error,
                "external_id": intent.external_id,
            },
            "audit": [event.payload for event in store.list_audit_events("canvas")],
        },
        sort_keys=True,
    )
    assert prompt not in persisted
    assert provider_error not in persisted
    assert attempt is not None and attempt.last_error == "provider_ambiguous"


async def test_tool_and_schema_payload_reservation_blocks_undersized_budget(store) -> None:
    tool = ToolSpec("large_tool", "x" * 900, {"type": "object", "properties": {"x": {"description": "y" * 900}}})
    schema = {"type": "object", "properties": {"result": {"description": "z" * 900}}}
    baseline = estimate_usage("openai", "gpt-4o-mini", MESSAGES, max_completion_tokens=8)
    full = estimate_usage(
        "openai",
        "gpt-4o-mini",
        MESSAGES,
        tools=[tool],
        response_schema=schema,
        max_completion_tokens=8,
    )
    assert full.prompt_tokens > baseline.prompt_tokens
    assert full.total_tokens > baseline.total_tokens

    adapter = FailingAdapter(RuntimeError("must not dispatch"))
    context = build_context(
        store,
        _settings(run_token_budget=baseline.total_tokens + 1, model_max_output_tokens=8),
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )
    with pytest.raises(BudgetExceededError):
        await governed_generate(
            context,
            stage=TaskStage.SETUP,
            messages=MESSAGES,
            tools=[tool],
            response_schema=schema,
        )
    assert adapter.calls == 0


async def test_known_pricing_allows_an_uncapped_dispatch(store) -> None:
    class RespondingAdapter:
        calls = 0

        async def generate(self, *args: object, **kwargs: object) -> AdapterResponse:
            self.calls += 1
            return AdapterResponse(parsed={"ok": True})

    adapter = RespondingAdapter()
    context = build_context(
        store,
        _settings(model_max_output_tokens=8),
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )

    response = await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)

    assert response.parsed == {"ok": True}
    assert adapter.calls == 1


async def test_unknown_pricing_denies_without_cost_cap(store) -> None:
    adapter = FailingAdapter(RuntimeError("must not dispatch"))
    context = build_context(
        store,
        _settings(model_pricing={}),
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )

    with pytest.raises(BudgetExceededError, match="cost is unavailable"):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)

    assert adapter.calls == 0
    assert store.list_audit_events("canvas")[-1].payload["reason"] == "cost_unavailable"
