"""Tests for the experiment-loop orchestrator against in-memory fakes.

Durable dedup: Phase 1's in-memory ``processed_loops`` set is gone -- every
test drives a real (migrated, tmp-file) ``StateStore``, and "already handled"
is asserted by calling ``process_once`` twice against the *same* store, the
way ``watch.py`` really achieves idempotency across restarts.
"""

from __future__ import annotations

import pytest

from lab_agent.config import Settings
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


async def test_loop_runs_until_model_stops(store):
    mcp = FakeMCP(note_text=dict(SEED))
    # round1: continue; round2: stop
    adapter = ScriptedAdapter(_structured([
        {"proceed": True, "reason": "promising", "next_focus": "raise dose"},
        {"proceed": False, "reason": "plateaued"},
    ]))
    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))  # type: ignore[arg-type]

    assert summary.rounds == 2
    assert len(summary.setup_ids) == 2  # seed setup1 + one generated
    assert len(summary.result_ids) == 2
    assert summary.closed_id
    assert "plateaued" in summary.stopped_reason
    # result1 -> setup2 chain edge, and result2 -> closed edge exist.
    assert any(src == "result1" for src, _ in mcp.connectors)


async def test_loop_backstop_forces_stop(store):
    mcp = FakeMCP(note_text=dict(SEED))
    settings = _settings()  # type: ignore[call-arg]
    settings.loop_max_rounds = 1
    adapter = ScriptedAdapter(_structured([{"proceed": True, "reason": "keep going"}]))
    summary = await run_loop(mcp, adapter, settings, store, canvas_id="c", loop=dict(LOOP))

    assert summary.rounds == 1
    assert len(summary.setup_ids) == 1  # no next round generated
    assert "backstop" in summary.stopped_reason


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


async def test_process_once_runs_pending_setup(store):
    workflow = {
        "ideas_needing_setup": [],
        "setups_needing_run": [{"widget_id": "setup1", "robot_id": "robot1", "title": "[EXP:Setup v001]"}],
        "loops": [],
    }
    mcp = FakeMCP(note_text={"setup1": "mix A and B"}, workflow=workflow)
    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "n/a"}]))
    counts = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")  # type: ignore[arg-type]

    assert counts["runs"] == 1
    assert any(src == "robot1" for src, _ in mcp.connectors)  # robot1 -> result


async def test_process_once_loop_idempotent_across_polls(store):
    workflow = {"ideas_needing_setup": [], "setups_needing_run": [], "loops": [dict(LOOP)]}
    mcp = FakeMCP(note_text=dict(SEED), workflow=workflow)
    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "done"}]))
    first = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")  # type: ignore[arg-type]
    second = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")
    assert first["loops"] == 1
    assert second["loops"] == 0  # durable attempt already completed -> not re-leased


async def test_live_rescan_after_loop_round_has_no_duplicate_or_actionable_loop(store):
    """Regression for the bug the round-filter fixes: without it, the
    orchestrator's own round-advance edge (result1 -> setup2) is
    graph-isomorphic to a user loop trigger and gets re-detected as a new
    actionable loop on the very next poll, pairing a stale result with a
    newer setup. `FakeMCP(live=True)` recomputes the snapshot the same way
    canvus-mcp does, so this exercises the real detector, not a static
    fixture that would hide the regression."""
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
    mcp.seed_connector("result1", "setup1")  # the user-drawn loop back-edge

    adapter = ScriptedAdapter(_structured([
        {"proceed": True, "reason": "promising", "next_focus": "raise dose"},
        {"proceed": False, "reason": "plateaued"},
    ]))
    settings = _settings()  # type: ignore[call-arg]

    first = await process_once(mcp, adapter, settings, store, RUNTIME_ID, "c")
    assert first == {"setups": 0, "runs": 0, "loops": 1}  # loop runs to closure (2 rounds)

    second = await process_once(mcp, adapter, settings, store, RUNTIME_ID, "c")
    assert second["loops"] == 0  # round-advance edge not mistaken for a new loop

    setup_titles = [w["title"] for w in mcp.notes.values() if w["title"].startswith("[EXP:Setup")]
    result_titles = [w["title"] for w in mcp.notes.values() if w["title"].startswith("[EXP:Result")]
    closed_titles = [w["title"] for w in mcp.notes.values() if w["title"].startswith("[EXP:Closed")]
    assert len(setup_titles) == 2  # setup1 (seed) + setup2 (generated) — no duplicate
    assert len(result_titles) == 2  # result1 (seed) + result2 (generated) — no duplicate
    assert len(closed_titles) == 1  # exactly one closed node
    assert any("v002" in t for t in setup_titles)
    assert any("v002" in t for t in result_titles)
    assert closed_titles[0].startswith("[EXP:Closed] after v002")
