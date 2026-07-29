"""End-to-end loop coverage through live Canvus graph detection."""

from __future__ import annotations

from lab_agent.config import Settings
from lab_agent.orchestrator import run_loop
from tests.fakes import ScriptedAdapter
from tests.integration.fake_mcp_server import FakeCanvusServer


def test_fake_server_recomputes_the_cross_app_workflow_contract() -> None:
    server = FakeCanvusServer()
    canvas = server.canvas("canvas-e2e")
    canvas.seed_experiment_loop()

    snapshot = canvas.scan()

    assert len(snapshot["loops"]) == 1
    assert snapshot["loops"][0]["setup_id"] == "setup"


async def test_closed_loop_is_written_back_to_the_live_canvus_graph(store) -> None:
    canvas = FakeCanvusServer().canvas("canvas-e2e")
    canvas.seed_experiment_loop()
    settings = Settings(artifact_public_base_url="https://lab.test", loop_min_rounds=1)
    adapter = ScriptedAdapter({"LoopDecision": {"proceed": False, "reason": "complete"}})
    loop = canvas.scan()["loops"][0]

    summary = await run_loop(
        canvas.mcp, adapter, settings, store, canvas_id=canvas.canvas_id, loop=dict(loop)
    )

    snapshot = canvas.scan()
    assert summary.stopped_reason == "model_decision"
    assert summary.closed_id in {item["widget_id"] for item in snapshot["closeds"]}
    assert adapter.schema_calls == ["LoopDecision"]
