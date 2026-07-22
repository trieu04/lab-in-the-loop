"""Regression tests for two experiment-loop fixes:

* Issue 2 -- successive generated rounds accumulate as *versions* on a single
  setup widget and a single result widget (idea-/setup-scoped discriminators),
  not a fresh widget per round.
* Issue 3 -- an early model STOP is overridden until ``loop_min_rounds``
  experiments exist, biasing the loop toward more than one experiment. Backstops
  are never overridden (covered in test_loop_stops.py).

Both drive the real orchestrator against a migrated on-disk ``StateStore`` and
``FakeMCP`` with a public artifact base URL configured (generated nodes are
capability-protected Browser artifacts).
"""

from __future__ import annotations

import pytest

from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentResult, ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.orchestrator import run_loop
from lab_agent.orchestrator_payloads import result_payload, setup_payload
from lab_agent.recovery import idempotency_key
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup

SETUP = grounded_setup()
RESULT = {"summary": "A+B reduced marker 30%", "metrics": ["reduction=0.30"]}
LOOP = {
    "loop_connector_id": "c5", "setup_id": "setup1", "result_id": "result1",
    "robot_id": "robot1", "idea_id": "idea1", "ragcluster_id": "rag1", "round": 1,
}
SEED = {"idea1": "{idea: Combine A with B}", "setup1": "Round: 1\nmix A and B", "result1": "marker reduced"}


def _settings(**kw):
    return Settings(artifact_public_base_url="https://lab.test", **kw)  # type: ignore[call-arg]


def _structured(decisions):
    return {"ExperimentSetup": SETUP, "ExperimentResult": RESULT, "LoopDecision": decisions}


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


async def test_loop_enforces_min_rounds_when_model_stops_early(store):
    """Model wants to stop at round 1; loop_min_rounds (default 2) forces a
    second experiment before the loop is allowed to close."""
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "early stop"}]))
    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))

    assert summary.rounds == 2  # early STOP overridden up to the minimum
    assert len(summary.setup_ids) == 2  # seed setup1 + one generated
    assert summary.closed_id  # still closes once the minimum is met


async def test_loop_min_rounds_one_honors_immediate_stop(store):
    """With loop_min_rounds=1 the same early STOP ends after one experiment --
    proving loop_min_rounds, not chance, drives the extra round above."""
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter(_structured([{"proceed": False, "reason": "early stop"}]))
    summary = await run_loop(mcp, adapter, _settings(loop_min_rounds=1), store, canvas_id="c", loop=dict(LOOP))

    assert summary.rounds == 1
    assert len(summary.setup_ids) == 1  # no generated round


async def test_later_rounds_append_versions_into_one_setup_and_result_widget(store):
    """Three experiments -> exactly one generated setup widget and one generated
    result widget, each carrying an append-only [round2, round3] version history."""
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter(_structured([
        {"proceed": True, "reason": "go", "next_focus": "raise dose"},
        {"proceed": True, "reason": "again", "next_focus": "raise more"},
        {"proceed": False, "reason": "done"},
    ]))
    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))

    assert summary.rounds == 3
    generated_setups = set(summary.setup_ids[1:])  # drop seed setup1
    generated_results = set(summary.result_ids[1:])  # drop seed result1
    assert len(generated_setups) == 1  # one accumulating setup widget, not two
    assert len(generated_results) == 1  # one accumulating result widget, not two

    astore = ArtifactStore(store.conn)
    setup_doc = astore.get_artifact_by_widget(canvas_id="c", widget_id=next(iter(generated_setups)))
    result_doc = astore.get_artifact_by_widget(canvas_id="c", widget_id=next(iter(generated_results)))
    assert setup_doc is not None and result_doc is not None
    setup_versions = astore.list_versions(setup_doc.opaque_id, canvas_id="c")
    result_versions = astore.list_versions(result_doc.opaque_id, canvas_id="c")
    assert [v.payload.get("round") for v in setup_versions] == [2, 3]  # appended, oldest first
    assert [v.payload.get("round") for v in result_versions] == [2, 3]


