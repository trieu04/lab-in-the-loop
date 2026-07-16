"""Phase 7 watcher dispatch and approval-status reconciliation tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from lab_agent.approval_service import ApprovalSubmission, submit_approval
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.integrations.identity import (
    DevelopmentIdentity,
    StaticDevelopmentIdentityProvider,
    hash_development_credential,
)
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import ApprovalDecision, ApprovalRole, hash_validation_result
from lab_agent.state_store import StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter

CANVAS = "canvas"
SETTINGS = Settings(artifact_public_base_url="https://lab.test")
NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    state = StateStore(tmp_path / "state.db")
    yield state
    state.close()


def _setup(rationale: str = "Test current proposal.") -> ExperimentSetup:
    return ExperimentSetup(
        rationale=rationale,
        conditions=["temperature=25C"],
        steps=["measure baseline"],
        expected_readouts=["signal"],
        hypothesis="The signal remains measurable.",
        success_criteria=["record signal"],
    )


def _persist_setup(store: StateStore, setup: ExperimentSetup) -> None:
    artifacts = ArtifactStore(store.conn)
    doc = artifacts.create_artifact(
        canvas_id=CANVAS,
        idempotency_key="setup",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.APPROVED_FOR_IN_SILICO,
        payload=setup.model_dump(),
        provenance=ArtifactProvenance(provider="harness"),
        round=1,
    )
    artifacts.map_widget(doc.opaque_id, canvas_id=CANVAS, widget_id="setup")


def _workflow(*, validations: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {
        "setups": [{"widget_id": "setup", "title": "[EXP:Setup v001]"}],
        "setups_needing_run": [{"widget_id": "setup", "robot_id": "robot"}],
        "validations": validations or [],
        "scientist_review_markers": [{"widget_id": "review"}],
        "loops": [{"loop_connector_id": "loop", "setup_id": "setup"}],
    }


async def _approve(store: StateStore, role: ApprovalRole, secret: str) -> None:
    result = store.list_validation_results(CANVAS)[-1]
    provider = StaticDevelopmentIdentityProvider(
        identities=(
            DevelopmentIdentity("scientist", frozenset({ApprovalRole.SCIENTIST}), hash_development_credential("s")),
            DevelopmentIdentity("lead", frozenset({ApprovalRole.LAB_LEAD}), hash_development_credential("l")),
        ),
        clock=lambda: NOW,
    )
    await submit_approval(
        canvas_id=CANVAS,
        current_proposal_hash=result.proposal_hash,
        current_validation_result_hash=hash_validation_result(result),
        submission=ApprovalSubmission(
            approval_id=f"{role.value}-approval",
            required_role=role,
            decision=ApprovalDecision.APPROVE,
            rationale="Reviewed durable validation evidence.",
            credential=SecretStr(secret),
        ),
        state_store=store,
        identity_provider=provider,
    )


async def test_watcher_validates_typed_setup_and_never_runs_robot_or_loop(store: StateStore) -> None:
    assert SETTINGS.wet_lab_execution_enabled is False
    _persist_setup(store, _setup())
    mcp = FakeMCP(workflow=_workflow())

    counts = await process_once(mcp, ScriptedAdapter({}), SETTINGS, store, "runtime", CANVAS)

    assert counts == {"setups": 0, "runs": 0, "loops": 0, "validations": 1}
    assert len(store.list_validation_results(CANVAS)) == 1
    assert not any(src == "robot" for src, _ in mcp.connectors)
    assert not any(widget["title"].startswith("[EXP:Result") for widget in mcp.notes.values())


async def test_approved_gate_and_robot_topology_never_dispatch_by_default(
    store: StateStore,
) -> None:
    _persist_setup(store, _setup())
    mcp = FakeMCP(workflow=_workflow())
    model_adapter = ScriptedAdapter({})

    await process_once(mcp, model_adapter, SETTINGS, store, "runtime", CANVAS)
    await _approve(store, ApprovalRole.SCIENTIST, "s")
    await _approve(store, ApprovalRole.LAB_LEAD, "l")
    validation_ids = [
        widget_id
        for widget_id, widget in mcp.notes.items()
        if widget["title"].startswith("[EXP:Validation]")
    ]
    mcp.workflow = _workflow(
        validations=[{"widget_id": widget_id} for widget_id in validation_ids]
    )

    counts = await process_once(mcp, model_adapter, SETTINGS, store, "runtime", CANVAS)

    assert counts["runs"] == 0
    assert counts["loops"] == 0
    assert model_adapter.schema_calls == []
    assert not any(src == "robot" for src, _ in mcp.connectors)
    assert not any(widget["title"].startswith("[EXP:Result") for widget in mcp.notes.values())
    status_id = next(
        widget_id
        for widget_id, widget in mcp.notes.items()
        if widget["title"].startswith("[EXP:Validation] Approval Status")
    )
    status = ArtifactStore(store.conn).get_artifact_by_widget(
        canvas_id=CANVAS,
        widget_id=status_id,
    )
    assert status is not None
    assert status.payload["approval_status"]["state"] == "APPROVED_FOR_WET_LAB"
    assert status.payload["execution_enabled"] is False


async def test_proposal_edits_revalidate_and_status_versions_reconcile_after_restart(tmp_path) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path)
    _persist_setup(first, _setup())
    mcp = FakeMCP(workflow=_workflow())
    mcp.seed_widget("review", "Note", title="[EXP:Scientist Review]", text="approve this")
    await process_once(mcp, ScriptedAdapter({}), SETTINGS, first, "runtime", CANVAS)
    validation_id = next(widget_id for widget_id, widget in mcp.notes.items() if widget["title"].startswith("[EXP:Validation]"))
    mcp.workflow = _workflow(validations=[{"widget_id": validation_id}])

    await process_once(mcp, ScriptedAdapter({}), SETTINGS, first, "runtime", CANVAS)
    status_id = next(widget_id for widget_id, widget in mcp.notes.items() if widget["title"].startswith("[EXP:Validation] Approval Status"))
    artifacts = ArtifactStore(first.conn)
    status = artifacts.get_artifact_by_widget(canvas_id=CANVAS, widget_id=status_id)
    assert status is not None and status.payload["approval_status"]["state"] == "NEEDS_SCIENTIST_REVIEW"
    assert status.payload["execution_enabled"] is False and status.current_version == 1
    assert first.list_gate_approvals(CANVAS) == []

    await _approve(first, ApprovalRole.SCIENTIST, "s")
    await process_once(mcp, ScriptedAdapter({}), SETTINGS, first, "runtime", CANVAS)
    status = artifacts.get_artifact_by_widget(canvas_id=CANVAS, widget_id=status_id)
    assert status is not None and status.payload["approval_status"]["state"] == "NEEDS_LAB_LEAD_APPROVAL"
    assert status.current_version == 2
    first.close()

    restarted = StateStore(path)
    try:
        await process_once(mcp, ScriptedAdapter({}), SETTINGS, restarted, "runtime", CANVAS)
        await _approve(restarted, ApprovalRole.LAB_LEAD, "l")
        await process_once(mcp, ScriptedAdapter({}), SETTINGS, restarted, "runtime", CANVAS)
        status = ArtifactStore(restarted.conn).get_artifact_by_widget(canvas_id=CANVAS, widget_id=status_id)
        assert status is not None and status.current_version == 3
        assert status.payload["approval_status"]["state"] == "APPROVED_FOR_WET_LAB"
        assert status.payload["approval_status"]["production_eligible"] is False
        assert status.payload["execution_enabled"] is False
        assert len([w for w in mcp.notes.values() if w["title"].startswith("[EXP:Validation] Approval Status")]) == 1

        setup = ArtifactStore(restarted.conn).get_artifact_by_widget(canvas_id=CANVAS, widget_id="setup")
        assert setup is not None
        ArtifactStore(restarted.conn).append_version(
            setup.opaque_id, canvas_id=CANVAS, state=setup.state, payload=_setup("Edited proposal.").model_dump(),
            provenance=setup.provenance, round=setup.round,
        )
        counts = await process_once(mcp, ScriptedAdapter({}), SETTINGS, restarted, "runtime", CANVAS)
        assert counts["validations"] == 1
        validation_ids = [wid for wid, widget in mcp.notes.items() if widget["title"].startswith("[EXP:Validation]")]
        mcp.workflow = _workflow(validations=[{"widget_id": wid} for wid in validation_ids])
        await process_once(mcp, ScriptedAdapter({}), SETTINGS, restarted, "runtime", CANVAS)
        statuses = [
            ArtifactStore(restarted.conn).get_artifact_by_widget(canvas_id=CANVAS, widget_id=wid)
            for wid, widget in mcp.notes.items() if widget["title"].startswith("[EXP:Validation] Approval Status")
        ]
        current = next(item for item in statuses if item and item.payload["proposal_hash"] != status.payload["proposal_hash"])
        assert current.payload["approval_status"]["state"] == "NEEDS_SCIENTIST_REVIEW"
    finally:
        restarted.close()
