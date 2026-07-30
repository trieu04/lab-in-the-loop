"""Focused safety tests for the manual model-intent retry operation."""

from __future__ import annotations

import asyncio

import pytest

from lab_agent import admin, cli
from lab_agent.config import Settings
from lab_agent.runtime import RuntimeContext
from lab_agent.state.intents import ModelIntentNotEligibleForRetryError
from lab_agent.state_store import IntentStatus, StateStore
from lab_agent.tenant import TenantContext

RUNTIME_ID = "operator-test"
INTENT_KEY = "a" * 64


def _ctx(store: StateStore) -> RuntimeContext:
    return RuntimeContext(store=store, runtime_instance_id=RUNTIME_ID, settings=Settings())


def _terminal_provider_failure(
    store: StateStore,
    *,
    canvas_id: str = "canvas-a",
    key: str = INTENT_KEY,
    kind: str = "model_call",
) -> None:
    store.prepare_intent(
        idempotency_key=key, canvas_id=canvas_id, kind=kind, input_hash="safe-hash"
    )
    store.mark_intent_submitted(key, canvas_id=canvas_id)
    store.mark_intent_failed(
        key, canvas_id=canvas_id, error="provider_error", next_retry_at=None
    )


def test_reset_model_intent_retries_only_terminal_provider_failure(store: StateStore) -> None:
    _terminal_provider_failure(store)

    reset = store.reset_failed_model_intent(INTENT_KEY, canvas_id="canvas-a")

    assert reset.status is IntentStatus.PENDING
    assert reset.attempt_count == 0
    assert reset.last_error is None
    assert reset.next_retry_at is None
    assert reset.external_id is None
    assert reset.reconciled_at is None


@pytest.mark.parametrize(
    ("error", "next_retry_at", "external_id"),
    [
        ("ambiguous:provider_error", None, None),
        ("provider_error", 1_000_001.0, None),
        ("provider_error", None, "request-123"),
    ],
)
def test_reset_model_intent_rejects_ambiguous_retryable_and_submitted_evidence(
    store: StateStore, error: str, next_retry_at: float | None, external_id: str | None
) -> None:
    _terminal_provider_failure(store)
    store.conn.execute(
        "UPDATE side_effect_intents SET last_error=?, next_retry_at=?, external_id=? "
        "WHERE tenant_id=? AND canvas_id=? AND idempotency_key=?",
        (error, next_retry_at, external_id, "default", "canvas-a", INTENT_KEY),
    )
    store.conn.commit()

    with pytest.raises(ModelIntentNotEligibleForRetryError, match="not eligible"):
        store.reset_failed_model_intent(INTENT_KEY, canvas_id="canvas-a")


def test_reset_model_intent_rejects_executed_reconciled_unknown_and_cross_canvas(
    store: StateStore,
) -> None:
    _terminal_provider_failure(store)
    store.mark_intent_executed(INTENT_KEY, canvas_id="canvas-a", external_id="request-123")
    with pytest.raises(ModelIntentNotEligibleForRetryError, match="not eligible"):
        store.reset_failed_model_intent(INTENT_KEY, canvas_id="canvas-a")

    store.mark_intent_reconciled(INTENT_KEY, canvas_id="canvas-a")
    with pytest.raises(ModelIntentNotEligibleForRetryError, match="not eligible"):
        store.reset_failed_model_intent(INTENT_KEY, canvas_id="canvas-a")

    _terminal_provider_failure(store, canvas_id="canvas-b", key="b" * 64)
    with pytest.raises(ModelIntentNotEligibleForRetryError, match="not eligible"):
        store.reset_failed_model_intent("b" * 64, canvas_id="canvas-a")
    with pytest.raises(ModelIntentNotEligibleForRetryError, match="not eligible"):
        store.reset_failed_model_intent("c" * 64, canvas_id="canvas-a")

    _terminal_provider_failure(store, key="d" * 64, kind="create_note")
    with pytest.raises(ModelIntentNotEligibleForRetryError, match="not eligible"):
        store.reset_failed_model_intent("d" * 64, canvas_id="canvas-a")


def test_reset_model_intent_is_tenant_scoped(tmp_path, clock, rng) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, clock=clock, rng=rng, tenant_context=TenantContext("tenant-a", ["canvas-a"], "a.example"))
    second = StateStore(path, clock=clock, rng=rng, tenant_context=TenantContext("tenant-b", ["canvas-a"], "b.example"))
    try:
        _terminal_provider_failure(first)
        _terminal_provider_failure(second)

        first.reset_failed_model_intent(INTENT_KEY, canvas_id="canvas-a")

        assert first.get_intent(INTENT_KEY, canvas_id="canvas-a").status is IntentStatus.PENDING
        assert second.get_intent(INTENT_KEY, canvas_id="canvas-a").status is IntentStatus.FAILED
    finally:
        first.close()
        second.close()


def test_admin_retry_model_intent_appends_safe_operator_audit_event(store: StateStore, capsys) -> None:
    _terminal_provider_failure(store)

    assert admin.retry_model_intent(_ctx(store), "canvas-a", INTENT_KEY) == 0
    assert capsys.readouterr().out == "Model intent retry queued.\n"
    events = [e for e in store.list_audit_events("canvas-a") if e.event == "operator_model_intent_retry"]
    assert len(events) == 1
    assert events[0].payload == {"intent_key_prefix": INTENT_KEY[:16]}
    store.verify_audit_chain()


def test_admin_retry_model_intent_rejects_ineligible_without_audit(store: StateStore, capsys) -> None:
    _terminal_provider_failure(store)
    store.mark_intent_ambiguous(
        INTENT_KEY, canvas_id="canvas-a", error="ambiguous:provider_error", external_id=None
    )

    assert admin.retry_model_intent(_ctx(store), "canvas-a", INTENT_KEY) == 1
    assert capsys.readouterr().out == "ERROR: model intent is not eligible for retry.\n"
    assert not [e for e in store.list_audit_events("canvas-a") if e.event == "operator_model_intent_retry"]


def test_cli_parser_requires_a_canvas_and_full_intent_key() -> None:
    args = cli._build_parser().parse_args(
        ["retry-model-intent", "--canvas", "canvas-a", "--intent", INTENT_KEY]
    )
    assert args.command == "retry-model-intent"
    assert args.canvas == "canvas-a"
    assert args.intent == INTENT_KEY

    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(["retry-model-intent", "--canvas", "canvas-a"])
    with pytest.raises(SystemExit):
        cli._build_parser().parse_args(
            ["retry-model-intent", "--canvas", "canvas-a", "--intent", "short-id"]
        )


def test_cli_retry_model_intent_dispatches_without_starting_a_provider(
    monkeypatch, store: StateStore, capsys
) -> None:
    _terminal_provider_failure(store)
    args = cli._build_parser().parse_args(
        ["retry-model-intent", "--canvas", "canvas-a", "--intent", INTENT_KEY]
    )
    ctx = _ctx(store)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings())
    monkeypatch.setattr(cli, "build_runtime_context", lambda _settings: ctx)
    monkeypatch.setattr(cli, "close_runtime_context", lambda _ctx: None)

    assert asyncio.run(cli._run(args)) == 0
    assert capsys.readouterr().out == "Model intent retry queued.\n"
    assert store.get_intent(INTENT_KEY, canvas_id="canvas-a").status is IntentStatus.PENDING