async def test_loop_appends_to_mapped_legacy_v1_setup_and_result(store):
    """Pre-upgrade round-scoped Browser artifacts mapped to the seed widgets must
    become the accumulating histories for round 2, not be forked into new widgets."""
    mcp = FakeMCP(note_text={"idea1": "{idea: Combine A with B}"})
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001] old")
    mcp.seed_widget("result1", "Browser", title="[EXP:Result v001] old")
    adapter = ScriptedAdapter(_structured([
        {"proceed": True, "reason": "go", "next_focus": "raise dose"},
        {"proceed": False, "reason": "done"},
    ]))
    astore = ArtifactStore(store.conn)
    setup_title, setup_payload_v1 = setup_payload(
        ExperimentSetup.model_validate({**SETUP, "rationale": "legacy setup"}),
        idea_text="Combine A with B", idea_id="idea1", round_index=1,
    )
    setup_doc = astore.create_artifact(
        canvas_id="c", idempotency_key=idempotency_key("c", "artifact_setup", "setup/predecessor:idea1/round:1"),
        artifact_type=ArtifactType.SETUP, state=DecisionState.RUNNING,
        payload={**setup_payload_v1, "title": setup_title},
        provenance=ArtifactProvenance(provider="openai", source_widget_id="idea1", trigger_id="setup/predecessor:idea1/round:1"),
        round=1,
    )
    result_title, result_payload_v1 = result_payload(
        ExperimentResult.model_validate({"summary": "legacy result", "metrics": ["old=1"]}),
        setup_id="setup1", round_index=1,
    )
    result_doc = astore.create_artifact(
        canvas_id="c", idempotency_key=idempotency_key("c", "artifact_result", "result/setup:setup1/round:1"),
        artifact_type=ArtifactType.RESULT, state=DecisionState.ANALYSIS_COMPLETE,
        payload={**result_payload_v1, "title": result_title},
        provenance=ArtifactProvenance(provider="openai", source_widget_id="setup1", trigger_id="result/setup:setup1/round:1"),
        round=1,
    )
    astore.map_widget(setup_doc.opaque_id, canvas_id="c", widget_id="setup1")
    astore.map_widget(result_doc.opaque_id, canvas_id="c", widget_id="result1")

    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))

    assert summary.setup_ids == ["setup1", "setup1"]
    assert summary.result_ids == ["result1", "result1"]
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 3  # setup, result, closed
    setup_versions = astore.list_versions(setup_doc.opaque_id, canvas_id="c")
    result_versions = astore.list_versions(result_doc.opaque_id, canvas_id="c")
    assert [v.payload.get("round") for v in setup_versions] == [1, 2]
    assert [v.payload.get("round") for v in result_versions] == [1, 2]


async def test_two_unlinked_loops_do_not_share_setup_or_result_artifacts(store):
    """Two valid loops with no connected idea (empty idea_id) must NOT collapse
    onto a shared ``setup/idea:`` widget. Each loop's own identity scopes its
    accumulating setup (and, through it, its result), so histories never merge."""
    # One canvas / one FakeMCP so widget ids are globally unique, as on a real
    # server; the two loops differ only by their own identity, not their idea.
    mcp = FakeMCP(note_text={
        "setupA": "Round: 1\nmix A", "resultA": "marker A",
        "setupB": "Round: 1\nmix B", "resultB": "marker B",
    })
    decisions = [{"proceed": True, "reason": "go", "next_focus": "raise dose"}, {"proceed": False, "reason": "done"}]

    async def run(tag):
        loop = {
            "loop_connector_id": f"c{tag}", "setup_id": f"setup{tag}", "result_id": f"result{tag}",
            "robot_id": f"robot{tag}", "idea_id": "", "ragcluster_id": "rag1", "round": 1,
        }
        adapter = ScriptedAdapter(_structured(list(decisions)))
        return await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=loop)

    summary_a = await run("A")
    summary_b = await run("B")

    gen_setup_a, gen_setup_b = summary_a.setup_ids[1], summary_b.setup_ids[1]
    gen_result_a, gen_result_b = summary_a.result_ids[1], summary_b.result_ids[1]
    assert gen_setup_a != gen_setup_b  # unlinked loops get distinct setup widgets
    assert gen_result_a != gen_result_b  # and therefore distinct result widgets

    astore = ArtifactStore(store.conn)
    setup_a = astore.get_artifact_by_widget(canvas_id="c", widget_id=gen_setup_a)
    setup_b = astore.get_artifact_by_widget(canvas_id="c", widget_id=gen_setup_b)
    assert setup_a is not None and setup_b is not None
    assert setup_a.opaque_id != setup_b.opaque_id  # separate canonical artifacts, no merged history
