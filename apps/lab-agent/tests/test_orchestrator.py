"""Tests for the experiment-loop orchestrator against in-memory fakes.

Durable dedup: Phase 1's in-memory ``processed_loops`` set is gone -- every
test drives a real (migrated, tmp-file) ``StateStore``, and "already handled"
is asserted by calling ``process_once`` twice against the *same* store, the
way ``watch.py`` really achieves idempotency across restarts.
"""

from __future__ import annotations

import pytest

from lab_agent.config import Settings
from lab_agent.loop_governance import reconcile_terminal_event
from lab_agent.models.governance import StopReason, TerminalStopEvent
from lab_agent.orchestrator import run_loop
from lab_agent.state_store import StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup

RUNTIME_ID = "test-runtime"

# Generated Setup/Result/Closed nodes are now capability-protected Browser
# widgets backed by the ArtifactStore, so a public base URL must be configured
# or the write fails closed (see test_browser_artifacts.py for that path).

def _settings(**kw):
    return Settings(artifact_public_base_url="https://lab.test", **kw)  # type: ignore[call-arg]

SETUP = grounded_setup()
RESULT = {"summary": "A+B reduced marker 30%", "metrics": ["reduction=0.30"]}

LOOP = {
    "loop_connector_id": "c5",
    "setup_id": "setup1",
    "result_id": "result1",
    "robot_id": "robot1",
    "idea_id": "idea1",
    "ragcluster_id": "rag1",
    "round": 1,
}
SEED = {
    "idea1": "{idea: Combine A with B}",
    "setup1": "Round: 1\nmix A and B",
    "result1": "marker reduced",
}

@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()

def _structured(decisions):
    return {"ExperimentSetup": SETUP, "ExperimentResult": RESULT, "LoopDecision": decisions}

async def test_loop_stages_grounded_mock_successor_then_waits_for_result(store):
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter(_structured([
        {"proceed": True, "reason": "promising", "next_focus": "raise dose"},
    ]))
    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))  # type: ignore[arg-type]

    assert summary.rounds == 1 and summary.stopped_reason == "successor_staged"
    assert not summary.closed_id and len(summary.setup_ids) == 2
    assert summary.result_ids == ["result1"]
    assert adapter.schema_calls == ["LoopDecision", "ExperimentSetup"]
    assert ("result1", summary.setup_ids[-1]) in mcp.connectors
    assert not any(src == "robot1" for src, _ in mcp.connectors)

async def test_loop_backstop_forces_terminal_stop(store):
    """Ungoverned mode: max_rounds backstop creates a terminal closure, but still
    processes exactly one decision per call (no successor generation)."""
    mcp = FakeMCP(note_text=dict(SEED))
    settings = _settings()  # type: ignore[call-arg]
    settings.loop_max_rounds = 1
    adapter = ScriptedAdapter(_structured([{"proceed": True, "reason": "keep going"}]))
    summary = await run_loop(mcp, adapter, settings, store, canvas_id="c", loop=dict(LOOP))

    # Backstop triggers *after* decision is processed, creating a terminal closure.
    assert summary.rounds == 1
    assert summary.closed_id  # Terminal Closed node written
    assert "max_rounds" in summary.stopped_reason
    assert len(summary.setup_ids) == 1  # No successor setup generated
    assert len(summary.result_ids) == 1  # No successor result generated

async def test_loop_model_decision_creates_terminal_closure(store):
    """Model decision to STOP (proceed=False) creates a terminal Closed node
    only if min_rounds is met. Otherwise returns deferred."""
    mcp = FakeMCP(note_text=dict(SEED))
    settings = _settings()
    settings.loop_min_rounds = 1  # Allow closure on first decision
    adapter = ScriptedAdapter(_structured([
        {"proceed": False, "reason": "plateau detected", "next_focus": ""},
    ]))
    summary = await run_loop(mcp, adapter, settings, store, canvas_id="c", loop=dict(LOOP))

    assert summary.rounds == 1
    assert summary.closed_id  # Terminal closure created because min_rounds met
    assert "model_decision" in summary.stopped_reason or "plateau" in summary.stopped_reason
    assert len(summary.setup_ids) == 1  # No successor
    assert len(summary.result_ids) == 1  # No successor

