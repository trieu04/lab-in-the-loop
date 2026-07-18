"""Tests for the on-demand ``[EXP:Needs Input]`` write stage.

``write_needs_input_node`` (``lab_agent.orchestrator_needs_input``) is not
wired into any attempt-driven flow -- it is callable on demand (e.g. an
ambiguous grounding or an approval gate) -- so these tests call it directly
rather than through ``watch.process_once``. Mirrors the setup/result/closed
coverage in ``test_browser_artifacts.py``: creation, the canonical
ArtifactStore record, the predecessor connector, cross-app marker detection
via the real canvus-mcp detector (``FakeMCP(live=True)``), restart/idempotency
convergence, and that idea/human-authored Notes are left untouched.
"""

from __future__ import annotations

import pytest

from lab_agent.artifact_store import ArtifactStore
from lab_agent.canvas_probe import tagged_title
from lab_agent.config import Settings
from lab_agent.durable_browser import ArtifactUrlError
from lab_agent.models.artifact import ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.orchestrator_needs_input import compute_reason_hash, write_needs_input_node
from lab_agent.recovery import idempotency_key
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP

BASE = "https://lab.test"


def _settings(**kw):
    return Settings(artifact_public_base_url=BASE, **kw)  # type: ignore[call-arg]


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


async def _write(mcp, store, **overrides):
    kw = {
        "canvas_id": "c", "message": "Proceed with option B or A?", "reason": "ambiguous grounding",
        "context": {"setup_id": "setup1"}, "round_index": 1, "predecessor_id": "setup1",
        "edge_kind": "setup_needs_input",
    }
    kw.update(overrides)
    return await write_needs_input_node(mcp, store, _settings(), **kw)


async def test_creates_browser_artifact_with_marker_url_and_connector(store):
    mcp = FakeMCP()
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001] A+B")

    widget_id = await _write(mcp, store)

    widget = mcp.notes[widget_id]
    assert widget["widget_type"] == "Browser"  # generated prompt is a Browser, not a Note
    assert widget["title"].startswith("[EXP:Needs Input] round 1")
    assert widget["url"].startswith(f"{BASE}/artifacts/") and "?token=" in widget["url"]
    assert ("setup1", widget_id) in mcp.connectors  # predecessor -> needs-input connector


async def test_canonical_artifact_record_uses_needs_review_state(store):
    mcp = FakeMCP()
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001] A+B")

    widget_id = await _write(mcp, store)

    astore = ArtifactStore(store.conn)
    doc = astore.get_artifact_by_widget(canvas_id="c", widget_id=widget_id)
    assert doc is not None
    assert doc.artifact_type == ArtifactType.NEEDS_INPUT
    assert doc.state == DecisionState.NEEDS_REVIEW  # safe, non-terminal -- no new state invented
    assert doc.payload["message"] == "Proceed with option B or A?"
    assert doc.payload["reason"] == "ambiguous grounding"
    assert doc.payload["context"] == {"setup_id": "setup1"}


async def test_missing_public_base_url_fails_closed_before_browser_call(store):
    mcp = FakeMCP()
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001] A+B")
    with pytest.raises(ArtifactUrlError):
        await write_needs_input_node(
            mcp, store, Settings(), canvas_id="c", message="m", reason="r", round_index=1,
            predecessor_id="setup1", edge_kind="setup_needs_input",
        )
    assert [w for w in mcp.notes.values() if "Needs Input" in w["title"]] == []


async def test_needs_input_widget_is_detected_via_needs_inputs_bucket(store):
    """Cross-app marker parity: the real canvus-mcp detector (not a fixture)
    must classify the generated widget into ``needs_inputs``."""
    mcp = FakeMCP(live=True)
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001] A+B")

    widget_id = await _write(mcp, store)

    import json

    raw = await mcp.call_tool("scan_experiment_workflow", {"canvas_id": "c"})
    snap = json.loads(raw)
    assert [b["widget_id"] for b in snap["needs_inputs"]] == [widget_id]


async def test_calling_twice_converges_on_one_widget_and_one_connector(store):
    """Idempotency without any attempt/lease machinery: the intent + artifact
    idempotency keys alone must make a direct repeat call converge."""
    mcp = FakeMCP()
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001] A+B")

    first_id = await _write(mcp, store)
    second_id = await _write(mcp, store)

    assert first_id == second_id
    browsers = [w for w in mcp.notes.values() if w["widget_type"] == "Browser" and "Needs Input" in w["title"]]
    assert len(browsers) == 1
    assert mcp.connectors.count(("setup1", first_id)) == 1
    assert store.conn.execute(
        "SELECT COUNT(*) FROM artifacts WHERE artifact_type=?", (ArtifactType.NEEDS_INPUT.value,)
    ).fetchone()[0] == 1


async def test_restart_converges_on_prior_browser_and_repairs_stale_url(store):
    """Worst case: a prior process's ``create_browser`` for the needs-input
    prompt landed on the canvas (tagged), but crashed before the local
    mapping/connector were recorded. Recovery must converge on that widget,
    repair its stale capability URL, and draw exactly one connector."""
    canvas_id, predecessor_id = "c", "setup1"
    reason_hash = compute_reason_hash("ambiguous grounding")
    discriminator = f"needs_input/predecessor:{predecessor_id}/reason:{reason_hash}"
    browser_key = idempotency_key(canvas_id, "create_browser", f"browser/needs_input/{discriminator}")
    prior_title = tagged_title("[EXP:Needs Input] round 1", browser_key)
    mcp = FakeMCP(
        workflow={"needs_inputs": [{"widget_id": "ni-prior", "widget_type": "Browser", "title": prior_title}]}
    )
    mcp.seed_widget(predecessor_id, "Browser", title="[EXP:Setup v001] A+B")
    mcp.seed_widget("ni-prior", "Browser", title=prior_title, url=f"{BASE}/artifacts/stale?token=old")

    widget_id = await _write(mcp, store)

    assert widget_id == "ni-prior"  # converged, no duplicate widget
    browsers = [w for w in mcp.notes.values() if w["widget_type"] == "Browser" and "Needs Input" in w["title"]]
    assert len(browsers) == 1
    assert mcp.notes["ni-prior"]["url"].startswith(f"{BASE}/artifacts/")
    assert "stale" not in mcp.notes["ni-prior"]["url"]  # token URL repaired in place
    assert mcp.connectors == [(predecessor_id, "ni-prior")]  # exactly one connector
    astore = ArtifactStore(store.conn)
    doc = astore.get_artifact_by_widget(canvas_id=canvas_id, widget_id="ni-prior")
    assert doc is not None and doc.artifact_type == ArtifactType.NEEDS_INPUT


async def test_idea_and_human_response_notes_are_never_touched(store):
    """The prompt is a generated Browser artifact; the idea and the human's
    response to the prompt stay plain Notes this helper never creates,
    edits, or reads."""
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="{idea: try X}")
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001] A+B")
    mcp.seed_widget("human-response1", "Note", text="Go ahead with option B")

    await _write(mcp, store)

    assert mcp.notes["idea1"] == {
        "id": "idea1", "widget_type": "Note", "title": "", "text": "{idea: try X}", "url": "",
    }
    assert mcp.notes["human-response1"] == {
        "id": "human-response1", "widget_type": "Note", "title": "", "text": "Go ahead with option B", "url": "",
    }
    assert mcp.connectors == [("setup1", [w for w in mcp.notes if "Needs Input" in mcp.notes[w]["title"]][0])]
