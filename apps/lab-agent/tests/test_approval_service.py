"""Credential-bearing approval service contract tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from lab_agent.approval import project_gate_state
from lab_agent.approval_service import ApprovalServiceError, ApprovalSubmission, submit_approval
from lab_agent.integrations.identity import (
    DevelopmentIdentity,
    IdentityAuthenticationError,
    StaticDevelopmentIdentityProvider,
    hash_development_credential,
)
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import (
    ApprovalDecision,
    ApprovalRole,
    InSilicoResult,
    ValidationDecision,
    ValidationMode,
    hash_validation_result,
)
from lab_agent.state.gate_evidence import GateEvidenceConflictError
from lab_agent.state_store import StateStore

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
PROPOSAL_HASH = "a" * 64
SCIENTIST_SECRET = "scientist-local-secret"
LEAD_SECRET = "lead-local-secret"


def _result(decision: ValidationDecision = ValidationDecision.PROCEED) -> InSilicoResult:
    return InSilicoResult(
        validation_id=f"validation-{decision.value}",
        proposal_hash=PROPOSAL_HASH,
        decision=decision,
        predicted_outcome="The structural dry-run stays within the reviewed envelope.",
        confidence=0.8,
        uncertainty="This deterministic validation is not a scientific simulation.",
        adapter_name="deterministic-mock",
        adapter_version="1",
        algorithm_version="rules-v1",
        mode=ValidationMode.DRY_RUN,
        completed_at=NOW,
    )


def _provider(clock: Callable[[], datetime] = lambda: NOW) -> StaticDevelopmentIdentityProvider:
    identities = (
        DevelopmentIdentity("scientist-1", frozenset({ApprovalRole.SCIENTIST}), hash_development_credential(SCIENTIST_SECRET)),
        DevelopmentIdentity("lead-1", frozenset({ApprovalRole.LAB_LEAD}), hash_development_credential(LEAD_SECRET)),
    )
    return StaticDevelopmentIdentityProvider(identities=identities, clock=clock)


def _submission(
    approval_id: str,
    role: ApprovalRole,
    secret: str,
    decision: ApprovalDecision = ApprovalDecision.APPROVE,
    rationale: str = "The current dry-run evidence supports this review decision.",
) -> ApprovalSubmission:
    return ApprovalSubmission(
        approval_id=approval_id,
        required_role=role,
        decision=decision,
        rationale=rationale,
        credential=SecretStr(secret),
    )

async def _submit(store: StateStore, result: InSilicoResult, submission: ApprovalSubmission, provider: StaticDevelopmentIdentityProvider | None = None):
    return await submit_approval(canvas_id="canvas-1", current_proposal_hash=PROPOSAL_HASH, current_validation_result_hash=hash_validation_result(result), submission=submission, state_store=store, identity_provider=_provider() if provider is None else provider)

async def test_scientist_then_lead_sequence_projects_dry_run_as_non_production(store) -> None:
    result = _result()
    store.append_validation_result("canvas-1", result)

    scientist = await _submit(store, result, _submission("scientist-approval", ApprovalRole.SCIENTIST, SCIENTIST_SECRET))
    lead = await _submit(store, result, _submission("lead-approval", ApprovalRole.LAB_LEAD, LEAD_SECRET))

    assert scientist.state is DecisionState.NEEDS_LAB_LEAD_APPROVAL
    assert lead.state is DecisionState.APPROVED_FOR_WET_LAB
    assert scientist.production_eligible is lead.production_eligible is False
    assert {approval.identity.role for approval in store.list_gate_approvals("canvas-1")} == {
        ApprovalRole.SCIENTIST,
        ApprovalRole.LAB_LEAD,
    }


async def test_wrong_order_and_role_fail_before_persisting(store) -> None:
    result = _result()
    store.append_validation_result("canvas-1", result)

    with pytest.raises(ApprovalServiceError, match="scientist"):
        await _submit(store, result, _submission("lead-approval", ApprovalRole.LAB_LEAD, LEAD_SECRET))
    with pytest.raises(ApprovalServiceError, match="scientist"):
        await _submit(store, result, _submission("wrong-role", ApprovalRole.LAB_LEAD, SCIENTIST_SECRET))

    assert store.list_gate_approvals("canvas-1") == []


async def test_bad_credential_never_creates_an_approval(store) -> None:
    result = _result()
    store.append_validation_result("canvas-1", result)

    with pytest.raises(IdentityAuthenticationError):
        await _submit(store, result, _submission("scientist-approval", ApprovalRole.SCIENTIST, "bad-secret"))

    assert store.list_gate_approvals("canvas-1") == []


@pytest.mark.parametrize("proposal_hash,result_hash", [("b" * 64, None), (None, "b" * 64)])
async def test_stale_hashes_fail_closed(store, proposal_hash: str | None, result_hash: str | None) -> None:
    result = _result()
    store.append_validation_result("canvas-1", result)
    submitted_result_hash = result_hash or hash_validation_result(result)

    with pytest.raises(ApprovalServiceError, match="current validation evidence"):
        await submit_approval(canvas_id="canvas-1", current_proposal_hash=proposal_hash or PROPOSAL_HASH, current_validation_result_hash=submitted_result_hash, submission=_submission("scientist-approval", ApprovalRole.SCIENTIST, SCIENTIST_SECRET), state_store=store, identity_provider=_provider())


@pytest.mark.parametrize(
    ("decision", "expected_state"),
    [(ValidationDecision.REVISE, DecisionState.NEEDS_REVIEW), (ValidationDecision.REJECT, DecisionState.REJECTED)],
)
async def test_validation_revise_or_reject_cannot_accept_approval(store, decision, expected_state) -> None:
    result = _result(decision)
    store.append_validation_result("canvas-1", result)

    with pytest.raises(ApprovalServiceError, match="does not accept"):
        await _submit(store, result, _submission("scientist-approval", ApprovalRole.SCIENTIST, SCIENTIST_SECRET))

    projection = store.load_current_gate_evidence("canvas-1", proposal_hash=PROPOSAL_HASH, validation_result_hash=hash_validation_result(result))
    assert project_gate_state(projection.validation_result, projection.approvals) is expected_state


async def test_duplicate_and_conflicting_replays_fail_closed(store) -> None:
    result = _result()
    store.append_validation_result("canvas-1", result)
    submission = _submission("scientist-approval", ApprovalRole.SCIENTIST, SCIENTIST_SECRET)

    first = await _submit(store, result, submission)
    replay = await _submit(store, result, submission)
    assert replay == first
    with pytest.raises(GateEvidenceConflictError, match="approval identity"):
        await _submit(store, result, _submission("scientist-approval", ApprovalRole.SCIENTIST, SCIENTIST_SECRET, rationale="Changed rationale."))
    with pytest.raises(ApprovalServiceError, match="lab_lead"):
        await _submit(store, result, _submission("duplicate-role", ApprovalRole.SCIENTIST, SCIENTIST_SECRET))


async def test_terminal_lab_lead_replay_ignores_advanced_verification_timestamp(store) -> None:
    result = _result()
    store.append_validation_result("canvas-1", result)
    timestamps = iter((NOW, NOW + timedelta(seconds=1), NOW + timedelta(seconds=2)))
    provider = _provider(clock=lambda: next(timestamps))
    scientist = await _submit(store, result, _submission("scientist-approval", ApprovalRole.SCIENTIST, SCIENTIST_SECRET), provider)
    first = await _submit(store, result, _submission("lead-approval", ApprovalRole.LAB_LEAD, LEAD_SECRET), provider)
    replay = await _submit(store, result, _submission("lead-approval", ApprovalRole.LAB_LEAD, LEAD_SECRET), provider)

    assert replay == first
    assert store.list_gate_approvals("canvas-1") == [scientist.approval, first.approval]


async def test_human_reject_is_terminal_and_credential_is_not_persisted(store) -> None:
    result = _result()
    store.append_validation_result("canvas-1", result)

    outcome = await _submit(
        store,
        result,
        _submission("scientist-reject", ApprovalRole.SCIENTIST, SCIENTIST_SECRET, ApprovalDecision.REJECT),
    )

    durable_text = str(store.conn.execute("SELECT approval_json FROM gate_approvals").fetchall()) + str(store.list_audit_events("canvas-1"))
    assert outcome.state is DecisionState.REJECTED
    assert SCIENTIST_SECRET not in durable_text


async def test_restart_projects_approved_evidence_from_the_durable_store(tmp_path, clock, rng) -> None:
    path = tmp_path / "approval-service.db"
    result = _result()
    first = StateStore(path, clock=clock, rng=rng)
    first.append_validation_result("canvas-1", result)
    await _submit(first, result, _submission("scientist-approval", ApprovalRole.SCIENTIST, SCIENTIST_SECRET))
    await _submit(first, result, _submission("lead-approval", ApprovalRole.LAB_LEAD, LEAD_SECRET))
    first.close()

    restarted = StateStore(path, clock=clock, rng=rng)
    try:
        evidence = restarted.load_current_gate_evidence("canvas-1", proposal_hash=PROPOSAL_HASH, validation_result_hash=hash_validation_result(result))
        assert project_gate_state(evidence.validation_result, evidence.approvals) is DecisionState.APPROVED_FOR_WET_LAB
    finally:
        restarted.close()