async def test_loop_replays_staged_successor_without_side_effects(store):
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter(_structured([
        {"proceed": True, "reason": "continue", "next_focus": "raise dose"},
    ]))
    first = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))
    calls, notes, connectors = list(adapter.schema_calls), len(mcp.notes), len(mcp.connectors)

    replay = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))

    assert replay.stopped_reason == first.stopped_reason == "successor_staged"
    assert replay.setup_ids[-1] == first.setup_ids[-1]
    assert adapter.schema_calls == calls
    assert len(mcp.notes) == notes and len(mcp.connectors) == connectors

async def test_legacy_terminal_audit_prevents_round_trigger_reopening(store):
    event = TerminalStopEvent(
        canvas_id="c", trigger_id="loop:c5", predecessor_id="result1",
        reason=StopReason.MODEL_DECISION, round_index=1, closure_id="closed-legacy",
    )
    assert reconcile_terminal_event(store, _settings(), event)
    adapter = ScriptedAdapter(_structured([{"proceed": True, "reason": "must not run"}]))
    summary = await run_loop(
        FakeMCP(note_text=dict(SEED)), adapter, _settings(), store,
        canvas_id="c", loop=dict(LOOP),
    )
    assert summary.closed_id == "closed-legacy" and adapter.schema_calls == []

async def test_process_once_generates_setup_from_idea(store):
    workflow = {
        "ideas_needing_setup": [{"widget_id": "idea1", "ragcluster_id": "rag1"}],
        "setups_needing_run": [],
        "loops": [],
    }
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=workflow)
    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "n/a"}]))
    counts = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")  # type: ignore[arg-type]

    assert counts["setups"] == 1
    # idea1 -> newly created setup note connector exists.
    assert any(src == "idea1" for src, _ in mcp.connectors)

async def test_process_once_does_not_run_robot_connected_setup(store):
    workflow = {
        "ideas_needing_setup": [],
        "setups_needing_run": [
            {"widget_id": "setup1", "robot_id": "robot1", "title": "[EXP:Setup v001]"},
        ],
        "loops": [],
    }
    mcp = FakeMCP(note_text={"setup1": "mix A and B"}, workflow=workflow)
    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "n/a"}]))
    counts = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")  # type: ignore[arg-type]

    assert counts["runs"] == 0
    assert not any(src == "robot1" for src, _ in mcp.connectors)

async def test_process_once_leaves_loops_for_phase_eight(store):
    workflow = {"ideas_needing_setup": [], "setups_needing_run": [], "loops": [dict(LOOP)]}
    mcp = FakeMCP(note_text=dict(SEED), workflow=workflow)
    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "done"}]))
    first = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")  # type: ignore[arg-type]
    second = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")
    assert first["loops"] == second["loops"] == 0
    assert store.get_attempt("c", "loop:c5") is None

async def test_live_rescan_does_not_process_legacy_robot_loop(store):
    """Phase 7 observes Canvas topology but never turns it into execution."""
    mcp = FakeMCP(live=True)
    mcp.seed_widget("rag1", "Image", title="RAGCluster_lung")
    mcp.seed_widget("idea1", "Note", text="{idea: Combine A with B}")
    mcp.seed_widget("setup1", "Note", title="[EXP:Setup v001] A+B", text="Round: 1\nmix A and B")
    mcp.seed_widget("robot1", "Note", title="Robot_arm")
    mcp.seed_widget("result1", "Note", title="[EXP:Result v001]", text="marker reduced")
    mcp.seed_connector("rag1", "idea1")
    mcp.seed_connector("idea1", "setup1")
    mcp.seed_connector("setup1", "robot1")
    mcp.seed_connector("robot1", "result1")
    mcp.seed_connector("result1", "setup1")

    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "unused"}]))
    first = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")
    second = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")

    assert first == second == {"setups": 0, "runs": 0, "loops": 0, "validations": 0}
    assert [w for w in mcp.notes if w.startswith("note")] == []
