"""Execution-mode dispatch tests using the durable local workflow ledger."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from lab_agent.approval_service import (
    ApprovalServiceError,
    ApprovalSubmission,
    submit_approval,
    submit_manual_execution_activation,
)
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.integrations.identity import (
    DevelopmentIdentity,
    IdentityAuthenticationError,
    IdentityAuthorizationError,
    StaticDevelopmentIdentityProvider,
    hash_development_credential,
)
from lab_agent.integrations.in_silico import DeterministicInSilicoAdapter
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import (
    ApprovalDecision,
    ApprovalRole,
    hash_proposal,
    hash_validation_result,
)
from lab_agent.orchestrator_robot import RobotExecutionAuthorizationError, run_on_robot
from lab_agent.orchestrator_validation import run_in_silico_validation
from lab_agent.state_store import StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter

CANVAS = "canvas"
SETTINGS = Settings(artifact_public_base_url="https://lab.test", wet_lab_execution_enabled=True)
NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _setup() -> ExperimentSetup:
    return ExperimentSetup(
        rationale="A grounded, executable setup.", conditions=["temperature=25C"],
        steps=["measure baseline"], expected_readouts=["signal"],
        hypothesis="Signal remains measurable.", success_criteria=["record signal"],
    )


def _persist_setup(store: StateStore, source_id: str = "idea") -> None:
    document = ArtifactStore(store.conn).create_artifact(
        canvas_id=CANVAS, idempotency_key="setup", artifact_type=ArtifactType.SETUP,
        state=DecisionState.APPROVED_FOR_IN_SILICO, payload=_setup().model_dump(),
        provenance=ArtifactProvenance(provider="harness", source_widget_id=source_id), round=1,
    )
    ArtifactStore(store.conn).map_widget(document.opaque_id, canvas_id=CANVAS, widget_id="setup")


def _provider() -> StaticDevelopmentIdentityProvider:
    return StaticDevelopmentIdentityProvider(
        identities=(
            DevelopmentIdentity("scientist", frozenset({ApprovalRole.SCIENTIST}), hash_development_credential("scientist-secret")),
            DevelopmentIdentity("lead", frozenset({ApprovalRole.LAB_LEAD}), hash_development_credential("lead-secret")),
        ),
        clock=lambda: NOW,
    )


async def _approve(store: StateStore) -> None:
    result = store.list_validation_results(CANVAS)[0]
    for approval_id, role, secret in (("scientist", ApprovalRole.SCIENTIST, "scientist-secret"), ("lead", ApprovalRole.LAB_LEAD, "lead-secret")):
        await submit_approval(
            canvas_id=CANVAS, current_proposal_hash=result.proposal_hash,
            current_validation_result_hash=hash_validation_result(result),
            submission=ApprovalSubmission(
                approval_id=approval_id, required_role=role, decision=ApprovalDecision.APPROVE,
                rationale="Current validation evidence was reviewed.", credential=SecretStr(secret),
            ), state_store=store, identity_provider=_provider(),
        )


async def _activate(store: StateStore, *, secret: str = "lead-secret", role: ApprovalRole = ApprovalRole.LAB_LEAD, proposal_hash: str | None = None) -> None:
    result = store.list_validation_results(CANVAS)[0]
    await submit_manual_execution_activation(
        canvas_id=CANVAS, setup_id="setup", current_proposal_hash=proposal_hash or hash_proposal(_setup()),
        current_validation_result_hash=hash_validation_result(result), credential=SecretStr(secret),
        required_role=role, state_store=store, identity_provider=_provider(),
    )


def _workflow(mode: str) -> dict[str, object]:
    return {
        "ideas": [{"widget_id": "idea", "execution_mode": mode}],
        "setups_needing_validation": [],
        "setups_needing_run": [{"widget_id": "setup", "robot_id": "robot", "title": "[EXP:Setup v001]"}],
        "validations": [],
    }


@pytest.fixture
def store(tmp_path):
    state = StateStore(tmp_path / "state.db")
    yield state
    state.close()


async def test_manual_activation_is_authenticated_durable_hash_bound_and_restart_safe(store: StateStore, tmp_path) -> None:
    _persist_setup(store)
    mcp = FakeMCP(workflow=_workflow("manual"))
    await run_in_silico_validation(mcp, SETTINGS, store, DeterministicInSilicoAdapter(), CANVAS, "setup", 1)
    await _approve(store)
    waiting = await process_once(mcp, ScriptedAdapter({}), SETTINGS, store, "one", CANVAS)
    assert waiting["runs"] == 0

    with pytest.raises(IdentityAuthenticationError):
        await _activate(store, secret="invalid")
    with pytest.raises(IdentityAuthorizationError):
        await _activate(store, secret="scientist-secret")
    with pytest.raises(ApprovalServiceError, match="current validation evidence"):
        await _activate(store, proposal_hash="f" * 64)
    await _activate(store)
    await _activate(store)
    events = [event for event in store.list_audit_events(CANVAS) if event.event == "manual_execution_activated"]
    assert len(events) == 1
    assert events[0].payload["identity"]["actor_id"] == "lead"
    assert "lead-secret" not in str(events[0].payload)

    store.close()
    restarted = StateStore(tmp_path / "state.db")
    try:
        ran = await process_once(
            mcp, ScriptedAdapter({"ExperimentResult": {"summary": "ok", "metrics": ["signal=1"]}}),
            SETTINGS, restarted, "one", CANVAS,
        )
        assert ran["runs"] == 1
        assert len([n for n in mcp.notes.values() if n["title"].startswith("[EXP:Result")]) == 1
        assert (await process_once(mcp, ScriptedAdapter({}), SETTINGS, restarted, "one", CANVAS))["runs"] == 0
    finally:
        restarted.close()


async def test_auto_runs_only_after_current_ordered_approval(store: StateStore) -> None:
    _persist_setup(store)
    mcp = FakeMCP(workflow=_workflow("auto"))
    await run_in_silico_validation(mcp, SETTINGS, store, DeterministicInSilicoAdapter(), CANVAS, "setup", 1)
    await _approve(store)
    disabled = await process_once(mcp, ScriptedAdapter({}), Settings(artifact_public_base_url="https://lab.test"), store, "one", CANVAS)
    assert disabled["runs"] == 0
    counts = await process_once(mcp, ScriptedAdapter({"ExperimentResult": {"summary": "ok", "metrics": ["signal=1"]}}), SETTINGS, store, "one", CANVAS)
    assert counts["runs"] == 1
    assert len([n for n in mcp.notes.values() if n["title"].startswith("[EXP:Result")]) == 1


async def test_direct_robot_dispatch_respects_disabled_execution_gate(store: StateStore) -> None:
    _persist_setup(store)
    mcp = FakeMCP(workflow=_workflow("auto"))
    await run_in_silico_validation(mcp, SETTINGS, store, DeterministicInSilicoAdapter(), CANVAS, "setup", 1)
    await _approve(store)
    with pytest.raises(RobotExecutionAuthorizationError, match="disabled"):
        await run_on_robot(mcp, ScriptedAdapter({}), Settings(artifact_public_base_url="https://lab.test"), store, canvas_id=CANVAS, setup_id="setup", setup_text="ignored", robot_id="robot", round_index=1, execution_mode="auto")
    assert not any(n["title"].startswith("[EXP:Result") for n in mcp.notes.values())


async def test_invalid_source_mode_blocks_an_activated_setup_once(store: StateStore) -> None:
    _persist_setup(store)
    mcp = FakeMCP(workflow=_workflow("manual"))
    await run_in_silico_validation(mcp, SETTINGS, store, DeterministicInSilicoAdapter(), CANVAS, "setup", 1)
    await _approve(store)
    await _activate(store)
    mcp.workflow = {**_workflow("unsupported"), "mode_errors": [{"widget_id": "idea", "parse_error": "unsupported_mode"}]}
    adapter = ScriptedAdapter({"ExperimentResult": {"summary": "bad", "metrics": []}})

    first = await process_once(mcp, adapter, SETTINGS, store, "one", CANVAS)
    second = await process_once(mcp, adapter, SETTINGS, store, "two", CANVAS)

    assert first["runs"] == second["runs"] == 0
    assert adapter.schema_calls == []
    assert len([n for n in mcp.notes.values() if n["title"].startswith("[EXP:Needs Input")]) == 1
    assert not any(n["title"].startswith("[EXP:Result") for n in mcp.notes.values())


async def test_unknown_mode_writes_one_needs_input_and_no_setup_or_robot(store: StateStore) -> None:
    mcp = FakeMCP(workflow={"mode_errors": [{"widget_id": "idea", "parse_error": "unsupported_mode"}]})
    adapter = ScriptedAdapter({"ExperimentSetup": _setup().model_dump(), "ExperimentResult": {"summary": "bad", "metrics": []}})
    first = await process_once(mcp, adapter, SETTINGS, store, "one", CANVAS)
    second = await process_once(mcp, adapter, SETTINGS, store, "two", CANVAS)
    assert first["setups"] == second["setups"] == 0
    assert adapter.schema_calls == []
    assert len([n for n in mcp.notes.values() if n["title"].startswith("[EXP:Needs Input")]) == 1
    assert not any(n["title"].startswith("[EXP:Result") for n in mcp.notes.values())
