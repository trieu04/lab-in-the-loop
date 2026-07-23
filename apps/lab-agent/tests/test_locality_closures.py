"""Audited/rendered locality-denial closure regressions."""

from __future__ import annotations

from lab_agent.adapters.base import AdapterResponse
from lab_agent.config import Settings
from lab_agent.model_gateway import GovernedAdapter, build_context
from lab_agent.models.governance import StopReason
from lab_agent.orchestrator import run_loop
from lab_agent.watch import process_once
from tests.fakes import FakeMCP
from tests.test_model_gateway import SpyAdapter

LOOP = {
    "loop_connector_id": "connector-1",
    "setup_id": "setup-1",
    "result_id": "result-1",
    "robot_id": "robot-1",
    "round": 1,
}


def _settings(**kw) -> Settings:
    return Settings(
        artifact_public_base_url="https://lab.test",
        openai_api_key="key",
        provider_endpoints={"openai": "https://openai.example"},
        provider_data_classifications={"openai": ["internal"]},
        **kw
    )


def _governed(store, inner: SpyAdapter, classification: str) -> GovernedAdapter:
    return GovernedAdapter(
        build_context(
            store,
            _settings(),
            "canvas",
            {"openai": inner},
            [{"data_classification": classification}],
        )
    )


async def test_loop_locality_denial_closes_once_without_provider_or_follow_on_writes(store) -> None:
    mcp = FakeMCP(
        note_text={"setup-1": "Round: 1\nsetup", "result-1": "Round: 1\nresult"}
    )
    inner = SpyAdapter([AdapterResponse(parsed={"proceed": True, "reason": "continue"})])
    governed = _governed(store, inner, "restricted")

    summary = await run_loop(
        mcp,
        governed,
        _settings(),
        store,
        canvas_id="canvas",
        loop=dict(LOOP),
        gov=governed.context,
    )

    assert summary.stopped_reason == StopReason.LOCALITY_DENIAL.value
    assert inner.calls == 0
    generated = [row for key, row in mcp.notes.items() if key not in {"setup-1", "result-1"}]
    assert len(generated) == 1
    assert mcp.connectors == [("result-1", generated[0]["id"])]
    stopped = [event for event in store.list_audit_events("canvas") if event.event == "loop_stopped"]
    assert [event.payload["reason"] for event in stopped] == ["locality_denial"]


async def test_robot_trigger_locality_denial_writes_only_predecessor_closure(store) -> None:
    """Locality denial must write exactly one closure with authorized auto-mode fixture.

    Canonical setup, current validation result, ordered approvals, and wet-lab
    enabled to ensure authorization is in place, then verify locality denial
    writes no duplicate closure.
    """
    from datetime import UTC, datetime

    from pydantic import SecretStr

    from lab_agent.approval_service import ApprovalSubmission, submit_approval
    from lab_agent.artifact_store import ArtifactStore
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
    from lab_agent.orchestrator_validation import run_in_silico_validation

    now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)

    def _setup() -> ExperimentSetup:
        return ExperimentSetup(
            rationale="Grounded proposal.",
            conditions=["temperature=25C"],
            steps=["measure baseline"],
            expected_readouts=["signal"],
            hypothesis="Signal remains measurable.",
            success_criteria=["record signal"],
        )

    # Persist canonical setup
    document = ArtifactStore(store.conn).create_artifact(
        canvas_id="canvas",
        idempotency_key="setup",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.APPROVED_FOR_IN_SILICO,
        payload=_setup().model_dump(),
        provenance=ArtifactProvenance(provider="harness", source_widget_id="idea"),
        round=1,
    )
    ArtifactStore(store.conn).map_widget(document.opaque_id, canvas_id="canvas", widget_id="setup-1")

    # Run validation to get deterministic result
    dummy_mcp = FakeMCP()
    await run_in_silico_validation(
        dummy_mcp,
        _settings(wet_lab_execution_enabled=True),
        store,
        DeterministicInSilicoAdapter(),
        "canvas",
        "setup-1",
        1,
    )

    # Obtain ordered approvals
    result = store.list_validation_results("canvas")[0]
    provider = StaticDevelopmentIdentityProvider(
        identities=(
            DevelopmentIdentity("scientist", frozenset({ApprovalRole.SCIENTIST}), hash_development_credential("s")),
            DevelopmentIdentity("lead", frozenset({ApprovalRole.LAB_LEAD}), hash_development_credential("l")),
        ),
        clock=lambda: now,
    )
    for approval_id, role, secret in (
        ("scientist", ApprovalRole.SCIENTIST, "s"),
        ("lead", ApprovalRole.LAB_LEAD, "l"),
    ):
        await submit_approval(
            canvas_id="canvas",
            current_proposal_hash=result.proposal_hash,
            current_validation_result_hash=hash_validation_result(result),
            submission=ApprovalSubmission(
                approval_id=approval_id,
                required_role=role,
                decision=ApprovalDecision.APPROVE,
                rationale="Reviewed.",
                credential=SecretStr(secret),
            ),
            state_store=store,
            identity_provider=provider,
        )

    workflow = {
        "ideas_needing_setup": [],
        "setups_needing_run": [
            {
                "widget_id": "setup-1",
                "robot_id": "robot-1",
                "title": "[EXP:Setup v001]",
                "data_classification": "restricted",
                "execution_mode": "auto",
            }
        ],
        "loops": [],
    }
    mcp = FakeMCP(note_text={"setup-1": "Round: 1\nsetup"}, workflow=workflow)
    inner = SpyAdapter([AdapterResponse(parsed={"summary": "must not run", "metrics": []})])
    governed = _governed(store, inner, "internal")

    await process_once(
        mcp, governed, _settings(wet_lab_execution_enabled=True), store, "runtime", "canvas", governed.context
    )

    assert inner.calls == 0
    generated = [row for key, row in mcp.notes.items() if key != "setup-1"]
    assert len(generated) == 1
    # Governance closures are terminal nodes; no connectors created
    assert not [row for row in generated if row["title"].startswith("[EXP:Result")]
