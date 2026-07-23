"""Browser-artifact write-path integration tests.

Generated Setup/Result/Closed nodes are capability-protected Browser widgets
backed by the canonical :class:`~lab_agent.artifact_store.ArtifactStore`, while
ideas stay Note-only. These drive the real orchestrator write helpers against a
migrated on-disk ``StateStore`` and ``FakeMCP`` -- no mocks of the harness.
"""

from __future__ import annotations

import json

import pytest

from lab_agent import artifact_compat, durable_browser, nodes
from lab_agent.artifact_store import ArtifactStore
from lab_agent.canvas_probe import tagged_title
from lab_agent.config import Settings
from lab_agent.durable_browser import ArtifactUrlError
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentResult, ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.orchestrator import generate_setup, run_loop
from lab_agent.orchestrator_payloads import result_payload, setup_payload
from lab_agent.orchestrator_support import write_result_node, write_setup_node
from lab_agent.recovery import idempotency_key, input_hash
from lab_agent.state_store import AttemptStatus, IntentHashMismatchError, IntentStatus, StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup

BASE = "https://lab.test"
SETUP = grounded_setup()
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
    """Run ``generate_setup`` and unpack its ``SetupOutcome`` to the
    ``(setup_id, setup)`` shape this file's assertions expect."""
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    outcome = await generate_setup(
        mcp, adapter, _settings(), store, canvas_id="c", idea_text="try X",
        idea_id="idea1", ragcluster_id="rag1", round_index=1, **kw,
    )
    return outcome.setup_id, outcome.setup


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
    assert doc.provenance.model_name == _settings().openai_model
    assert doc.provenance.source_widget_id == "idea1"
    assert doc.provenance.trigger_id == "setup/predecessor:idea1/round:1"


async def test_generated_setup_appears_to_the_right_of_idea_geometry(store):
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="{idea: try X}", x=100.0, y=200.0, width=260.0, height=180.0)

    setup_id, _ = await _make_setup(mcp, store)

    assert mcp.notes[setup_id]["location"] == {"x": 400.0, "y": 200.0}


async def test_generated_setup_falls_back_when_offset_overflows(store):
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="{idea: try X}", x=1e308, y=200.0, width=1e308, height=180.0)

    setup_id, _ = await _make_setup(mcp, store)

    assert mcp.notes[setup_id]["location"] == {"x": 0.0, "y": 420.0}


async def test_mapped_setup_repair_preserves_manual_position(store):
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="{idea: try X}", x=100.0, y=200.0, width=260.0, height=180.0)
    mcp.seed_widget(
        "setup-existing", "Browser", title="[EXP:Setup v001] old", url=f"{BASE}/artifacts/stale?token=old",
        x=900.0, y=900.0, width=480.0, height=360.0,
    )
    setup_model = ExperimentSetup.model_validate(SETUP)
    _, payload = setup_payload(setup_model, idea_text="try X", idea_id="idea1", round_index=1)
    artifact_key = idempotency_key("c", "artifact_setup", "setup/idea:idea1")
    provenance = ArtifactProvenance(
        provider="openai", model_name=_settings().openai_model,
        source_widget_id="idea1", trigger_id="setup/predecessor:idea1/round:1",
    )
    astore = ArtifactStore(store.conn)
    doc = astore.create_artifact(
        canvas_id="c", idempotency_key=artifact_key, artifact_type=ArtifactType.SETUP,
        state=DecisionState.RUNNING, payload=payload, provenance=provenance, round=1,
    )
    astore.map_widget(doc.opaque_id, canvas_id="c", widget_id="setup-existing")

    setup_id, _ = await _make_setup(mcp, store)

    assert setup_id == "setup-existing"
    assert mcp.notes[setup_id]["url"].startswith(f"{BASE}/artifacts/")
    assert "stale" not in mcp.notes[setup_id]["url"]
    assert mcp.notes[setup_id]["location"] == {"x": 900.0, "y": 900.0}


async def test_legacy_mapped_setup_identity_is_reused_without_duplicate(store):
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="{idea: try X}", x=100.0, y=200.0, width=260.0, height=180.0)
    mcp.seed_widget("setup-existing", "Browser", title="[EXP:Setup v001] old", url=f"{BASE}/artifacts/stale?token=old")
    setup_model = ExperimentSetup.model_validate({**SETUP, "rationale": "old rationale"})
    _, payload = setup_payload(setup_model, idea_text="try X", idea_id="idea1", round_index=1)
    artifact_key = idempotency_key("c", "artifact_setup", "setup/predecessor:idea1/round:1")
    provenance = ArtifactProvenance(
        provider="openai", model_name=_settings().openai_model,
        source_widget_id="idea1", trigger_id="setup/predecessor:idea1/round:1",
    )
    astore = ArtifactStore(store.conn)
    doc = astore.create_artifact(
        canvas_id="c", idempotency_key=artifact_key, artifact_type=ArtifactType.SETUP,
        state=DecisionState.RUNNING, payload=payload, provenance=provenance, round=1,
    )
    astore.map_widget(doc.opaque_id, canvas_id="c", widget_id="setup-existing")

    setup_id, _ = await _make_setup(mcp, store)

    assert setup_id == "setup-existing"
    assert [w for w in mcp.notes.values() if w["widget_type"] == "Browser"] == [mcp.notes["setup-existing"]]
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    versions = astore.list_versions(doc.opaque_id, canvas_id="c")
    assert [v.payload["rationale"] for v in versions] == ["old rationale"]


