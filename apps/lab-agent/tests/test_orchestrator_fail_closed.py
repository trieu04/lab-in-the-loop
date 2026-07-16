"""Fail-closed schema-validation tests for the experiment-loop orchestrator.

``coerce_or_fail`` must raise on a malformed structured output instead of
fabricating missing required fields, so the caller skips the write and leaves
the canvas pending -- never a half-written node, never a silently-swallowed
trigger. Companion module ``test_orchestrator.py`` covers the happy-path
loop/process behaviour. Generated nodes are Browser widgets, so a base URL is
configured; the fail-closed paths here stop *before* any write anyway.
"""

from __future__ import annotations

import pytest

from lab_agent.config import Settings
from lab_agent.models.evidence import GroundingDecision
from lab_agent.orchestrator import generate_setup, run_loop
from lab_agent.orchestrator_support import emit_result
from lab_agent.state_store import StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup

RUNTIME_ID = "test-runtime"

SETUP = grounded_setup()
# Missing the respective required field(s): ExperimentSetup needs "rationale",
# ExperimentResult needs "summary", LoopDecision needs "proceed" and "reason".
MALFORMED_SETUP = {"steps": ["mix A and B"]}
MALFORMED_RESULT = {"observations": ["looked fine"]}
MALFORMED_DECISION = {"next_focus": "raise dose"}

LOOP = {
    "loop_connector_id": "c5", "setup_id": "setup1", "result_id": "result1",
    "robot_id": "robot1", "idea_id": "idea1", "ragcluster_id": "rag1", "round": 1,
}
SEED = {
    "idea1": "{idea: Combine A with B}",
    "setup1": "Round: 1\nmix A and B",
    "result1": "marker reduced",
}


def _settings(**kw):
    return Settings(artifact_public_base_url="https://lab.test", **kw)  # type: ignore[call-arg]


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


async def test_generate_setup_fails_closed_on_malformed_output(store):
    mcp = FakeMCP()
    adapter = ScriptedAdapter({"ExperimentSetup": MALFORMED_SETUP})
    outcome = await generate_setup(
        mcp, adapter, _settings(), store, canvas_id="c", idea_text="try X",
        idea_id="idea1", ragcluster_id="rag1", round_index=1,
    )
    assert outcome.setup_id == ""
    assert outcome.setup is None
    assert outcome.decision is GroundingDecision.SCHEMA_FAILED
    assert mcp.notes == {}  # no widget written
    assert mcp.connectors == []  # no connector written


async def test_emit_result_fails_closed_on_malformed_output():
    adapter = ScriptedAdapter({"ExperimentResult": MALFORMED_RESULT})

    result = await emit_result(adapter, "mix A and B", settings=_settings())

    assert result is None
    assert adapter.schema_calls == ["ExperimentResult"]


async def test_run_loop_decision_fails_closed_returns_deferred(store):
    """Phase 2 contract: schema validation failure returns immediately with
    loop_continuation_deferred (no terminal closure, no successor generation)."""
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter({"LoopDecision": MALFORMED_DECISION})
    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))  # type: ignore[arg-type]

    assert summary.rounds == 0
    assert summary.closed_id == ""  # No terminal closure written
    assert summary.stopped_reason == "schema_validation_failed"
    assert adapter.schema_calls.count("LoopDecision") == 1  # One attempt only
    assert len(mcp.notes) == len(SEED)  # No new nodes written
    assert mcp.connectors == []  # No connectors written


async def test_process_once_setup_fails_closed_without_counting_or_connecting(store):
    workflow = {
        "ideas_needing_setup": [{"widget_id": "idea1", "ragcluster_id": "rag1"}],
        "setups_needing_run": [], "loops": [],
    }
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=workflow)
    adapter = ScriptedAdapter({"ExperimentSetup": MALFORMED_SETUP})
    counts = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")  # type: ignore[arg-type]

    assert counts["setups"] == 0  # skipped write is not counted a success
    assert mcp.connectors == []  # idea1 -> setup connector never drawn
    assert adapter.schema_calls.count("ExperimentSetup") == 1  # no retry this cycle


@pytest.mark.parametrize(
    ("structured", "failing_schema"),
    [
        pytest.param({"LoopDecision": MALFORMED_DECISION}, "LoopDecision", id="decision_stage"),
    ],
)
async def test_run_loop_schema_failure_returns_deferred_idempotently(
    store, structured, failing_schema,
):
    """Phase 2: run_loop() processes exactly one decision per call.
    Schema validation failure on the decision returns immediately with
    loop_continuation_deferred, no Canvas writes, minimal model calls. Repeated
    calls are idempotent—no partial writes, no orphaned connectors, no
    successor generation.

    Phase 2 never generates successor Setup/Result nodes, so downstream schema
    validation stages (round_advance_*) do not exist—only the decision stage is
    ever validated. This is idempotent: repeated calls on the same loop with the
    same schema failure accumulate no Canvas artifacts.
    """
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter(structured)

    # First call: schema validation fails at decision stage.
    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))  # type: ignore[arg-type]
    assert summary.stopped_reason == "schema_validation_failed"
    assert summary.closed_id == ""  # No terminal closure
    assert summary.rounds == 0  # Decision never validated
    assert len(mcp.notes) == len(SEED)  # Canvas unchanged
    assert mcp.connectors == []  # No edges written

    schema_calls_after_first = adapter.schema_calls.count(failing_schema)
    mcp_write_count_after_first = len(mcp.notes)

    # Second call: re-enter with same loop. Schema re-validated, but zero
    # additional side effects (idempotent). No new Canvas writes, no successors.
    summary2 = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))  # type: ignore[arg-type]
    assert summary2.stopped_reason == "schema_validation_failed"
    assert summary2.closed_id == ""  # Still no closure
    assert summary2.rounds == 0  # Unchanged
    assert len(mcp.notes) == mcp_write_count_after_first  # No new writes
    assert mcp.connectors == []  # Still no edges

    # Adapter called again for re-validation, but no other I/O.
    schema_calls_after_second = adapter.schema_calls.count(failing_schema)
    assert schema_calls_after_second == schema_calls_after_first + 1  # Exactly one more call
