"""Fail-closed transition guard for validation and ordered human approvals."""

from __future__ import annotations

from dataclasses import dataclass

from lab_agent.models.states import DecisionState
from lab_agent.models.validation import (
    ApprovalDecision,
    ApprovalRole,
    GateApproval,
    InSilicoResult,
    ValidationDecision,
    hash_validation_result,
)


class TransitionDeniedError(RuntimeError):
    """A requested gate transition lacks current authoritative evidence."""


@dataclass(frozen=True)
class TransitionEvidence:
    """Current proposal and durable evidence used to authorize a transition."""

    proposal_hash: str | None = None
    validation_result: InSilicoResult | None = None
    scientist_approval: GateApproval | None = None
    lab_lead_approval: GateApproval | None = None


_ALLOWED_TARGETS: dict[DecisionState, frozenset[DecisionState]] = {
    DecisionState.DRAFT: frozenset({DecisionState.NEEDS_REVIEW}),
    DecisionState.NEEDS_REVIEW: frozenset({DecisionState.APPROVED_FOR_IN_SILICO}),
    DecisionState.APPROVED_FOR_IN_SILICO: frozenset({DecisionState.IN_SILICO_RUNNING}),
    DecisionState.IN_SILICO_RUNNING: frozenset({DecisionState.IN_SILICO_COMPLETE}),
    DecisionState.IN_SILICO_COMPLETE: frozenset(
        {
            DecisionState.NEEDS_SCIENTIST_REVIEW,
            DecisionState.NEEDS_REVIEW,
            DecisionState.REJECTED,
        }
    ),
    DecisionState.NEEDS_SCIENTIST_REVIEW: frozenset(
        {DecisionState.NEEDS_LAB_LEAD_APPROVAL, DecisionState.REJECTED}
    ),
    DecisionState.NEEDS_LAB_LEAD_APPROVAL: frozenset(
        {DecisionState.APPROVED_FOR_WET_LAB, DecisionState.REJECTED}
    ),
}


def require_transition(
    current: DecisionState,
    target: DecisionState,
    evidence: TransitionEvidence | None = None,
) -> None:
    """Authorize one lifecycle edge or raise without mutating state."""

    if target not in _ALLOWED_TARGETS.get(current, frozenset()):
        raise TransitionDeniedError(f"transition denied: {current.value}->{target.value}")

    current_evidence = evidence or TransitionEvidence()
    if current is DecisionState.IN_SILICO_RUNNING:
        _require_current_result(current_evidence)
    elif current is DecisionState.IN_SILICO_COMPLETE:
        _require_validation_decision(target, current_evidence)
    elif current is DecisionState.NEEDS_SCIENTIST_REVIEW:
        expected = ApprovalDecision.APPROVE if target is DecisionState.NEEDS_LAB_LEAD_APPROVAL else ApprovalDecision.REJECT
        _require_approval(current_evidence, current_evidence.scientist_approval, ApprovalRole.SCIENTIST, expected)
    elif current is DecisionState.NEEDS_LAB_LEAD_APPROVAL:
        _require_approval(
            current_evidence,
            current_evidence.scientist_approval,
            ApprovalRole.SCIENTIST,
            ApprovalDecision.APPROVE,
        )
        expected = ApprovalDecision.APPROVE if target is DecisionState.APPROVED_FOR_WET_LAB else ApprovalDecision.REJECT
        _require_approval(current_evidence, current_evidence.lab_lead_approval, ApprovalRole.LAB_LEAD, expected)


def _require_current_result(evidence: TransitionEvidence) -> InSilicoResult:
    result = evidence.validation_result
    if result is None or evidence.proposal_hash is None:
        raise TransitionDeniedError("current proposal and validation result are required")
    if result.proposal_hash != evidence.proposal_hash:
        raise TransitionDeniedError("validation result is stale for the current proposal")
    return result


def _require_validation_decision(target: DecisionState, evidence: TransitionEvidence) -> None:
    result = _require_current_result(evidence)
    expected_targets = {
        ValidationDecision.PROCEED: DecisionState.NEEDS_SCIENTIST_REVIEW,
        ValidationDecision.REVISE: DecisionState.NEEDS_REVIEW,
        ValidationDecision.REJECT: DecisionState.REJECTED,
    }
    if target is not expected_targets[result.decision]:
        raise TransitionDeniedError("target does not match the validation decision")


