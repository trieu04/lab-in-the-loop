"""State-machine tests for Phase 7 validation and approval gates."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from lab_agent.approval import TransitionDeniedError, TransitionEvidence, require_transition
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import (
    ApprovalDecision,
    ApprovalRole,
    GateApproval,
    IdentityAssertion,
    InSilicoResult,
    ValidationDecision,
    ValidationMode,
    hash_proposal,
    hash_validation_result,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _result(decision: ValidationDecision = ValidationDecision.PROCEED) -> InSilicoResult:
    setup = ExperimentSetup(rationale="Bound proposal", steps=["measure"], expected_readouts=["signal"])
    return InSilicoResult(
        validation_id="validation-1",
        proposal_hash=hash_proposal(setup),
        decision=decision,
        predicted_outcome="Structural dry-run outcome.",
        confidence=0.9,
        uncertainty="No scientific simulation was executed.",
        assumptions=("Structural checks only.",),
        risk_flags=("dry-run-only",),
        adapter_name="deterministic-mock",
        adapter_version="1",
        algorithm_version="structural-rules-v1",
        mode=ValidationMode.DRY_RUN,
        completed_at=NOW,
    )


def _approval(
    result: InSilicoResult,
    role: ApprovalRole,
    decision: ApprovalDecision = ApprovalDecision.APPROVE,
    *,
    proposal_hash: str | None = None,
    result_hash: str | None = None,
) -> GateApproval:
    return GateApproval(
        approval_id=f"approval-{role.value}",
        proposal_hash=proposal_hash or result.proposal_hash,
        validation_result_hash=result_hash or hash_validation_result(result),
        identity=IdentityAssertion(
            actor_id=f"actor-{role.value}",
            role=role,
            credential_domain="research.example",
            provider="test-idp",
            verified_at=NOW,
        ),
        decision=decision,
        rationale="Reviewed current proposal and validation evidence.",
        decided_at=NOW,
        validation_adapter=result.adapter_name,
        validation_adapter_version=result.adapter_version,
        validation_algorithm_version=result.algorithm_version,
    )


def test_pre_validation_sequence_rejects_skips_and_reversals() -> None:
    require_transition(DecisionState.DRAFT, DecisionState.NEEDS_REVIEW)
    require_transition(DecisionState.NEEDS_REVIEW, DecisionState.APPROVED_FOR_IN_SILICO)
    require_transition(DecisionState.APPROVED_FOR_IN_SILICO, DecisionState.IN_SILICO_RUNNING)

    with pytest.raises(TransitionDeniedError, match="DRAFT->IN_SILICO_RUNNING"):
        require_transition(DecisionState.DRAFT, DecisionState.IN_SILICO_RUNNING)
    with pytest.raises(TransitionDeniedError, match="NEEDS_REVIEW->DRAFT"):
        require_transition(DecisionState.NEEDS_REVIEW, DecisionState.DRAFT)


def test_validation_completion_requires_a_result_for_the_current_proposal() -> None:
    result = _result()

    with pytest.raises(TransitionDeniedError, match="required"):
        require_transition(DecisionState.IN_SILICO_RUNNING, DecisionState.IN_SILICO_COMPLETE)
    with pytest.raises(TransitionDeniedError, match="stale"):
        require_transition(
            DecisionState.IN_SILICO_RUNNING,
            DecisionState.IN_SILICO_COMPLETE,
            TransitionEvidence(proposal_hash="0" * 64, validation_result=result),
        )

    require_transition(
        DecisionState.IN_SILICO_RUNNING,
        DecisionState.IN_SILICO_COMPLETE,
        TransitionEvidence(proposal_hash=result.proposal_hash, validation_result=result),
    )


@pytest.mark.parametrize(
    ("decision", "target"),
    [
        (ValidationDecision.PROCEED, DecisionState.NEEDS_SCIENTIST_REVIEW),
        (ValidationDecision.REVISE, DecisionState.NEEDS_REVIEW),
        (ValidationDecision.REJECT, DecisionState.REJECTED),
    ],
)
def test_validation_decision_selects_exactly_one_next_state(
    decision: ValidationDecision, target: DecisionState
) -> None:
    result = _result(decision)
    evidence = TransitionEvidence(proposal_hash=result.proposal_hash, validation_result=result)

    require_transition(DecisionState.IN_SILICO_COMPLETE, target, evidence)
    wrong_target = DecisionState.REJECTED if target is not DecisionState.REJECTED else DecisionState.NEEDS_REVIEW
    with pytest.raises(TransitionDeniedError, match="does not match"):
        require_transition(DecisionState.IN_SILICO_COMPLETE, wrong_target, evidence)


def test_scientist_then_lab_lead_order_is_required() -> None:
    result = _result()
    scientist = _approval(result, ApprovalRole.SCIENTIST)
    lead = _approval(result, ApprovalRole.LAB_LEAD)
    base = TransitionEvidence(proposal_hash=result.proposal_hash, validation_result=result)

    with pytest.raises(TransitionDeniedError, match="scientist approval evidence"):
        require_transition(
            DecisionState.NEEDS_SCIENTIST_REVIEW,
            DecisionState.NEEDS_LAB_LEAD_APPROVAL,
            base,
        )

    require_transition(
        DecisionState.NEEDS_SCIENTIST_REVIEW,
        DecisionState.NEEDS_LAB_LEAD_APPROVAL,
        replace(base, scientist_approval=scientist),
    )

    with pytest.raises(TransitionDeniedError, match="lab_lead approval evidence"):
        require_transition(
            DecisionState.NEEDS_LAB_LEAD_APPROVAL,
            DecisionState.APPROVED_FOR_WET_LAB,
            replace(base, scientist_approval=scientist),
        )

    require_transition(
        DecisionState.NEEDS_LAB_LEAD_APPROVAL,
        DecisionState.APPROVED_FOR_WET_LAB,
        replace(base, scientist_approval=scientist, lab_lead_approval=lead),
    )


def test_stale_or_wrong_role_approvals_fail_closed() -> None:
    result = _result()
    base = TransitionEvidence(proposal_hash=result.proposal_hash, validation_result=result)

    with pytest.raises(TransitionDeniedError, match="scientist decision"):
        require_transition(
            DecisionState.NEEDS_SCIENTIST_REVIEW,
            DecisionState.NEEDS_LAB_LEAD_APPROVAL,
            replace(base, scientist_approval=_approval(result, ApprovalRole.LAB_LEAD)),
        )

    with pytest.raises(TransitionDeniedError, match="stale for the validation result"):
        require_transition(
            DecisionState.NEEDS_SCIENTIST_REVIEW,
            DecisionState.NEEDS_LAB_LEAD_APPROVAL,
            replace(
                base,
                scientist_approval=_approval(result, ApprovalRole.SCIENTIST, result_hash="0" * 64),
            ),
        )


def test_phase_7_terminal_state_has_no_execution_transition() -> None:
    with pytest.raises(TransitionDeniedError, match="APPROVED_FOR_WET_LAB->RUNNING"):
        require_transition(DecisionState.APPROVED_FOR_WET_LAB, DecisionState.RUNNING)
