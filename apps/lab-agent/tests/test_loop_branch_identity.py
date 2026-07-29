"""Stable branch and terminal identity regressions for loop continuation."""

from __future__ import annotations

from lab_agent.config import Settings
from lab_agent.notification_smtp import SMTPConfiguration
from lab_agent.orchestrator import run_loop
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup


def _settings(*, minimum: int = 1) -> Settings:
    settings = Settings(
        _env_file=None, _env_prefix="__TEST_NO_ENV__",
        artifact_public_base_url="https://lab.test", loop_min_rounds=minimum,
    )
    settings.notification_smtp = SMTPConfiguration(
        enabled=True, host="smtp.test", port=587, sender="lab@lab.test",
        recipients=("operator@lab.test",), sender_allowlist=("lab@lab.test",),
        recipient_allowlist=("operator@lab.test",),
    )
    return settings


def _loop(setup: str, result: str, connector: str, *, round_index: int = 1) -> dict[str, object]:
    return {
        "loop_connector_id": connector, "setup_id": setup, "result_id": result,
        "robot_id": f"robot-{setup}", "idea_id": "shared-idea",
        "ragcluster_id": "rag", "round": round_index,
    }


async def test_connector_redraw_replays_one_stable_terminal_and_notification(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    mcp = FakeMCP(note_text={"shared-idea": "idea", "setup-a": "setup", "result-a": "result"})
    adapter = ScriptedAdapter({"LoopDecision": {"proceed": False, "reason": "done"}})
    try:
        first = await run_loop(
            mcp, adapter, _settings(), store, canvas_id="canvas",
            loop=_loop("setup-a", "result-a", "connector-old"),
        )
        replay = await run_loop(
            mcp, adapter, _settings(), store, canvas_id="canvas",
            loop=_loop("setup-a", "result-a", "connector-redrawn"),
        )

        assert replay.closed_id == first.closed_id and adapter.schema_calls == ["LoopDecision"]
        stopped = [event for event in store.list_audit_events("canvas") if event.event == "loop_stopped"]
        assert len(stopped) == 1
        assert stopped[0].payload["trigger_id"].startswith("loop-terminal:loop-run:")
        assert len(store.list_notification_records(canvas_id="canvas")) == 1
    finally:
        store.close()


async def test_later_round_observation_replays_closed_branch_without_model_call(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    mcp = FakeMCP(note_text={
        "shared-idea": "idea", "setup-a": "setup", "result-a": "result one",
        "result-a2": "result two",
    })
    adapter = ScriptedAdapter({"LoopDecision": {"proceed": False, "reason": "done"}})
    try:
        first = await run_loop(
            mcp, adapter, _settings(), store, canvas_id="canvas",
            loop=_loop("setup-a", "result-a", "connector-one"),
        )
        replay = await run_loop(
            mcp, adapter, _settings(), store, canvas_id="canvas",
            loop=_loop("setup-a", "result-a2", "connector-two", round_index=2),
        )
        assert replay.closed_id == first.closed_id
        assert replay.rounds == first.rounds == 1 and adapter.schema_calls == ["LoopDecision"]
    finally:
        store.close()


async def test_two_branches_sharing_one_idea_keep_separate_continuations(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    mcp = FakeMCP(note_text={
        "shared-idea": "idea", "setup-a": "setup a", "result-a": "result a",
        "setup-b": "setup b", "result-b": "result b",
    })
    adapter = ScriptedAdapter({
        "LoopDecision": {"proceed": False, "reason": "too early", "next_focus": "repeat"},
        "ExperimentSetup": grounded_setup(),
    })
    try:
        first = await run_loop(
            mcp, adapter, _settings(minimum=2), store, canvas_id="canvas",
            loop=_loop("setup-a", "result-a", "connector-a"),
        )
        second = await run_loop(
            mcp, adapter, _settings(minimum=2), store, canvas_id="canvas",
            loop=_loop("setup-b", "result-b", "connector-b"),
        )
        branch_a = store.get_loop_continuation("canvas", "setup:setup-a")
        branch_b = store.get_loop_continuation("canvas", "setup:setup-b")
        assert branch_a is not None and branch_b is not None
        assert branch_a.run_id != branch_b.run_id
        assert branch_a.staged_setup_id == first.setup_ids[-1]
        assert branch_b.staged_setup_id == second.setup_ids[-1]
        assert first.result_ids == ["result-a"] and second.result_ids == ["result-b"]
    finally:
        store.close()
