"""Browser-artifact write-path integration tests.

Generated Setup/Result/Closed nodes are capability-protected Browser widgets
backed by the canonical :class:`~lab_agent.artifact_store.ArtifactStore`, while
ideas stay Note-only. These drive the real orchestrator write helpers against a
migrated on-disk ``StateStore`` and ``FakeMCP`` -- no mocks of the harness.
"""

from __future__ import annotations

import json

import pytest

from lab_agent import durable_browser, nodes
from lab_agent.artifact_store import ArtifactStore
from lab_agent.canvas_probe import tagged_title
from lab_agent.config import Settings
from lab_agent.durable_browser import ArtifactUrlError
from lab_agent.models.artifact import ArtifactType
from lab_agent.orchestrator import generate_setup, run_loop
from lab_agent.recovery import idempotency_key
from lab_agent.state_store import AttemptStatus, StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter

BASE = "https://lab.test"
SETUP = {"rationale": "because", "steps": ["mix A and B"], "inputs": ["A", "B"]}
RESULT = {"summary": "reduced 30%", "metrics": ["reduction=0.30"]}
IDEA_WORKFLOW = {
    "ideas_needing_setup": [{"widget_id": "idea1", "ragcluster_id": "rag1"}],
    "setups_needing_run": [], "loops": [],
}


def _settings(**kw):
    return Settings(artifact_public_base_url=BASE, **kw)  # type: ignore[call-arg]


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


async def _make_setup(mcp, store, **kw):
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    return await generate_setup(
        mcp, adapter, _settings(), store, canvas_id="c", idea_text="try X",
        idea_id="idea1", ragcluster_id="rag1", round_index=1, **kw,
    )


async def test_generated_setup_is_a_browser_artifact_with_url_connector_and_store_row(store):
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="{idea: try X}")
    setup_id, setup = await _make_setup(mcp, store)

    assert setup is not None and setup_id
    widget = mcp.notes[setup_id]
    assert widget["widget_type"] == "Browser"  # generated artifact is a Browser, not a Note
    assert widget["title"].startswith("[EXP:Setup v001]")  # exact marker preserved
    assert len(widget["title"]) <= 120  # title/tag length contract
    assert widget["url"].startswith(f"{BASE}/artifacts/") and "?token=" in widget["url"]  # capability URL path
    assert ("idea1", setup_id) in mcp.connectors  # idea -> setup connector
    assert mcp.notes["idea1"]["widget_type"] == "Note"  # the idea itself stays Note-only

    astore = ArtifactStore(store.conn)
    doc = astore.get_artifact_by_widget(canvas_id="c", widget_id=setup_id)
    assert doc is not None
    assert doc.artifact_type == ArtifactType.SETUP and doc.widget_id == setup_id
    assert doc.payload["rationale"] == "because"  # structured payload, not only rendered text
    assert doc.payload["title"].startswith("[EXP:Setup v001]")
    assert doc.provenance.provider == "openai"  # actual configured provider/model
    assert doc.provenance.model_name == "gpt-4o-mini"
    assert doc.provenance.source_widget_id == "idea1"
    assert doc.provenance.trigger_id == "setup/predecessor:idea1/round:1"


async def test_generated_result_and_closed_are_browser_artifacts(store):
    mcp = FakeMCP(note_text={"idea1": "{idea: A+B}", "setup1": "Round: 1\nmix", "result1": "marker reduced"})
    adapter = ScriptedAdapter({
        "ExperimentSetup": SETUP, "ExperimentResult": RESULT,
        "LoopDecision": [{"proceed": True, "reason": "go", "next_focus": "x"}, {"proceed": False, "reason": "done"}],
    })
    loop = {
        "loop_connector_id": "c5", "setup_id": "setup1", "result_id": "result1",
        "robot_id": "robot1", "idea_id": "idea1", "ragcluster_id": "rag1", "round": 1,
    }
    summary = await run_loop(mcp, adapter, _settings(), store, canvas_id="c", loop=loop)

    astore = ArtifactStore(store.conn)
    result2_id = summary.result_ids[1]
    assert mcp.notes[result2_id]["widget_type"] == "Browser"
    assert mcp.notes[result2_id]["title"].startswith("[EXP:Result v002]")
    rdoc = astore.get_artifact_by_widget(canvas_id="c", widget_id=result2_id)
    assert rdoc is not None and rdoc.artifact_type == ArtifactType.RESULT
    assert rdoc.payload["summary"] == "reduced 30%"

    closed = mcp.notes[summary.closed_id]
    assert closed["widget_type"] == "Browser"
    assert closed["title"].startswith("[EXP:Closed] after v002")
    cdoc = astore.get_artifact_by_widget(canvas_id="c", widget_id=summary.closed_id)
    assert cdoc is not None and cdoc.artifact_type == ArtifactType.CLOSED
    assert cdoc.payload["proceed"] is False


