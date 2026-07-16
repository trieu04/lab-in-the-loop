"""Phase 5 gateway regressions for redaction, routing, and retry boundaries."""

from __future__ import annotations

import json

import pytest

from lab_agent.adapters.base import AdapterResponse, AmbiguousProviderError, TransientProviderError
from lab_agent.adapters.factory import get_adapters
from lab_agent.config import Settings
from lab_agent.model_gateway import ModelCallExhaustedError, build_context, governed_generate
from lab_agent.models.governance import TaskStage

MESSAGES = [{"role": "user", "content": "perform the governed operation"}]
PRICING = {
    "gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0},
    "claude-sonnet-4-5": {"input_per_1k": 1.0, "output_per_1k": 1.0},
}


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openai_api_key": "openai-test-key",
        "anthropic_api_key": "claude-test-key",
        "model_pricing": PRICING,
        "pricing_version": "v1",
        "provider_endpoints": {
            "openai": "https://openai.example",
            "claude": "https://claude.example",
        },
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class AmbiguousAdapter:
    async def generate(self, *args: object, **kwargs: object) -> AdapterResponse:
        raise AmbiguousProviderError("ERROR_SENTINEL", "provider-request")


class TransportProbe:
    def __init__(self) -> None:
        self.submissions = 0

    async def submit(self) -> AdapterResponse:
        self.submissions += 1
        return AdapterResponse(parsed={"unexpected": "transport submission"})


class PreSubmissionTransientAdapter:
    def __init__(self, transport: TransportProbe) -> None:
        self.gateway_invocations = 0
        self.transport = transport

    async def generate(self, *args: object, **kwargs: object) -> AdapterResponse:
        self.gateway_invocations += 1
        raise TransientProviderError("rate limited before transport submission")


async def test_ambiguous_error_redacts_durable_intent_and_audit_payloads(store) -> None:
    prompt = "PROMPT_SENTINEL"
    context = build_context(
        store,
        _settings(),
        "canvas",
        {"openai": AmbiguousAdapter()},
        [{"classification": "internal"}],
    )

    with pytest.raises(AmbiguousProviderError):
        await governed_generate(
            context,
            stage=TaskStage.SETUP,
            messages=[{"role": "user", "content": prompt}],
        )

    intent = store.list_incomplete_intents("canvas")[0]
    persisted = json.dumps(
        {"input_hash": intent.input_hash, "last_error": intent.last_error, "external_id": intent.external_id}
    )
    assert prompt not in persisted
    assert "ERROR_SENTINEL" not in persisted
    assert intent.last_error == "ambiguous:provider_error"
    assert intent.input_hash != prompt
    for event in store.list_audit_events("canvas"):
        payload = json.dumps(event.payload, sort_keys=True)
        assert prompt not in payload
        assert "ERROR_SENTINEL" not in payload


async def test_factory_built_adapters_route_without_post_dispatch_fallback(store, monkeypatch) -> None:
    settings = _settings(routing_table={"setup": ["claude", "openai"]})
    adapters = get_adapters(settings)
    claude_calls: list[bool] = []
    openai_calls: list[bool] = []

    async def claude_generate(*args: object, **kwargs: object) -> AdapterResponse:
        claude_calls.append(True)
        return AdapterResponse(parsed={"provider": "claude"})

    async def openai_generate(*args: object, **kwargs: object) -> AdapterResponse:
        openai_calls.append(True)
        return AdapterResponse(parsed={"provider": "openai"})

    monkeypatch.setattr(adapters["claude"], "generate", claude_generate)
    monkeypatch.setattr(adapters["openai"], "generate", openai_generate)
    context = build_context(store, settings, "canvas", adapters, [{"classification": "internal"}])

    response = await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)

    assert response.parsed == {"provider": "claude"}
    assert claude_calls == [True]
    assert openai_calls == []
    assert store.list_audit_events("canvas")[-1].payload["fallback"] is False

    blocked_settings = _settings(
        routing_table={"setup": ["claude", "openai"]},
        provider_data_classifications={"claude": ["public"], "openai": ["public", "internal"]},
    )
    blocked_adapters = get_adapters(blocked_settings)
    blocked_claude_calls: list[bool] = []
    blocked_openai_calls: list[bool] = []

    async def blocked_claude(*args: object, **kwargs: object) -> AdapterResponse:
        blocked_claude_calls.append(True)
        return AdapterResponse(parsed={"provider": "claude"})

    async def blocked_openai(*args: object, **kwargs: object) -> AdapterResponse:
        blocked_openai_calls.append(True)
        return AdapterResponse(parsed={"provider": "openai"})

    monkeypatch.setattr(blocked_adapters["claude"], "generate", blocked_claude)
    monkeypatch.setattr(blocked_adapters["openai"], "generate", blocked_openai)
    blocked_context = build_context(
        store, blocked_settings, "locality-canvas", blocked_adapters, [{"classification": "internal"}]
    )

    await governed_generate(blocked_context, stage=TaskStage.SETUP, messages=MESSAGES)

    assert set(blocked_adapters) == {"claude", "openai"}
    assert blocked_claude_calls == []
    assert blocked_openai_calls == [True]
    assert store.list_audit_events("locality-canvas")[-1].payload["fallback"] is True

    failed_adapters = get_adapters(settings)
    failed_claude_calls: list[bool] = []
    failed_openai_calls: list[bool] = []

    async def failed_claude(*args: object, **kwargs: object) -> AdapterResponse:
        failed_claude_calls.append(True)
        raise TransientProviderError("provider rate limit")

    async def failed_openai(*args: object, **kwargs: object) -> AdapterResponse:
        failed_openai_calls.append(True)
        return AdapterResponse(parsed={"provider": "openai"})

    monkeypatch.setattr(failed_adapters["claude"], "generate", failed_claude)
    monkeypatch.setattr(failed_adapters["openai"], "generate", failed_openai)
    failed_context = build_context(
        store, settings, "failed-canvas", failed_adapters, [{"classification": "internal"}]
    )

    with pytest.raises(TransientProviderError, match="provider rate limit"):
        await governed_generate(failed_context, stage=TaskStage.SETUP, messages=MESSAGES)

    assert failed_claude_calls == [True]
    assert failed_openai_calls == []


async def test_pre_submission_transient_retry_releases_holds_and_stops_at_attempt_bound(store, clock) -> None:
    transport = TransportProbe()
    adapter = PreSubmissionTransientAdapter(transport)
    settings = _settings(model_call_max_attempts=2, retry_base_seconds=5.0, retry_max_seconds=30.0)
    context = build_context(
        store, settings, "canvas", {"openai": adapter}, [{"classification": "internal"}]
    )

    with pytest.raises(TransientProviderError):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    first = store.list_incomplete_intents("canvas")[0]
    assert first.next_retry_at == clock() + 10.0
    assert store.budget_totals("canvas") == (0, 0.0, 0, 0.0)

    clock.advance(10.0)
    with pytest.raises(TransientProviderError):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    second = store.list_incomplete_intents("canvas")[0]
    assert second.attempt_count == 2
    assert second.next_retry_at == clock() + 20.0
    assert store.budget_totals("canvas") == (0, 0.0, 0, 0.0)

    clock.advance(20.0)
    with pytest.raises(ModelCallExhaustedError):
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)
    assert adapter.gateway_invocations == 2
    assert transport.submissions == 0