async def test_legacy_tagged_setup_browser_is_recovered_without_duplicate(store):
    canvas_id, idea_id, round_index = "c", "idea1", 1
    old_discriminator = f"setup/predecessor:{idea_id}/round:{round_index}"
    old_browser_key = idempotency_key(canvas_id, "create_browser", f"browser/setup/{old_discriminator}")
    prior_title = tagged_title(f"{nodes.EXP_SETUP} v001] try X", old_browser_key)
    mcp = FakeMCP(workflow={"setups": [{"widget_id": "setup-prior", "widget_type": "Browser", "title": prior_title}]})
    mcp.seed_widget(idea_id, "Note", text="{idea: try X}")
    mcp.seed_widget("setup-prior", "Browser", title=prior_title, url=f"{BASE}/artifacts/stale?token=old")
    setup_model = ExperimentSetup.model_validate({**SETUP, "rationale": "old rationale"})
    _, payload = setup_payload(setup_model, idea_text="try X", idea_id=idea_id, round_index=round_index)
    astore = ArtifactStore(store.conn)
    doc = astore.create_artifact(
        canvas_id=canvas_id, idempotency_key=idempotency_key(canvas_id, "artifact_setup", old_discriminator),
        artifact_type=ArtifactType.SETUP, state=DecisionState.RUNNING, payload=payload,
        provenance=ArtifactProvenance(
            provider="openai", model_name=_settings().openai_model,
            source_widget_id=idea_id, trigger_id=f"setup/predecessor:{idea_id}/round:{round_index}",
        ),
        round=round_index,
    )

    setup_id, _ = await _make_setup(mcp, store)

    assert setup_id == "setup-prior"
    browsers = [w for w in mcp.notes.values() if w["widget_type"] == "Browser"]
    assert len(browsers) == 1
    assert "stale" not in mcp.notes[setup_id]["url"]
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    versions = astore.list_versions(doc.opaque_id, canvas_id=canvas_id)
    assert [v.payload["rationale"] for v in versions] == ["old rationale"]


async def test_same_round_setup_retry_keeps_original_version(store):
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="{idea: try X}", x=100.0, y=200.0, width=260.0, height=180.0)
    provenance = ArtifactProvenance(
        provider="openai", model_name=_settings().openai_model,
        source_widget_id="idea1", trigger_id="setup/predecessor:idea1/round:1",
    )
    first = ExperimentSetup.model_validate({**SETUP, "rationale": "first rationale"})
    second = ExperimentSetup.model_validate({**SETUP, "rationale": "second rationale"})

    mcp.fail_next_as_error_payload("create_browser")
    with pytest.raises(nodes.MCPToolError):
        await write_setup_node(
            mcp, store, _settings(), canvas_id="c", setup=first, idea_text="try X", idea_id="idea1",
            round_index=1, predecessor_id="idea1", edge_kind="idea_setup", provenance=provenance,
        )

    setup_id = await write_setup_node(
        mcp, store, _settings(), canvas_id="c", setup=second, idea_text="try X", idea_id="idea1",
        round_index=1, predecessor_id="idea1", edge_kind="idea_setup", provenance=provenance,
    )

    astore = ArtifactStore(store.conn)
    doc = astore.get_artifact_by_widget(canvas_id="c", widget_id=setup_id)
    assert doc is not None
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    versions = astore.list_versions(doc.opaque_id, canvas_id="c")
    assert [v.payload["rationale"] for v in versions] == ["first rationale"]


async def test_generated_result_appears_near_setup_not_far_robot(store):
    """Test geometry/connector assertions without authorization bypass.

    This test exercises the write path directly with write_result_node,
    which is the appropriate seam for testing layout. Authorization checking
    is tested separately in test_execution_modes.py.
    """
    mcp = FakeMCP()
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001]", x=500.0, y=100.0, width=480.0, height=360.0)
    mcp.seed_widget("robot1", "Note", title="Robot_arm", x=5000.0, y=5000.0, width=260.0, height=180.0)

    result_id = await write_result_node(
        mcp,
        store,
        _settings(),
        canvas_id="c",
        result=ExperimentResult.model_validate(RESULT),
        setup_id="setup1",
        robot_id="robot1",
        round_index=1,
    )

    assert result_id
    assert ("robot1", result_id) in mcp.connectors
    assert mcp.notes[result_id]["location"] == {"x": 1020.0, "y": 100.0}


