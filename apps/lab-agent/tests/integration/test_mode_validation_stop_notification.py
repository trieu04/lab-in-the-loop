"""Cross-boundary mode, validation, approval, and terminal-stop tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from lab_agent.approval_service import (
    ApprovalSubmission,
    submit_approval,
    submit_manual_execution_activation,
)
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.integrations.identity import (
    DevelopmentIdentity,
    StaticDevelopmentIdentityProvider,
    hash_development_credential,
)
from lab_agent.integrations.in_silico import DeterministicInSilicoAdapter
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import ApprovalDecision, ApprovalRole, hash_validation_result
from lab_agent.state_store import StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter

CANVAS = "canvas-mode-e2e"
NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    state = StateStore(tmp_path / "state.db")
    yield state
    state.close()


def _setup() -> ExperimentSetup:
    return ExperimentSetup(
        rationale="E2E grounded proposal.", conditions=["temperature=25C"],
        steps=["measure baseline"], expected_readouts=["signal"],
        hypothesis="Signal remains measurable.", success_criteria=["record signal"],
    )


def _persist(store: StateStore) -> None:
    artifact = ArtifactStore(store.conn).create_artifact(
        canvas_id=CANVAS, idempotency_key="setup-e2e", artifact_type=ArtifactType.SETUP,
        state=DecisionState.APPROVED_FOR_IN_SILICO, payload=_setup().model_dump(),
        provenance=ArtifactProvenance(provider="harness", source_widget_id="idea"), round=1,
    )
    ArtifactStore(store.conn).map_widget(artifact.opaque_id, canvas_id=CANVAS, widget_id="setup")


def _identity() -> StaticDevelopmentIdentityProvider:
    return StaticDevelopmentIdentityProvider(
        identities=(
            DevelopmentIdentity("scientist", frozenset({ApprovalRole.SCIENTIST}), hash_development_credential("scientist")),
            DevelopmentIdentity("lead", frozenset({ApprovalRole.LAB_LEAD}), hash_development_credential("lead")),
        ), clock=lambda: NOW,
    )


def _workflow(mode: str, *, run: bool = True) -> dict[str, object]:
    return {
        "ideas": [{"widget_id": "idea", "execution_mode": mode}],
        "setups_needing_validation": [{"widget_id": "setup", "title": "[EXP:Setup v001]"}],
        "setups_needing_run": ([{"widget_id": "setup", "robot_id": "robot", "title": "[EXP:Setup v001]"}] if run else []),
        "validations": [],
    }


async def _approve(store: StateStore) -> None:
    result = store.list_validation_results(CANVAS)[0]
    provider = _identity()
    for role, secret in ((ApprovalRole.SCIENTIST, "scientist"), (ApprovalRole.LAB_LEAD, "lead")):
        await submit_approval(
            canvas_id=CANVAS, current_proposal_hash=result.proposal_hash,
            current_validation_result_hash=hash_validation_result(result),
            submission=ApprovalSubmission(
                approval_id=f"{role.value}-e2e", required_role=role,
                decision=ApprovalDecision.APPROVE, rationale="Reviewed evidence.",
                credential=SecretStr(secret),
            ), state_store=store, identity_provider=provider,
        )


async def _activate(store: StateStore) -> None:
    result = store.list_validation_results(CANVAS)[0]
    await submit_manual_execution_activation(
        canvas_id=CANVAS, setup_id="setup", current_proposal_hash=result.proposal_hash,
        current_validation_result_hash=hash_validation_result(result), credential=SecretStr("lead"),
        required_role=ApprovalRole.LAB_LEAD, state_store=store, identity_provider=_identity(),
    )


async def test_manual_mode_validation_approval_activation_and_restart(store, tmp_path) -> None:
    settings = Settings(artifact_public_base_url="https://lab.test", wet_lab_execution_enabled=True)
    _persist(store)
    mcp = FakeMCP(workflow=_workflow("manual"))
    await process_once(mcp, ScriptedAdapter({}), settings, store, "worker", CANVAS)
    assert len(store.list_validation_results(CANVAS)) == 1
    await _approve(store)
    assert (await process_once(mcp, ScriptedAdapter({}), settings, store, "worker", CANVAS))["runs"] == 0
    await _activate(store)
    adapter = ScriptedAdapter({"ExperimentResult": {"summary": "signal", "metrics": ["ok"]}})
    assert (await process_once(mcp, adapter, settings, store, "worker", CANVAS))["runs"] == 1
    assert len([n for n in mcp.notes.values() if n["title"].startswith("[EXP:Result")]) == 1

    store.close()
    restarted = StateStore(tmp_path / "state.db")
    try:
        assert (await process_once(mcp, ScriptedAdapter({}), settings, restarted, "worker", CANVAS))["runs"] == 0
        assert len([n for n in mcp.notes.values() if n["title"].startswith("[EXP:Result")]) == 1
    finally:
        restarted.close()


async def test_auto_mode_keeps_validation_and_approval_gates(store) -> None:
    settings = Settings(artifact_public_base_url="https://lab.test", wet_lab_execution_enabled=True)
    _persist(store)
    mcp = FakeMCP(workflow=_workflow("auto"))
    await process_once(mcp, ScriptedAdapter({}), settings, store, "worker", CANVAS)
    await _approve(store)
    adapter = ScriptedAdapter({"ExperimentResult": {"summary": "signal", "metrics": ["ok"]}})
    counts = await process_once(mcp, adapter, settings, store, "worker", CANVAS)
    assert counts["runs"] == 1
    assert adapter.schema_calls == ["ExperimentResult"]


async def test_unknown_mode_is_visible_but_non_actionable(store) -> None:
    mcp = FakeMCP(workflow={"mode_errors": [{"widget_id": "idea", "parse_error": "unsupported_mode"}]})
    adapter = ScriptedAdapter({"ExperimentSetup": _setup().model_dump()})
    settings = Settings(artifact_public_base_url="https://lab.test")
    first = await process_once(mcp, adapter, settings, store, "worker", CANVAS, in_silico_adapter=DeterministicInSilicoAdapter())
    second = await process_once(mcp, adapter, settings, store, "worker-2", CANVAS, in_silico_adapter=DeterministicInSilicoAdapter())
    assert first["setups"] == second["setups"] == 0
    assert adapter.schema_calls == []
    assert len([n for n in mcp.notes.values() if n["title"].startswith("[EXP:Needs Input")]) == 1


__all__ = [
    "test_manual_mode_validation_approval_activation_and_restart",
    "test_auto_mode_keeps_validation_and_approval_gates",
    "test_unknown_mode_is_visible_but_non_actionable",
]
