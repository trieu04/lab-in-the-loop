"""Failure, retry, and restart boundaries through durable production paths."""

from __future__ import annotations

from lab_agent import admin
from lab_agent.config import Settings
from lab_agent.runtime import RuntimeContext
from lab_agent.state_store import StateStore
from lab_agent.watch import process_once
from tests.fakes import ScriptedAdapter, grounded_setup
from tests.integration.fake_mcp_server import FakeCanvusServer


def _settings(**values: object) -> Settings:
    return Settings(artifact_public_base_url="https://lab.test", **values)


async def test_malformed_adapter_output_creates_a_visible_non_actionable_prompt(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db", rng=lambda: 0.0)
    canvas = FakeCanvusServer().canvas("canvas-failure")
    canvas.mcp.seed_widget("rag", "Image", title="RAGCluster_test")
    canvas.mcp.seed_widget("idea", "Note", text="{idea: test experiment}")
    canvas.mcp.seed_connector("rag", "idea")
    settings = _settings(max_attempts=1)
    try:
        failed = await process_once(
            canvas.mcp, ScriptedAdapter({"ExperimentSetup": {"rationale": "invalid"}}),
            settings, store, "worker", canvas.canvas_id,
        )
        assert failed["setups"] == 1
        titles = [item["title"] for item in canvas.mcp.notes.values()]
        assert any(title.startswith("[EXP:Needs Input]") for title in titles)
        assert not any(title.startswith("[EXP:Setup]") for title in titles)
    finally:
        store.close()


async def test_mcp_error_payload_reconciles_on_retry_without_duplicate_browser(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db", rng=lambda: 0.0)
    canvas = FakeCanvusServer().canvas("canvas-reconcile")
    canvas.mcp.seed_widget("rag", "Image", title="RAGCluster_test")
    canvas.mcp.seed_widget("idea", "Note", text="{idea: test experiment}")
    canvas.mcp.seed_connector("rag", "idea")
    settings = _settings(max_attempts=1)
    adapter = ScriptedAdapter({"ExperimentSetup": grounded_setup()})
    canvas.mcp.fail_next_as_error_payload("create_browser")
    try:
        assert (await process_once(canvas.mcp, adapter, settings, store, "worker", canvas.canvas_id))["setups"] == 0
        admin.reset_attempt(
            RuntimeContext(store=store, runtime_instance_id="worker", settings=settings),
            canvas.canvas_id, "idea_setup:idea",
        )
        assert (await process_once(canvas.mcp, adapter, settings, store, "worker", canvas.canvas_id))["setups"] == 1
        assert len(canvas.browser_ids()) == 1
    finally:
        store.close()