async def test_generated_result_and_closed_are_browser_artifacts(store):
    """Phase 2: run_loop() processes one decision per call without generating
    successors. Terminal closure is only created if conditions allow, otherwise
    returns loop_continuation_deferred."""
    mcp = FakeMCP(note_text={"idea1": "{idea: A+B}", "setup1": "Round: 1\nmix", "result1": "marker reduced"})
    adapter = ScriptedAdapter({
        "LoopDecision": {"proceed": False, "reason": "done"},
    })
    loop = {
        "loop_connector_id": "c5", "setup_id": "setup1", "result_id": "result1",
        "robot_id": "robot1", "idea_id": "idea1", "ragcluster_id": "rag1", "round": 1,
    }
    settings = _settings()
    settings.loop_min_rounds = 1  # Allow closure on first decision
    summary = await run_loop(mcp, adapter, settings, store, canvas_id="c", loop=loop)

    astore = ArtifactStore(store.conn)
    # Only seed result, no successors generated.
    assert len(summary.result_ids) == 1
    result1_id = summary.result_ids[0]
    assert mcp.notes[result1_id]["widget_type"] == "Note"

    # Terminal Closed node created (because min_rounds met).
    closed = mcp.notes[summary.closed_id]
    assert closed["widget_type"] == "Browser"
    assert closed["title"].startswith("[EXP:Closed]")
    cdoc = astore.get_artifact_by_widget(canvas_id="c", widget_id=summary.closed_id)
    assert cdoc is not None and cdoc.artifact_type == ArtifactType.CLOSED
    assert cdoc.payload["proceed"] is False


async def test_missing_public_base_url_fails_closed_before_browser_call(store):
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="{idea: try X}")
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    with pytest.raises(ArtifactUrlError):
        await generate_setup(
            mcp, adapter, Settings(artifact_public_base_url=""), store, canvas_id="c", idea_text="try X",
            idea_id="idea1", ragcluster_id="rag1", round_index=1,
        )
    assert [w for w in mcp.notes.values() if w["widget_type"] == "Browser"] == []  # no Browser created


async def test_missing_public_base_url_leaves_attempt_retryable(store):
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    counts = await process_once(
        mcp, adapter, Settings(artifact_public_base_url=""), store, "rt1", "c"
    )  # empty base URL

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
    canvas_id, idea_id = "c", "idea1"
    # Setup artifacts converge on ONE widget per idea across rounds, so the
    # discriminator is idea-scoped (not round-scoped).
    discriminator = f"setup/idea:{idea_id}"
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


async def test_legacy_mapped_result_identity_is_reused_without_duplicate(store):
    mcp = FakeMCP()
    mcp.seed_widget("setup1", "Browser", title="[EXP:Setup v001]", x=500.0, y=100.0, width=480.0, height=360.0)
    mcp.seed_widget("robot1", "Note", title="Robot_arm")
    mcp.seed_widget("result-existing", "Browser", title="[EXP:Result v001] old", url=f"{BASE}/artifacts/stale?token=old")
    old_result = ExperimentResult.model_validate({"summary": "old summary", "metrics": ["old=1"]})
    _, payload = result_payload(old_result, setup_id="setup1", round_index=1)
    artifact_key = idempotency_key("c", "artifact_result", "result/setup:setup1/round:1")
    provenance = ArtifactProvenance(
        provider="openai", model_name=_settings().openai_model,
        source_widget_id="setup1", trigger_id="result/setup:setup1/round:1",
    )
    astore = ArtifactStore(store.conn)
    doc = astore.create_artifact(
        canvas_id="c", idempotency_key=artifact_key, artifact_type=ArtifactType.RESULT,
        state=DecisionState.ANALYSIS_COMPLETE, payload=payload, provenance=provenance, round=1,
    )
    astore.map_widget(doc.opaque_id, canvas_id="c", widget_id="result-existing")

    result_id = await write_result_node(
        mcp, store, _settings(), canvas_id="c", result=ExperimentResult.model_validate(RESULT),
        setup_id="setup1", robot_id="robot1", round_index=1,
    )

    assert result_id == "result-existing"
    result_widgets = [w for w in mcp.notes.values() if w["title"].startswith("[EXP:Result")]
    assert result_widgets == [mcp.notes["result-existing"]]
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    versions = astore.list_versions(doc.opaque_id, canvas_id="c")
    assert [v.payload["summary"] for v in versions] == ["old summary"]


