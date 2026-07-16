"""Terminal event and closure reconciliation contracts."""

from __future__ import annotations

import pytest

from lab_agent.config import Settings
from lab_agent.loop_governance import close_with_reason
from lab_agent.model_gateway import GovernedAdapter, build_context
from lab_agent.models.governance import StopReason, TerminalStopEvent
from lab_agent.orchestrator import run_loop
from lab_agent.orchestrator_support import LoopSummary
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        _env_prefix="__TEST_NO_ENV__",
        artifact_public_base_url="https://lab.test",
    )


async def test_terminal_event_is_redacted_deterministic_and_reconciled(store, settings) -> None:
    mcp = FakeMCP(note_text={"result1": "sensitive setup and model result"})
    summary = LoopSummary(rounds=1, result_ids=["result1"])
    first = await close_with_reason(
        mcp,
        settings,
        store,
        summary,
        canvas_id="canvas",
        trigger_id="loop:loop-1",
        result_id="result1",
        round_index=1,
        reason=StopReason.MAX_ROUNDS,
    )
    second = await close_with_reason(
        mcp,
        settings,
        store,
        summary,
        canvas_id="canvas",
        trigger_id="loop:loop-1",
        result_id="result1",
        round_index=1,
        reason=StopReason.MAX_ROUNDS,
    )

    assert first == second == TerminalStopEvent(
        canvas_id="canvas",
        trigger_id="loop:loop-1",
        predecessor_id="result1",
        reason=StopReason.MAX_ROUNDS,
        round_index=1,
        closure_id=first.closure_id,
    )
    assert first.notification_eligible
    assert first.audit_payload == {
        "canvas_id": "canvas",
        "trigger_id": "loop:loop-1",
        "predecessor_id": "result1",
        "reason": "max_rounds",
        "round": 1,
        "closure_id": first.closure_id,
        "notification_eligible": True,
    }
    assert "sensitive" not in repr(first.audit_payload)
    stopped = [event for event in store.list_audit_events("canvas") if event.event == "loop_stopped"]
    assert len(stopped) == 1
    assert stopped[0].payload == first.audit_payload


async def test_locality_denial_closes_before_provider_or_follow_on_writes(store, settings) -> None:
    settings.provider_endpoints = {"openai": "https://api.openai.com/v1"}
    settings.provider_data_classifications = {"openai": ["public"]}
    inner = ScriptedAdapter({"LoopDecision": {"proceed": True, "reason": "continue"}})
    gov = build_context(store, settings, "canvas", {"openai": inner}, [{"classification": "internal"}])
    mcp = FakeMCP(note_text={"setup1": "safe", "result1": "safe"})

    summary = await run_loop(
        mcp, GovernedAdapter(gov), settings, store, canvas_id="canvas",
        loop={"loop_connector_id": "loop-1", "setup_id": "setup1", "result_id": "result1", "round": 1},
        gov=gov,
    )

    assert summary.stopped_reason == StopReason.LOCALITY_DENIAL.value
    assert inner.schema_calls == []
    assert len(mcp.notes) == 3
    assert len(mcp.connectors) == 1
    stopped = [event for event in store.list_audit_events("canvas") if event.event == "loop_stopped"]
    assert len(stopped) == 1
    assert stopped[0].payload["reason"] == StopReason.LOCALITY_DENIAL.value


