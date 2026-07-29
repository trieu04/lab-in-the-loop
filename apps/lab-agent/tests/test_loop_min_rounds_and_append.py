"""Successor staging preserves minimum-round and artifact-isolation behavior."""

from __future__ import annotations

import pytest

from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.model_gateway import build_context
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.orchestrator import run_loop
from lab_agent.orchestrator_payloads import setup_payload
from lab_agent.recovery import idempotency_key
from lab_agent.state_store import StateStore
from lab_agent.watch_loops import process_loops
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup

SETUP = grounded_setup()
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


def _settings(**values: object) -> Settings:
    return Settings(artifact_public_base_url="https://lab.test", **values)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path):
    state = StateStore(tmp_path / "state.db")
    yield state
    state.close()


async def test_loop_enforces_min_rounds_across_authorized_successor_results(store) -> None:
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter(
        {
            "ExperimentSetup": SETUP,
            "LoopDecision": [
                {"proceed": False, "reason": "early"},
                {"proceed": False, "reason": "final"},
            ],
        }
    )
    first = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))

    assert first.rounds == 1 and first.stopped_reason == "successor_staged"
    assert len(first.setup_ids) == 2 and first.result_ids == ["result1"]
    assert not first.closed_id
    assert adapter.schema_calls == ["LoopDecision", "ExperimentSetup"]

    mcp.seed_widget("result2", "Note", title="[EXP:Result v002]", text="marker reduced again")
    second_loop = {**LOOP, "setup_id": first.setup_ids[-1], "result_id": "result2", "round": 2}
    second = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=second_loop)

    assert second.rounds == 2 and second.stopped_reason == "model_decision"
    assert second.closed_id and adapter.schema_calls[-1] == "LoopDecision"
    stopped = [event for event in store.list_audit_events("c") if event.event == "loop_stopped"]
    assert len(stopped) == 1 and stopped[0].round == 2


async def test_loop_min_rounds_one_honors_immediate_stop(store) -> None:
    adapter = ScriptedAdapter({"LoopDecision": {"proceed": False, "reason": "stop"}})
    summary = await run_loop(
        FakeMCP(note_text=dict(SEED)),
        adapter,
        _settings(loop_min_rounds=1),
        store,
        canvas_id="c",
        loop=dict(LOOP),
    )
    assert summary.rounds == 1 and summary.closed_id
    assert summary.setup_ids == ["setup1"] and summary.result_ids == ["result1"]
    assert adapter.schema_calls == ["LoopDecision"]


async def test_successor_appends_to_mapped_legacy_setup_without_writing_result(store) -> None:
    mcp = FakeMCP(note_text={"idea1": "{idea: Combine A with B}"})
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001] old")
    mcp.seed_widget("result1", "Browser", title="[EXP:Result v001] old")
    artifacts = ArtifactStore(store.conn)
    title, payload = setup_payload(
        ExperimentSetup.model_validate({**SETUP, "rationale": "legacy setup"}),
        idea_text="Combine A with B",
        idea_id="idea1",
        round_index=1,
    )
    document = artifacts.create_artifact(
        canvas_id="c",
        idempotency_key=idempotency_key("c", "artifact_setup", "setup/predecessor:idea1/round:1"),
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.RUNNING,
        payload={**payload, "title": title},
        provenance=ArtifactProvenance(provider="openai", source_widget_id="idea1"),
        round=1,
    )
    artifacts.map_widget(document.opaque_id, canvas_id="c", widget_id="setup1")
    adapter = ScriptedAdapter(
        {
            "ExperimentSetup": SETUP,
            "LoopDecision": {"proceed": True, "reason": "go"},
            "ExperimentResult": {"summary": "must not run", "metrics": []},
        }
    )

    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=dict(LOOP))

    assert summary.setup_ids == ["setup1", "setup1"] and summary.result_ids == ["result1"]
    assert summary.stopped_reason == "successor_staged"
    assert adapter.schema_calls == ["LoopDecision", "ExperimentSetup"]
    assert (
        ArtifactStore(store.conn).get_artifact_by_widget(canvas_id="c", widget_id="setup1").round
        == 2
    )


async def test_two_unlinked_loops_do_not_share_staged_setup_artifacts(store) -> None:
    mcp = FakeMCP(
        note_text={
            "setupA": "Round: 1\nmix A",
            "resultA": "marker A",
            "setupB": "Round: 1\nmix B",
            "resultB": "marker B",
        }
    )

    async def stage(tag: str):
        loop = {
            "loop_connector_id": f"c{tag}",
            "setup_id": f"setup{tag}",
            "result_id": f"result{tag}",
            "robot_id": f"robot{tag}",
            "idea_id": "",
            "ragcluster_id": "rag1",
            "round": 1,
        }
        adapter = ScriptedAdapter(
            {
                "ExperimentSetup": SETUP,
                "LoopDecision": {"proceed": True, "reason": "go", "next_focus": "repeat"},
            }
        )
        return await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=loop)

    summary_a, summary_b = await stage("A"), await stage("B")
    assert summary_a.setup_ids[-1] != summary_b.setup_ids[-1]
    assert summary_a.result_ids == ["resultA"] and summary_b.result_ids == ["resultB"]


async def test_loop_workflow_attempts_are_round_qualified(store) -> None:
    mcp = FakeMCP(note_text=dict(SEED))
    settings = _settings()
    adapter = ScriptedAdapter(
        {
            "ExperimentSetup": SETUP,
            "LoopDecision": [
                {"proceed": False, "reason": "early"},
                {"proceed": False, "reason": "done"},
            ],
        }
    )
    gov = build_context(store, settings, "c", {}, [{"classification": "internal"}])

    assert await process_loops(mcp, adapter, settings, store, "one", "c", [dict(LOOP)], gov) == 1
    assert store.get_attempt("c", "loop:c5:round:1").status.value == "completed"
    assert await process_loops(mcp, adapter, settings, store, "two", "c", [dict(LOOP)], gov) == 0

    continuation = store.get_loop_continuation("c", "setup:setup1")
    assert continuation is not None
    mcp.seed_widget("result2", "Note", title="[EXP:Result v002]", text="second result")
    round_two = {
        **LOOP,
        "setup_id": continuation.staged_setup_id,
        "result_id": "result2",
        "round": 2,
    }
    assert await process_loops(mcp, adapter, settings, store, "three", "c", [round_two], gov) == 1
    assert store.get_attempt("c", "loop:c5:round:2").status.value == "completed"