async def test_legacy_tagged_result_browser_is_recovered_without_duplicate(store):
    canvas_id, setup_id, round_index = "c", "setup1", 1
    old_discriminator = f"result/setup:{setup_id}/round:{round_index}"
    old_browser_key = idempotency_key(canvas_id, "create_browser", f"browser/result/{old_discriminator}")
    prior_title = tagged_title(f"{nodes.EXP_RESULT} v001]", old_browser_key)
    mcp = FakeMCP(workflow={"results": [{"widget_id": "result-prior", "widget_type": "Browser", "title": prior_title}]})
    mcp.seed_widget(setup_id, "Browser", title="[EXP:Setup v001]", x=500.0, y=100.0, width=480.0, height=360.0)
    mcp.seed_widget("robot1", "Note", title="Robot_arm")
    mcp.seed_widget("result-prior", "Browser", title=prior_title, url=f"{BASE}/artifacts/stale?token=old")
    old_result = ExperimentResult.model_validate({"summary": "old summary", "metrics": ["old=1"]})
    _, payload = result_payload(old_result, setup_id=setup_id, round_index=round_index)
    astore = ArtifactStore(store.conn)
    doc = astore.create_artifact(
        canvas_id=canvas_id, idempotency_key=idempotency_key(canvas_id, "artifact_result", old_discriminator),
        artifact_type=ArtifactType.RESULT, state=DecisionState.ANALYSIS_COMPLETE, payload=payload,
        provenance=ArtifactProvenance(
            provider="openai", model_name=_settings().openai_model,
            source_widget_id=setup_id, trigger_id=f"result/setup:{setup_id}/round:{round_index}",
        ),
        round=round_index,
    )

    result_id = await write_result_node(
        mcp, store, _settings(), canvas_id=canvas_id, result=ExperimentResult.model_validate(RESULT),
        setup_id=setup_id, robot_id="robot1", round_index=round_index,
    )

    assert result_id == "result-prior"
    result_widgets = [w for w in mcp.notes.values() if w["title"].startswith("[EXP:Result")]
    assert len(result_widgets) == 1
    assert "stale" not in mcp.notes[result_id]["url"]
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    mapped = astore.get_artifact_by_widget(canvas_id=canvas_id, widget_id=result_id)
    assert mapped is not None and mapped.opaque_id == doc.opaque_id


async def test_browser_intent_accepts_legacy_title_position_hash(store):
    canvas_id, idea_id = "c", "idea1"
    mcp = FakeMCP()
    mcp.seed_widget(idea_id, "Note", text="{idea: try X}", x=100.0, y=200.0, width=260.0, height=180.0)
    setup_model = ExperimentSetup.model_validate(SETUP)
    _, payload = setup_payload(setup_model, idea_text="try X", idea_id=idea_id, round_index=1)
    provenance = ArtifactProvenance(
        provider="openai", model_name=_settings().openai_model,
        source_widget_id=idea_id, trigger_id=f"setup/predecessor:{idea_id}/round:1",
    )
    astore = ArtifactStore(store.conn)
    doc = astore.create_artifact(
        canvas_id=canvas_id, idempotency_key=idempotency_key(canvas_id, "artifact_setup", f"setup/idea:{idea_id}"),
        artifact_type=ArtifactType.SETUP, state=DecisionState.RUNNING, payload=payload,
        provenance=provenance, round=1,
    )
    browser_key = idempotency_key(canvas_id, "create_browser", f"browser/setup/setup/idea:{idea_id}")
    legacy_hash = artifact_compat.legacy_browser_intent_hash(
        opaque_id=doc.opaque_id,
        tagged_title=tagged_title(f"{nodes.EXP_SETUP} v001] try X", browser_key),
        x=0.0,
        y=420.0,
    )
    store.prepare_intent(
        idempotency_key=browser_key, canvas_id=canvas_id, kind="create_browser", input_hash=legacy_hash,
    )

    setup_id, _ = await _make_setup(mcp, store)

    assert setup_id
    intent = store.get_intent(browser_key)
    assert intent is not None
    assert intent.status == IntentStatus.RECONCILED
    assert intent.input_hash == legacy_hash


async def test_browser_intent_rejects_unrelated_existing_hash(store):
    browser_key = idempotency_key("c", "create_browser", "browser/setup/setup/idea:idea1")
    wrong_hash = input_hash({"opaque_id": "unrelated-artifact"})
    store.prepare_intent(
        idempotency_key=browser_key, canvas_id="c", kind="create_browser", input_hash=wrong_hash,
    )

    with pytest.raises(IntentHashMismatchError):
        artifact_compat.prepare_browser_intent(
            store, canvas_id="c", browser_key_value=browser_key, opaque_id="expected-artifact",
        )

    intent = store.get_intent(browser_key)
    assert intent is not None
    assert intent.input_hash == wrong_hash


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