async def test_missing_public_base_url_fails_closed_before_browser_call(store):
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="{idea: try X}")
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    with pytest.raises(ArtifactUrlError):
        await generate_setup(
            mcp, adapter, Settings(), store, canvas_id="c", idea_text="try X",
            idea_id="idea1", ragcluster_id="rag1", round_index=1,
        )
    assert [w for w in mcp.notes.values() if w["widget_type"] == "Browser"] == []  # no Browser created


async def test_missing_public_base_url_leaves_attempt_retryable(store):
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    counts = await process_once(mcp, adapter, Settings(), store, "rt1", "c")  # empty base URL

    assert counts["setups"] == 0
    assert [w for w in mcp.notes.values() if w["widget_type"] == "Browser"] == []
    attempt = store.get_attempt("c", "idea_setup:idea1")
    assert attempt is not None
    assert attempt.status == AttemptStatus.FAILED  # retryable, not completed


async def test_restart_converges_on_existing_browser_and_repairs_token_url(store):
    """Worst-case crash: a prior process created the setup Browser (tagged) with
    a now-stale token URL but never mapped/connected it locally. Recovery must
    converge on that one widget, repair its URL, map it, and draw one connector
    -- one artifact, one Browser, one connector."""
    canvas_id, idea_id, round_index = "c", "idea1", 1
    discriminator = f"setup/predecessor:{idea_id}/round:{round_index}"
    browser_key = idempotency_key(canvas_id, "create_browser", f"browser/setup/{discriminator}")
    prior_title = tagged_title(f"{nodes.EXP_SETUP} v001] try X", browser_key)
    mcp = FakeMCP(workflow={"setups": [{"widget_id": "setup-prior", "widget_type": "Browser", "title": prior_title}]})
    mcp.seed_widget(idea_id, "Note", text="{idea: try X}")
    mcp.seed_widget("setup-prior", "Browser", title=prior_title, url=f"{BASE}/artifacts/stale?token=old")

    setup_id, _ = await _make_setup(mcp, store)

    assert setup_id == "setup-prior"  # converged, no duplicate widget
    browsers = [w for w in mcp.notes.values() if w["widget_type"] == "Browser"]
    assert len(browsers) == 1
    assert mcp.notes["setup-prior"]["url"].startswith(f"{BASE}/artifacts/")
    assert "stale" not in mcp.notes["setup-prior"]["url"]  # token URL repaired
    assert mcp.connectors == [("idea1", "setup-prior")]  # exactly one connector
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1  # one canonical artifact
    astore = ArtifactStore(store.conn)
    doc = astore.get_artifact_by_widget(canvas_id=canvas_id, widget_id="setup-prior")
    assert doc is not None and doc.widget_id == "setup-prior"


async def test_read_stage_text_prefers_artifact_then_falls_back_to_legacy_note(store):
    mcp = FakeMCP()
    mcp.seed_widget("legacy", "Note", title="[EXP:Setup v001]", text="legacy body text")
    # Legacy Note (no artifact mapped) -> falls back to the Note body.
    assert await durable_browser.read_stage_text(mcp, store, "c", "legacy") == "legacy body text"

    mcp.seed_widget("idea1", "Note", text="{idea: try X}")
    setup_id, _ = await _make_setup(mcp, store)
    # Browser-backed setup -> reads the rendered body from the canonical store,
    # never the Browser HTML.
    text = await durable_browser.read_stage_text(mcp, store, "c", setup_id)
    assert "Idea: idea1" in text and "Round: 1" in text


async def test_no_audit_event_leaks_token_or_capability_url(store):
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    await process_once(mcp, adapter, _settings(), store, "rt1", "c")

    for event in store.list_audit_events("c"):
        blob = json.dumps(event.payload)
        assert "token=" not in blob  # capability token never audited
        assert "/artifacts/" not in blob  # capability URL never audited
        assert BASE not in blob
