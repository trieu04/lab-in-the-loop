"""Durable submitted/in-flight model-call intent regressions."""

from __future__ import annotations

import pytest

from lab_agent.adapters.base import AdapterResponse, Usage
from lab_agent.config import Settings
from lab_agent.model_gateway import (
    ModelCallInFlightError,
    build_context,
    governed_generate,
)
from lab_agent.models.governance import TaskStage
from lab_agent.state_store import IntentStatus, StateStore
from tests.test_model_gateway import MESSAGES, PRICING, SpyAdapter


class ProviderCrash(BaseException):
    """Simulates process death outside normal provider exception handling."""


class CrashAdapter(SpyAdapter):
    async def generate(self, *args, **kwargs):
        self.calls += 1
        raise ProviderCrash("process terminated during provider call")


class ReconcileCrashStore(StateStore):
    def mark_intent_reconciled(self, idempotency_key: str, *, canvas_id: str | None = None, external_id: str | None = None):
        raise ProviderCrash("process terminated before reconciliation")


class RecoveringAdapter(CrashAdapter):
    def __init__(self, response: AdapterResponse) -> None:
        super().__init__([])
        self.response = response
        self.reconcile_calls = 0

    async def reconcile_submission(self, *, idempotency_key: str, request_id: str | None):
        self.reconcile_calls += 1
        return self.response


def _settings() -> Settings:
    return Settings(
        openai_api_key="key",
        model_pricing=PRICING,
        pricing_version="v1",
        provider_endpoints={"openai": "https://openai.example"},
    )


def _context(store: StateStore, adapter: SpyAdapter):
    return build_context(
        store,
        _settings(),
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )


def test_submitted_transition_is_visible_and_counts_one_dispatch(store) -> None:
    store.prepare_intent(
        idempotency_key="key", canvas_id="canvas", kind="model_call", input_hash="hash"
    )

    submitted = store.mark_intent_submitted("key")
    failed = store.mark_intent_failed("key", error="known failure")

    assert submitted.status is IntentStatus.SUBMITTED
    assert submitted.attempt_count == 1
    assert failed.status is IntentStatus.FAILED
    assert failed.attempt_count == 1
    assert store.list_incomplete_intents("canvas")[0].status is IntentStatus.FAILED


async def test_restart_blocks_redispatch_of_submitted_provider_call(tmp_path, clock, rng) -> None:
    db_path = tmp_path / "state.db"
    first = StateStore(db_path, clock=clock, rng=rng)
    crashing = CrashAdapter([])
    with pytest.raises(ProviderCrash):
        await governed_generate(_context(first, crashing), stage=TaskStage.SETUP, messages=MESSAGES)
    assert first.list_incomplete_intents("canvas")[0].status is IntentStatus.SUBMITTED
    first.close()

    restarted = StateStore(db_path, clock=clock, rng=rng)
    fresh = SpyAdapter([AdapterResponse(parsed={"duplicate": True})])
    try:
        with pytest.raises(ModelCallInFlightError):
            await governed_generate(
                _context(restarted, fresh), stage=TaskStage.SETUP, messages=MESSAGES
            )
        assert fresh.calls == 0
        assert restarted.list_incomplete_intents("canvas")[0].status is IntentStatus.SUBMITTED
    finally:
        restarted.close()


async def test_restart_blocks_redispatch_of_executed_unreconciled_call(
    tmp_path, clock, rng
) -> None:
    db_path = tmp_path / "state.db"
    first = ReconcileCrashStore(db_path, clock=clock, rng=rng)
    response = AdapterResponse(parsed={"ok": True})
    with pytest.raises(ProviderCrash):
        await governed_generate(
            _context(first, SpyAdapter([response])), stage=TaskStage.SETUP, messages=MESSAGES
        )
    assert first.list_incomplete_intents("canvas")[0].status is IntentStatus.EXECUTED
    first.close()

    restarted = StateStore(db_path, clock=clock, rng=rng)
    fresh = SpyAdapter([AdapterResponse(parsed={"duplicate": True})])
    try:
        with pytest.raises(ModelCallInFlightError):
            await governed_generate(
                _context(restarted, fresh), stage=TaskStage.SETUP, messages=MESSAGES
            )
        assert fresh.calls == 0
    finally:
        restarted.close()


async def test_capable_adapter_reconciles_submitted_call_once_after_restart(
    tmp_path, clock, rng
) -> None:
    db_path = tmp_path / "state.db"
    first = StateStore(db_path, clock=clock, rng=rng)
    with pytest.raises(ProviderCrash):
        await governed_generate(
            _context(first, CrashAdapter([])), stage=TaskStage.SETUP, messages=MESSAGES
        )
    intent_key = first.list_incomplete_intents("canvas")[0].idempotency_key
    first.close()

    response = AdapterResponse(
        parsed={"ok": True},
        usage=Usage("openai", "gpt-4o-mini", prompt_tokens=5, total_tokens=5, request_id="request-1"),
    )
    restarted = StateStore(db_path, clock=clock, rng=rng)
    recovery = RecoveringAdapter(response)
    try:
        assert await governed_generate(
            _context(restarted, recovery), stage=TaskStage.SETUP, messages=MESSAGES
        ) is response
        assert recovery.calls == 0
        assert recovery.reconcile_calls == 1
        assert restarted.get_intent(intent_key).status is IntentStatus.RECONCILED
        assert restarted.budget_totals("canvas")[:2] == (5, pytest.approx(0.005))
        committed = [e for e in restarted.list_audit_events("canvas") if e.event == "budget_committed"]
        assert len(committed) == 1
        assert "reservation_id" in committed[0].payload
        with pytest.raises(RuntimeError, match="already reconciled"):
            await governed_generate(_context(restarted, recovery), stage=TaskStage.SETUP, messages=MESSAGES)
        assert recovery.calls == 0
        assert recovery.reconcile_calls == 1
    finally:
        restarted.close()
