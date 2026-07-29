"""Browser artifact delivery across the live fake Canvus contract."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from starlette.testclient import TestClient

from lab_agent.artifact_server import create_artifact_app
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.orchestrator_support import write_setup_node
from tests.integration.fake_mcp_server import FakeCanvusServer


def _settings() -> Settings:
    return Settings(artifact_public_base_url="https://lab.test")


def _setup(round_index: int) -> ExperimentSetup:
    return ExperimentSetup(
        rationale=f"round {round_index}", conditions=["temperature=25C"],
        steps=["measure signal"], expected_readouts=["signal"],
        hypothesis="signal is measurable", success_criteria=["record signal"],
    )


async def test_browser_artifact_delivers_updated_version_without_widget_recreation(store) -> None:
    canvas = FakeCanvusServer().canvas("canvas-browser")
    canvas.mcp.seed_widget("idea", "Note", text="{idea: test experiment}")
    first = await write_setup_node(
        canvas.mcp, store, _settings(), canvas_id=canvas.canvas_id, setup=_setup(1),
        idea_text="test experiment", idea_id="idea", round_index=1,
        predecessor_id="idea", edge_kind="idea_setup",
    )
    first_url = canvas.mcp.notes[first]["url"]
    second = await write_setup_node(
        canvas.mcp, store, _settings(), canvas_id=canvas.canvas_id, setup=_setup(2),
        idea_text="test experiment", idea_id="idea", round_index=2,
        predecessor_id="idea", edge_kind="idea_setup",
    )

    assert second == first
    assert canvas.browser_ids() == {first}
    assert canvas.mcp.notes[first]["url"] != first_url
    document = ArtifactStore(store.conn).get_artifact_by_widget(
        canvas_id=canvas.canvas_id, widget_id=first
    )
    assert document is not None and document.current_version == 2
    parsed = urlparse(canvas.mcp.notes[first]["url"])
    response = TestClient(create_artifact_app(ArtifactStore(store.conn))).get(
        parsed.path, params=parse_qs(parsed.query)
    )
    assert response.status_code == 200
    assert "round 2" in response.text
