"""Focused runtime tests for canonical in-silico validation artifacts."""

from __future__ import annotations

import pytest

from lab_agent import nodes
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.integrations.in_silico import (
    DeterministicInSilicoAdapter,
    DisabledRealInSilicoAdapter,
    InSilicoNotReadyError,
    MockFailureMode,
    RealAdapterReadiness,
)
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import ValidationDecision, hash_proposal
from lab_agent.orchestrator_support import write_setup_node
from lab_agent.orchestrator_validation import TypedSetupArtifactError, run_in_silico_validation
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP


@pytest.fixture
def store(tmp_path):
    state = StateStore(tmp_path / "state.db")
    yield state
    state.close()


def _settings() -> Settings:
    return Settings(artifact_public_base_url="https://lab.test")


def _setup(**changes: object) -> ExperimentSetup:
    return ExperimentSetup(
        rationale="Test the proposal.",
        conditions=["temperature=25C"],
        steps=["measure baseline"],
        expected_readouts=["signal"],
        hypothesis="Signal remains measurable.",
        success_criteria=["record signal"],
    ).model_copy(update=changes)


def _persist_setup(store: StateStore, setup: ExperimentSetup, setup_id: str = "setup-1") -> None:
    doc = ArtifactStore(store.conn).create_artifact(
        canvas_id="canvas-1",
        idempotency_key=f"setup:{setup_id}",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.APPROVED_FOR_IN_SILICO,
        payload=setup.model_dump(),
        provenance=ArtifactProvenance(provider="harness", source_widget_id="idea-1"),
        round=2,
    )
    ArtifactStore(store.conn).map_widget(doc.opaque_id, canvas_id="canvas-1", widget_id=setup_id)


@pytest.mark.parametrize(
    ("changes", "decision", "target"),
    [
        ({}, ValidationDecision.PROCEED, DecisionState.NEEDS_SCIENTIST_REVIEW),
        ({"success_criteria": []}, ValidationDecision.REVISE, DecisionState.NEEDS_REVIEW),
        ({"steps": []}, ValidationDecision.REJECT, DecisionState.REJECTED),
    ],
)
async def test_validation_writes_decision_neutral_browser_artifact(
    store: StateStore,
    changes: dict[str, object],
    decision: ValidationDecision,
    target: DecisionState,
) -> None:
    setup = _setup(**changes)
    _persist_setup(store, setup)
    mcp = FakeMCP(workflow={"validations": []})

    outcome = await run_in_silico_validation(
        mcp, _settings(), store, DeterministicInSilicoAdapter(), "canvas-1", "setup-1", 2
    )

    assert outcome.result.decision is decision
    assert outcome.projected_state is target
    widget = mcp.notes[outcome.validation_artifact_widget_id]
    assert widget["title"].startswith(f"[EXP:Validation] {decision.value}")
    assert ("setup-1", outcome.validation_artifact_widget_id) in mcp.connectors
    doc = ArtifactStore(store.conn).get_artifact_by_widget(
        canvas_id="canvas-1", widget_id=outcome.validation_artifact_widget_id
    )
    assert doc is not None and doc.artifact_type is ArtifactType.IN_SILICO
    assert doc.payload["proposal_hash"] == hash_proposal(setup) == outcome.proposal_hash
    assert doc.payload["result_hash"] == outcome.result_hash
    assert doc.payload["validation"]["decision"] == decision.value
    assert doc.payload["approval_status"]["state"] == target.value
    assert doc.payload["validation_label"] == "DETERMINISTIC DRY RUN — NOT SCIENTIFIC VALIDATION"
    assert store.list_validation_results("canvas-1", proposal_hash=outcome.proposal_hash) == [
        outcome.result
    ]
    assert "credential" not in str(doc.payload).lower()
    assert "token=" not in str(doc.payload)


async def test_generated_setup_starts_approved_for_in_silico(store: StateStore) -> None:
    mcp = FakeMCP()
    setup_id = await write_setup_node(
        mcp,
        store,
        _settings(),
        canvas_id="canvas-1",
        setup=_setup(),
        idea_text="idea",
        idea_id="idea-1",
        round_index=1,
        predecessor_id="idea-1",
        edge_kind="idea_setup",
    )

    doc = ArtifactStore(store.conn).get_artifact_by_widget(canvas_id="canvas-1", widget_id=setup_id)
    assert doc is not None and doc.state is DecisionState.APPROVED_FOR_IN_SILICO


async def test_validation_rejects_legacy_or_untyped_setup_widget(store: StateStore) -> None:
    with pytest.raises(TypedSetupArtifactError, match="canonical setup artifact"):
        await run_in_silico_validation(
            FakeMCP(),
            _settings(),
            store,
            DeterministicInSilicoAdapter(),
            "canvas-1",
            "legacy-note",
            1,
        )


@pytest.mark.parametrize(
    "failure",
    [MockFailureMode.TIMEOUT, MockFailureMode.PROVIDER_FAILURE, MockFailureMode.INVALID_SCHEMA],
)
async def test_adapter_failures_audit_without_writing_validation_artifact(
    store: StateStore, failure: MockFailureMode
) -> None:
    _persist_setup(store, _setup())
    mcp = FakeMCP()
    with pytest.raises(RuntimeError):
        await run_in_silico_validation(
            mcp, _settings(), store, DeterministicInSilicoAdapter(failure), "canvas-1", "setup-1", 2
        )

    assert not [w for w in mcp.notes.values() if w["title"].startswith(nodes.EXP_VALIDATION)]
    audit = store.list_audit_events("canvas-1")
    assert audit[-1].event == "in_silico_validation_failed"
    assert audit[-1].payload["error_type"]


async def test_not_ready_adapter_audits_without_canvas_write(store: StateStore) -> None:
    _persist_setup(store, _setup())
    with pytest.raises(InSilicoNotReadyError):
        await run_in_silico_validation(
            FakeMCP(),
            _settings(),
            store,
            DisabledRealInSilicoAdapter(RealAdapterReadiness()),
            "canvas-1",
            "setup-1",
            2,
        )

    event = store.list_audit_events("canvas-1")[-1]
    assert event.event == "in_silico_validation_failed"
    assert event.payload["error_type"] == "InSilicoNotReadyError"