async def test_schema_failure_is_not_a_notification_eligible_terminal_event(store, settings) -> None:
    settings.provider_endpoints = {"openai": "https://api.openai.com/v1"}
    settings.model_pricing = {"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}}
    settings.pricing_version = "test-v1"
    inner = ScriptedAdapter({"LoopDecision": {"next_focus": "invalid schema"}})
    gov = build_context(store, settings, "canvas", {"openai": inner}, [{"classification": "internal"}])
    mcp = FakeMCP(note_text={"setup1": "safe", "result1": "safe"})

    summary = await run_loop(
        mcp, GovernedAdapter(gov), settings, store, canvas_id="canvas",
        loop={"loop_connector_id": "loop-1", "setup_id": "setup1", "result_id": "result1", "round": 1},
        gov=gov,
    )

    assert summary.stopped_reason == "schema_validation_failed"
    assert inner.schema_calls == ["LoopDecision"]
    assert len(mcp.notes) == 2
    assert not [event for event in store.list_audit_events("canvas") if event.event == "loop_stopped"]


async def test_continue_generates_successor_then_terminal_event(store, settings) -> None:
    mcp = FakeMCP(note_text={"setup1": "safe", "result1": "safe"})
    adapter = ScriptedAdapter({
        "ExperimentSetup": grounded_setup(),
        "ExperimentResult": {"summary": "synthetic result", "metrics": ["signal=1"]},
        "LoopDecision": [
            {"proceed": True, "reason": "continue", "next_focus": "raise dose"},
            {"proceed": False, "reason": "complete"},
        ],
    })
    summary = await run_loop(
        mcp, adapter, settings, store, canvas_id="canvas",
        loop={"loop_connector_id": "loop-1", "setup_id": "setup1", "result_id": "result1", "round": 1},
    )

    assert summary.rounds == 2
    assert len(summary.setup_ids) == len(summary.result_ids) == 2
    assert summary.closed_id and summary.stopped_reason == StopReason.MODEL_DECISION.value
    assert adapter.schema_calls == ["LoopDecision", "ExperimentSetup", "ExperimentResult", "LoopDecision"]
    stopped = [event for event in store.list_audit_events("canvas") if event.event == "loop_stopped"]
    assert len(stopped) == 1
    assert stopped[0].payload["reason"] == StopReason.MODEL_DECISION.value
    assert stopped[0].payload["notification_eligible"] is True


async def test_model_stop_event_replays_without_provider_or_canvas_write(tmp_path, settings) -> None:
    db_path = tmp_path / "terminal-replay.db"
    loop = {"loop_connector_id": "loop-1", "setup_id": "setup1", "result_id": "result1", "round": 1}
    settings.provider_endpoints = {"openai": "https://api.openai.com/v1"}
    settings.model_pricing = {"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}}
    settings.pricing_version = "test-v1"
    settings.loop_min_rounds = 1
    mcp = FakeMCP(note_text={"setup1": "safe", "result1": "sensitive model result"})
    first_store = StateStore(db_path)
    try:
        first_adapter = ScriptedAdapter({"LoopDecision": {"proceed": False, "reason": "secret stop"}})
        first_gov = build_context(first_store, settings, "canvas", {"openai": first_adapter}, [{"classification": "internal"}])
        first = await run_loop(mcp, GovernedAdapter(first_gov), settings, first_store, canvas_id="canvas", loop=loop, gov=first_gov)
    finally:
        first_store.close()

    replay_store = StateStore(db_path)
    try:
        replay_adapter = ScriptedAdapter({"LoopDecision": {"proceed": False, "reason": "unused"}})
        replay_gov = build_context(replay_store, settings, "canvas", {"openai": replay_adapter}, [{"classification": "internal"}])
        replay = await run_loop(mcp, GovernedAdapter(replay_gov), settings, replay_store, canvas_id="canvas", loop=loop, gov=replay_gov)
        stopped = [event for event in replay_store.list_audit_events("canvas") if event.event == "loop_stopped"]
    finally:
        replay_store.close()

    assert first.closed_id == replay.closed_id
    assert first.stopped_reason == replay.stopped_reason == StopReason.MODEL_DECISION.value
    assert first_adapter.schema_calls == ["LoopDecision"]
    assert replay_adapter.schema_calls == []
    assert len(mcp.notes) == 3
    assert len(mcp.connectors) == 1
    assert len(stopped) == 1
    assert stopped[0].payload["reason"] == StopReason.MODEL_DECISION.value
    assert stopped[0].payload["notification_eligible"] is True
    assert stopped[0].payload["trigger_id"] == "loop:loop-1"
    assert "secret" not in repr(stopped[0].payload)
