"""Canonical ExperimentResult generation survives projection crashes and replay."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

import lab_agent.orchestrator_robot as robot_module
from lab_agent.approval_service import ApprovalSubmission, submit_approval
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.integrations.identity import (
    DevelopmentIdentity,
    StaticDevelopmentIdentityProvider,
    hash_development_credential,
)
from lab_agent.integrations.in_silico import DeterministicInSilicoAdapter
from lab_agent.model_gateway import GovernedAdapter, build_context
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import ApprovalDecision, ApprovalRole, hash_validation_result
from lab_agent.orchestrator_robot import run_on_robot
from lab_agent.orchestrator_validation import run_in_silico_validation
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP, ScriptedAdapter

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
CANVAS = "canvas"


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        _env_prefix="__TEST_NO_ENV__",
        artifact_public_base_url="https://lab.test",
        wet_lab_execution_enabled=True,
    )


def _setup() -> ExperimentSetup:
    return ExperimentSetup(
        rationale="current proposal",
        conditions=["temperature=25C"],
        steps=["measure baseline"],
        expected_readouts=["signal"],
        hypothesis="signal remains measurable",
        success_criteria=["record signal"],
    )


def _provider() -> StaticDevelopmentIdentityProvider:
    return StaticDevelopmentIdentityProvider(
        identities=(
            DevelopmentIdentity(
                "scientist", frozenset({ApprovalRole.SCIENTIST}), hash_development_credential("s")
            ),
            DevelopmentIdentity(
                "lead", frozenset({ApprovalRole.LAB_LEAD}), hash_development_credential("l")
            ),
        ),
        clock=lambda: NOW,
    )


async def _authorized(store: StateStore, mcp: FakeMCP) -> None:
    setup = _setup()
    doc = ArtifactStore(store.conn).create_artifact(
        canvas_id=CANVAS,
        idempotency_key="setup",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.APPROVED_FOR_IN_SILICO,
        payload=setup.model_dump(),
        provenance=ArtifactProvenance(provider="harness"),
        round=1,
    )
    ArtifactStore(store.conn).map_widget(doc.opaque_id, canvas_id=CANVAS, widget_id="setup")
    mcp.seed_widget("setup", "Browser", title="[EXP:Setup v001]")
    await run_in_silico_validation(
        mcp,
        _settings(),
        store,
        DeterministicInSilicoAdapter(),
        CANVAS,
        "setup",
        1,
    )
    validation = store.list_validation_results(CANVAS)[0]
    for role, secret in ((ApprovalRole.SCIENTIST, "s"), (ApprovalRole.LAB_LEAD, "l")):
        await submit_approval(
            canvas_id=CANVAS,
            current_proposal_hash=validation.proposal_hash,
            current_validation_result_hash=hash_validation_result(validation),
            submission=ApprovalSubmission(
                approval_id=f"{role.value}-result",
                required_role=role,
                decision=ApprovalDecision.APPROVE,
                rationale="reviewed",
                credential=SecretStr(secret),
            ),
            state_store=store,
            identity_provider=_provider(),
        )


@pytest.mark.asyncio
async def test_governed_crash_after_generation_recovers_without_model_call(
    tmp_path, monkeypatch
) -> None:
    store = StateStore(tmp_path / "state.db")
    mcp = FakeMCP()
    await _authorized(store, mcp)
    settings = _settings()
    settings.provider_endpoints = {"openai": "https://api.openai.com"}
    settings.model_pricing = {"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}}
    settings.pricing_version = "test-v1"
    inner = ScriptedAdapter({"ExperimentResult": {"summary": "canonical", "metrics": ["signal=1"]}})
    gov = build_context(
        store, settings, CANVAS, {"openai": inner}, [{"classification": "internal"}]
    )
    original = robot_module.write_result_node
    crashed = False

    async def crash_once(*args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise RuntimeError("crash before Canvas projection")
        return await original(*args, **kwargs)

    monkeypatch.setattr(robot_module, "write_result_node", crash_once)
    with pytest.raises(RuntimeError, match="crash before"):
        await run_on_robot(
            mcp,
            GovernedAdapter(gov),
            settings,
            store,
            canvas_id=CANVAS,
            setup_id="setup",
            setup_text="ignored",
            robot_id="robot",
            round_index=1,
            execution_mode="auto",
        )
    assert (
        store.get_result_generation(
            CANVAS,
            "setup",
            1,
            *[
                store.list_validation_results(CANVAS)[0].proposal_hash,
                hash_validation_result(store.list_validation_results(CANVAS)[0]),
            ],
        )
        is not None
    )

    result_id, result = await run_on_robot(
        mcp,
        GovernedAdapter(gov),
        settings,
        store,
        canvas_id=CANVAS,
        setup_id="setup",
        setup_text="ignored",
        robot_id="robot",
        round_index=1,
        execution_mode="auto",
    )
    assert result_id and result is not None and result.summary == "canonical"
    assert inner.schema_calls == ["ExperimentResult"]
    store.close()


@pytest.mark.asyncio
async def test_duplicate_ungoverned_replay_returns_canonical_result(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    mcp = FakeMCP()
    await _authorized(store, mcp)
    settings = _settings()
    first_adapter = ScriptedAdapter({"ExperimentResult": {"summary": "first", "metrics": ["x=1"]}})
    first_id, first = await run_on_robot(
        mcp,
        first_adapter,
        settings,
        store,
        canvas_id=CANVAS,
        setup_id="setup",
        setup_text="ignored",
        robot_id="robot",
        round_index=1,
        execution_mode="auto",
    )
    second_adapter = ScriptedAdapter(
        {"ExperimentResult": {"summary": "different", "metrics": ["x=2"]}}
    )
    second_id, second = await run_on_robot(
        mcp,
        second_adapter,
        settings,
        store,
        canvas_id=CANVAS,
        setup_id="setup",
        setup_text="ignored",
        robot_id="robot",
        round_index=1,
        execution_mode="auto",
    )
    assert second_id == first_id and second == first and second_adapter.schema_calls == []
    store.close()
