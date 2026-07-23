"""Phase 7 keeps legacy loop recovery inert until Phase 8."""

from __future__ import annotations

from lab_agent.config import Settings
from lab_agent.state_store import StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter


async def test_watcher_does_not_recover_or_dispatch_crashed_robot_loop(tmp_path) -> None:
    """A stale Browser plus loop connector remains presentation-only in Phase 7."""

    canvas_id = "c"
    mcp = FakeMCP(
        workflow={
            "ideas_needing_setup": [],
            "setups_needing_run": [{"widget_id": "setup", "robot_id": "robot"}],
            "loops": [{"loop_connector_id": "loop", "setup_id": "setup", "result_id": "result"}],
        }
    )
    mcp.seed_widget("setup", "Note", title="[EXP:Setup v001]", text="mix A and B")
    mcp.seed_widget("robot", "Note", title="Robot_arm")
    mcp.seed_widget("result", "Note", title="[EXP:Result v001]", text="marker reduced")
    mcp.seed_widget("closed-prior", "Browser", title="[EXP:Closed] after v001", url="https://lab.test/artifacts/stale?token=old")
    store = StateStore(tmp_path / "state.db")
    try:
        counts = await process_once(
            mcp,
            ScriptedAdapter({"LoopDecision": {"proceed": False, "reason": "unused"}}),
            Settings(artifact_public_base_url="https://lab.test"),
            store,
            "rt1",
            canvas_id,
        )

        assert counts == {"setups": 0, "runs": 0, "loops": 0, "validations": 0}
        assert mcp.notes["closed-prior"]["url"].endswith("stale?token=old")
        assert store.get_attempt(canvas_id, "loop:loop") is None
        assert not mcp.connectors
    finally:
        store.close()
