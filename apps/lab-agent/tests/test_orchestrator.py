"""Tests for the experiment-loop orchestrator against in-memory fakes."""

from __future__ import annotations

from lab_agent.config import Settings
from lab_agent.orchestrator import run_loop
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter

SETUP = {"rationale": "because", "steps": ["mix A and B"], "inputs": ["A", "B"]}
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


def _structured(decisions):
    return {"ExperimentSetup": SETUP, "ExperimentResult": RESULT, "LoopDecision": decisions}


async def test_loop_runs_until_model_stops():
    mcp = FakeMCP(note_text=dict(SEED))
    # round1: continue; round2: stop
    adapter = ScriptedAdapter(_structured([
        {"proceed": True, "reason": "promising", "next_focus": "raise dose"},
        {"proceed": False, "reason": "plateaued"},
    ]))
    summary = await run_loop(mcp, adapter, Settings(), canvas_id="c", loop=dict(LOOP))  # type: ignore[arg-type]

    assert summary.rounds == 2
    assert len(summary.setup_ids) == 2  # seed setup1 + one generated
    assert len(summary.result_ids) == 2
    assert summary.closed_id
    assert "plateaued" in summary.stopped_reason
    # result1 -> setup2 chain edge, and result2 -> closed edge exist.
    assert any(src == "result1" for src, _ in mcp.connectors)


async def test_loop_backstop_forces_stop():
    mcp = FakeMCP(note_text=dict(SEED))
    settings = Settings()  # type: ignore[call-arg]
    settings.loop_max_rounds = 1
    adapter = ScriptedAdapter(_structured([{"proceed": True, "reason": "keep going"}]))
    summary = await run_loop(mcp, adapter, settings, canvas_id="c", loop=dict(LOOP))

    assert summary.rounds == 1
    assert len(summary.setup_ids) == 1  # no next round generated
    assert "backstop" in summary.stopped_reason


async def test_process_once_generates_setup_from_idea():
    workflow = {
        "ideas_needing_setup": [{"widget_id": "idea1", "ragcluster_id": "rag1"}],
        "setups_needing_run": [],
        "loops": [],
    }
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=workflow)
    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "n/a"}]))
    counts = await process_once(mcp, adapter, Settings(), "c", set())  # type: ignore[arg-type]

    assert counts["setups"] == 1
    # idea1 -> newly created setup note connector exists.
    assert any(src == "idea1" for src, _ in mcp.connectors)


async def test_process_once_runs_pending_setup():
    workflow = {
        "ideas_needing_setup": [],
        "setups_needing_run": [{"widget_id": "setup1", "robot_id": "robot1", "title": "[EXP:Setup v001]"}],
        "loops": [],
    }
    mcp = FakeMCP(note_text={"setup1": "mix A and B"}, workflow=workflow)
    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "n/a"}]))
    counts = await process_once(mcp, adapter, Settings(), "c", set())  # type: ignore[arg-type]

    assert counts["runs"] == 1
    assert any(src == "robot1" for src, _ in mcp.connectors)  # robot1 -> result


async def test_process_once_loop_idempotent_across_polls():
    workflow = {"ideas_needing_setup": [], "setups_needing_run": [], "loops": [dict(LOOP)]}
    mcp = FakeMCP(note_text=dict(SEED), workflow=workflow)
    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "done"}]))
    seen: set[str] = set()
    first = await process_once(mcp, adapter, Settings(), "c", seen)  # type: ignore[arg-type]
    second = await process_once(mcp, adapter, Settings(), "c", seen)
    assert first["loops"] == 1
    assert second["loops"] == 0  # same loop connector id skipped