def _require_approval(
    evidence: TransitionEvidence,
    approval: GateApproval | None,
    role: ApprovalRole,
    decision: ApprovalDecision,
) -> None:
    result = _require_current_result(evidence)
    if approval is None:
        raise TransitionDeniedError(f"{role.value} approval evidence is required")
    if approval.identity.role is not role or approval.decision is not decision:
        raise TransitionDeniedError(f"{role.value} decision does not authorize this transition")
    if approval.proposal_hash != evidence.proposal_hash:
        raise TransitionDeniedError(f"{role.value} approval is stale for the current proposal")
    if approval.validation_result_hash != hash_validation_result(result):
        raise TransitionDeniedError(f"{role.value} approval is stale for the validation result")
    expected_metadata = (
        result.adapter_name,
        result.adapter_version,
        result.algorithm_version,
    )
    approval_metadata = (
        approval.validation_adapter,
        approval.validation_adapter_version,
        approval.validation_algorithm_version,
    )
    if approval_metadata != expected_metadata:
        raise TransitionDeniedError(f"{role.value} approval validation metadata does not match")


def project_gate_state(
    validation_result: InSilicoResult | None,
    approvals: tuple[GateApproval, ...],
) -> DecisionState:
    """Project the only safe state represented by current durable gate evidence."""

    if validation_result is None:
        raise TransitionDeniedError("current validation evidence is required")
    evidence = TransitionEvidence(
        proposal_hash=validation_result.proposal_hash,
        validation_result=validation_result,
    )
    if validation_result.decision is ValidationDecision.REVISE:
        require_transition(DecisionState.IN_SILICO_COMPLETE, DecisionState.NEEDS_REVIEW, evidence)
        return DecisionState.NEEDS_REVIEW
    if validation_result.decision is ValidationDecision.REJECT:
        require_transition(DecisionState.IN_SILICO_COMPLETE, DecisionState.REJECTED, evidence)
        return DecisionState.REJECTED

    require_transition(DecisionState.IN_SILICO_COMPLETE, DecisionState.NEEDS_SCIENTIST_REVIEW, evidence)
    slots: dict[ApprovalRole, GateApproval] = {}
    for approval in approvals:
        role = approval.identity.role
        if role in slots:
            raise TransitionDeniedError(f"duplicate {role.value} approval evidence")
        slots[role] = approval
    scientist = slots.get(ApprovalRole.SCIENTIST)
    lead = slots.get(ApprovalRole.LAB_LEAD)
    if scientist is None:
        if lead is not None and lead.decision is ApprovalDecision.APPROVE:
            raise TransitionDeniedError("lab lead approval precedes scientist approval")
        return DecisionState.REJECTED if lead is not None else DecisionState.NEEDS_SCIENTIST_REVIEW

    evidence = TransitionEvidence(
        proposal_hash=validation_result.proposal_hash,
        validation_result=validation_result,
        scientist_approval=scientist,
        lab_lead_approval=lead,
    )
    if scientist.decision is ApprovalDecision.REJECT:
        require_transition(DecisionState.NEEDS_SCIENTIST_REVIEW, DecisionState.REJECTED, evidence)
        return DecisionState.REJECTED
    require_transition(DecisionState.NEEDS_SCIENTIST_REVIEW, DecisionState.NEEDS_LAB_LEAD_APPROVAL, evidence)
    if lead is None:
        return DecisionState.NEEDS_LAB_LEAD_APPROVAL
    if lead.decision is ApprovalDecision.REJECT:
        require_transition(DecisionState.NEEDS_LAB_LEAD_APPROVAL, DecisionState.REJECTED, evidence)
        return DecisionState.REJECTED
    require_transition(DecisionState.NEEDS_LAB_LEAD_APPROVAL, DecisionState.APPROVED_FOR_WET_LAB, evidence)
    return DecisionState.APPROVED_FOR_WET_LAB


__all__ = ["TransitionDeniedError", "TransitionEvidence", "project_gate_state", "require_transition"]
