"""Integration tests for the grounding gate wired into the dispatch/write path
(plan items 7 and 9): insufficient evidence is a *handled* needs-input outcome
(never a schema failure), a sufficiency claim backed by a fabricated citation
fails closed and stays retryable exactly like a schema failure, and repeated
attempts with the same unresolved reason converge on one artifact instead of
accumulating duplicates. Companion to ``test_orchestrator_fail_closed.py``,
which covers pure schema-validation failures.
"""

from __future__ import annotations

import pytest

from lab_agent.config import Settings
from lab_agent.models.evidence import GroundingDecision
from lab_agent.orchestrator import generate_setup
from lab_agent.state_store import AttemptStatus, StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup

RUNTIME_ID = "test-runtime"

# Sufficient rationale/steps but no evidence asserted -> NEEDS_INPUT, not a
# schema failure (the schema itself is well-formed; the *gate* rejects it).
INSUFFICIENT_SETUP = {"rationale": "because", "steps": ["mix A and B"], "inputs": ["A", "B"]}
# Claims sufficiency but cites a source_id no read ever produced.
FABRICATED_SETUP = {
    "rationale": "because", "steps": ["mix A and B"], "inputs": ["A", "B"],
    "evidence_status": "sufficient", "citations": [{"source_id": "not-a-real-ledger-id"}],
}
IDEA_WORKFLOW = {
    "ideas_needing_setup": [{"widget_id": "idea1", "ragcluster_id": "rag1"}],
    "setups_needing_run": [], "loops": [],
}


def _settings(**kw):
    return Settings(artifact_public_base_url="https://lab.test", **kw)  # type: ignore[call-arg]


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


async def test_process_once_setup_needs_input_is_handled_not_a_schema_failure(store):
    """Insufficient evidence must write only a needs-input prompt and count as
    a *handled* trigger (ok=True) -- never a schema failure, never a retry."""
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    adapter = ScriptedAdapter({"ExperimentSetup": INSUFFICIENT_SETUP})
    counts = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")  # type: ignore[arg-type]

    assert counts["setups"] == 1  # handled, not a failure
    setups = [w for w in mcp.notes.values() if "Setup" in w["title"] and "Needs" not in w["title"]]
    assert setups == []  # no executable Setup written
    needs_input = [w for w in mcp.notes.values() if "Needs Input" in w["title"]]
    assert len(needs_input) == 1
    attempt = store.get_attempt("c", "idea_setup:idea1")
    assert attempt is not None and attempt.status == AttemptStatus.COMPLETED


async def test_process_once_setup_invalid_citation_fails_closed_and_is_retryable(tmp_path):
    """A sufficiency claim backed by a fabricated citation must write nothing
    and stay retryable, exactly like a schema-validation failure."""
    store = StateStore(tmp_path / "state.db", rng=lambda: 0.0)  # zero jitter: due immediately
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    adapter = ScriptedAdapter({"ExperimentSetup": FABRICATED_SETUP})

    first = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")  # type: ignore[arg-type]
    assert first["setups"] == 0
    assert mcp.notes == {"idea1": mcp.notes["idea1"]}  # nothing written beyond the seed
    assert mcp.connectors == []
    attempt = store.get_attempt("c", "idea_setup:idea1")
    assert attempt is not None and attempt.status == AttemptStatus.FAILED

    second = await process_once(mcp, adapter, _settings(), store, RUNTIME_ID, "c")
    assert second["setups"] == 0  # still retried, still not counted a success
    assert mcp.connectors == []
    store.close()


async def test_needs_input_setup_converges_on_restart_same_reason(store):
    """Two independent grounding attempts for the same idea with the same
    unresolved reason must converge on one needs-input widget/connector, not
    accumulate a fresh one each restart (dedup identity: canvas + predecessor
    + reason hash)."""
    mcp = FakeMCP()
    outcome1 = await generate_setup(
        mcp, ScriptedAdapter({"ExperimentSetup": INSUFFICIENT_SETUP}), _settings(), store,
        canvas_id="c", idea_text="try X", idea_id="idea1", ragcluster_id="rag1", round_index=1,
    )
    outcome2 = await generate_setup(
        mcp, ScriptedAdapter({"ExperimentSetup": INSUFFICIENT_SETUP}), _settings(), store,
        canvas_id="c", idea_text="try X", idea_id="idea1", ragcluster_id="rag1", round_index=1,
    )

    assert outcome1.decision is GroundingDecision.NEEDS_INPUT
    assert outcome2.decision is GroundingDecision.NEEDS_INPUT
    needs_input = [w for w in mcp.notes.values() if "Needs Input" in w["title"]]
    assert len(needs_input) == 1
    assert mcp.connectors.count(("idea1", needs_input[0]["id"])) == 1


async def test_self_contained_sd_and_signal_setup_writes_without_needs_input(store):
    mcp = FakeMCP()
    payload = grounded_setup({
        "rationale": "Compare groups using standard deviation (SD).",
        "expected_readouts": [
            "Signal is normalized fluorescence intensity in arbitrary units, averaged "
            "across three wells at 30 minutes."
        ],
        "parameters": ["Assume three replicate wells; this reversible default may be adjusted."],
        "ambiguity_flags": [
            {"term": "candidate alpha"},
            {"term": "lung stiffness score"},
            {"term": "synthetic"},
            {"term": "signal"},
        ],
    })

    outcome = await generate_setup(
        mcp, ScriptedAdapter({"ExperimentSetup": payload}), _settings(), store,
        canvas_id="c", idea_text="compare SD of the signal", idea_id="idea1",
        ragcluster_id="rag1", round_index=1,
    )

    assert outcome.decision is GroundingDecision.EXECUTABLE
    assert any("Setup" in widget["title"] for widget in mcp.notes.values())
    assert not any("Needs Input" in widget["title"] for widget in mcp.notes.values())


async def test_material_ambiguity_still_writes_needs_input(store):
    mcp = FakeMCP()
    payload = grounded_setup({
        "rationale": "Compare groups using standard deviation (SD).",
        "ambiguity_flags": [
            {
                "term": "signal",
                "resolved": False,
                "material_impact": "Changes the assay interpretation.",
                "alternatives": ["fluorescence", "luminescence"],
            }
        ],
    })

    outcome = await generate_setup(
        mcp, ScriptedAdapter({"ExperimentSetup": payload}), _settings(), store,
        canvas_id="c", idea_text="compare SD of the signal", idea_id="idea1",
        ragcluster_id="rag1", round_index=1,
    )

    assert outcome.decision is GroundingDecision.NEEDS_INPUT
    assert any("Needs Input" in widget["title"] for widget in mcp.notes.values())
    assert not any(
        "Setup" in widget["title"] and "Needs Input" not in widget["title"]
        for widget in mcp.notes.values()
    )
