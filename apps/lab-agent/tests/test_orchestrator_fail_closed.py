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
from lab_agent.orchestrator import generate_setup, run_loop, run_on_robot
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
SEED = {"idea1": "{idea: Combine A with B}", "setup1": "Round: 1\nmix A and B", "result1": "marker reduced"}


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


async def test_run_on_robot_fails_closed_on_malformed_output(store):
    mcp = FakeMCP()
    adapter = ScriptedAdapter({"ExperimentResult": MALFORMED_RESULT})
    result_id, result = await run_on_robot(
        mcp, adapter, _settings(), store, canvas_id="c", setup_id="setup1",
        setup_text="mix A and B", robot_id="robot1", round_index=1,
    )
    assert result_id == ""
    assert result is None
    assert mcp.notes == {}
    assert mcp.connectors == []


async def test_run_loop_decision_fails_closed_without_retry_or_close(store):
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter({"LoopDecision": MALFORMED_DECISION})
    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))  # type: ignore[arg-type]

    assert summary.rounds == 0
    assert summary.closed_id == ""
    assert summary.stopped_reason == "schema_validation_failed"
    assert adapter.schema_calls.count("LoopDecision") == 1  # no immediate inner-loop retry
    assert len(mcp.notes) == len(SEED)  # no new node (e.g. Closed) written
    assert mcp.connectors == []  # no connector written


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
        pytest.param(
            {
                "LoopDecision": {"proceed": True, "reason": "promising", "next_focus": "raise dose"},
                "ExperimentSetup": MALFORMED_SETUP,
            },
            "ExperimentSetup", id="round_advance_setup_stage",
        ),
        pytest.param(
            {
                "LoopDecision": {"proceed": True, "reason": "promising", "next_focus": "raise dose"},
                "ExperimentSetup": SETUP, "ExperimentResult": MALFORMED_RESULT,
            },
            "ExperimentResult", id="round_advance_result_stage",
        ),
    ],
)
async def test_process_once_loop_retries_after_schema_validation_failure(tmp_path, structured, failing_schema):
    """A schema-validation failure anywhere in a loop's cycle -- deciding, or
    generating the next round's setup or its paired result -- must not
    permanently swallow the loop connector: it must stay eligible so the very
    next poll retries it, instead of `processed_loops` orphaning it forever.

    `round_advance_result_stage` also regression-tests that the setup and result
    for a round-advance must both validate before either is written, so a
    result-stage failure does not strand a written-but-unpaired setup that gets
    rewritten every subsequent poll (AC-UC-LITL-02-004 / FR-LITL-019).

    Zero-jitter ``rng`` makes the durable backoff delay 0s, so the failed
    attempt's ``next_retry_at`` is immediately due on the very next poll.
    """
    store = StateStore(tmp_path / "state.db", rng=lambda: 0.0)
    workflow = {"ideas_needing_setup": [], "setups_needing_run": [], "loops": [dict(LOOP)]}
    mcp = FakeMCP(note_text=dict(SEED), workflow=workflow)
    adapter = ScriptedAdapter(structured)

    first = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")  # type: ignore[arg-type]
    assert first["loops"] == 0  # skipped write is not counted a success
    assert len(mcp.notes) == len(SEED)  # nothing written
    assert mcp.connectors == []
    first_calls = adapter.schema_calls.count(failing_schema)

    second = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")
    assert second["loops"] == 0  # still failing, still not counted
    # The adapter is invoked again on the next poll -- the failure is not
    # silently swallowed forever.
    assert adapter.schema_calls.count(failing_schema) == first_calls * 2
    store.close()
