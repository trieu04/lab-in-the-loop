"""Credential-bearing service for ordered, durable human approvals."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from lab_agent.approval import TransitionEvidence, project_gate_state, require_transition
from lab_agent.integrations.identity import IdentityProvider, IdentityVerificationError
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import (
    ApprovalDecision,
    ApprovalRole,
    GateApproval,
    InSilicoResult,
    ValidationMode,
)
from lab_agent.state.gate_evidence import GateEvidenceConflictError
from lab_agent.state_store import StateStore

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")

class ApprovalServiceError(RuntimeError):
    """An approval request cannot safely alter the durable approval projection."""

class RobotExecutionAuthorizationError(ApprovalServiceError):
    """The current proposal has not passed the required durable gates."""

class ApprovalSubmission(BaseModel):
    """Untrusted approval input; its credential is used only for verification."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    approval_id: str = Field(min_length=1, max_length=200)
    required_role: ApprovalRole
    decision: ApprovalDecision
    rationale: str = Field(min_length=1, max_length=2000)
    credential: SecretStr

@dataclass(frozen=True)
class ApprovalOutcome:
    """Persisted approval and durable state projection after a submission."""
    approval: GateApproval
    state: DecisionState
    production_eligible: bool
    tenant_id: str = "default"
    canvas_id: str = "default"

@dataclass(frozen=True)
class ExecutionAuthorization:
    tenant_id: str
    canvas_id: str
    proposal_hash: str
    validation_result_hash: str
    state: DecisionState

def require_current_execution_authorization(
    state_store: StateStore, canvas_id: str, proposal_hash: str, validation_result_hash: str
) -> ExecutionAuthorization:
    """Return current ordered approval proof or reject execution without mutation."""

    _validate_request_context(canvas_id, proposal_hash, validation_result_hash)
    evidence = state_store.load_current_gate_evidence(
        canvas_id, proposal_hash=proposal_hash, validation_result_hash=validation_result_hash
    )
    if evidence.validation_result is None:
        raise RobotExecutionAuthorizationError("current validation evidence is missing or stale")
    try:
        state = project_gate_state(evidence.validation_result, evidence.approvals)
    except Exception as exc:
        raise RobotExecutionAuthorizationError("current approval evidence is invalid") from exc
    if state is not DecisionState.APPROVED_FOR_WET_LAB:
        raise RobotExecutionAuthorizationError("current ordered approval evidence is required")
    return ExecutionAuthorization(
        state_store.tenant_id, canvas_id, proposal_hash, validation_result_hash, state
    )

def _activation_key(canvas_id: str, setup_id: str, proposal_hash: str, result_hash: str) -> str:
    material = f"manual-activation:{canvas_id}:{setup_id}:{proposal_hash}:{result_hash}".encode()
    return f"manual-activation:{hashlib.sha256(material).hexdigest()}"

async def submit_manual_execution_activation(
    *, canvas_id: str, setup_id: str, current_proposal_hash: str,
    current_validation_result_hash: str, credential: SecretStr, required_role: ApprovalRole,
    state_store: StateStore, identity_provider: IdentityProvider,
) -> None:
    """Verify an actor, then durably activate one exact approved manual run."""

    _validate_request_context(canvas_id, current_proposal_hash, current_validation_result_hash)
    if not setup_id or len(setup_id) > 200:
        raise ApprovalServiceError("setup_id must be between 1 and 200 characters")
    if not isinstance(credential, SecretStr) or not isinstance(required_role, ApprovalRole):
        raise ApprovalServiceError("credential and required_role are required")
    try:
        identity = await identity_provider.verify(credential, required_role)
    except IdentityVerificationError:
        raise
    except Exception as exc:
        raise ApprovalServiceError("identity verification failed") from exc
    if identity.role is not required_role:
        raise ApprovalServiceError("verified identity role does not match required role")
    require_current_execution_authorization(
        state_store, canvas_id, current_proposal_hash, current_validation_result_hash
    )
    key = _activation_key(canvas_id, setup_id, current_proposal_hash, current_validation_result_hash)
    intent = state_store.prepare_intent(
        idempotency_key=key, canvas_id=canvas_id, kind="manual_execution_activation",
        input_hash=hashlib.sha256(f"{current_proposal_hash}:{current_validation_result_hash}:manual".encode()).hexdigest(),
    )
    if intent.status.value != "reconciled":
        state_store.mark_intent_reconciled(key, canvas_id=canvas_id)
        state_store.append_audit_event(canvas_id, "manual_execution_activated", {
            "setup_id": setup_id, "proposal_hash": current_proposal_hash,
            "validation_result_hash": current_validation_result_hash,
            "identity": identity.model_dump(mode="json"),
        })

def has_manual_execution_activation(
    state_store: StateStore, canvas_id: str, setup_id: str, proposal_hash: str, validation_result_hash: str
) -> bool:
    """Return whether exact, already-authorized manual activation is durable."""

    if not setup_id or len(setup_id) > 200:
        return False
    intent = state_store.get_intent(_activation_key(canvas_id, setup_id, proposal_hash, validation_result_hash), canvas_id=canvas_id)
    return intent is not None and intent.status.value == "reconciled"

async def submit_approval(
    *, canvas_id: str, current_proposal_hash: str, current_validation_result_hash: str,
    submission: ApprovalSubmission, state_store: StateStore, identity_provider: IdentityProvider,
) -> ApprovalOutcome:
    """Verify and append one ordered approval, or fail without advancing state."""
    _validate_request_context(canvas_id, current_proposal_hash, current_validation_result_hash)
    evidence = state_store.load_current_gate_evidence(
        canvas_id, proposal_hash=current_proposal_hash, validation_result_hash=current_validation_result_hash
    )
    result = evidence.validation_result
    if result is None:
        raise ApprovalServiceError("current validation evidence is missing or stale")
    existing = state_store.get_gate_approval(canvas_id, submission.approval_id)
    if existing is None:
        current_state = project_gate_state(result, evidence.approvals)
        expected_role = _required_role(current_state)
        if submission.required_role is not expected_role:
            raise ApprovalServiceError(f"current gate requires {expected_role.value} approval")
    try:
        identity = await identity_provider.verify(submission.credential, submission.required_role)
    except IdentityVerificationError:
        raise
    except Exception as exc:
        raise ApprovalServiceError("identity verification failed") from exc
    if identity.role is not submission.required_role:
        raise ApprovalServiceError("verified identity role does not match required role")
    approval = GateApproval(
        tenant_id=state_store.tenant_id, canvas_id=canvas_id,
        approval_id=submission.approval_id, proposal_hash=current_proposal_hash,
        validation_result_hash=current_validation_result_hash, identity=identity, decision=submission.decision,
        rationale=submission.rationale, decided_at=identity.verified_at, validation_adapter=result.adapter_name,
        validation_adapter_version=result.adapter_version, validation_algorithm_version=result.algorithm_version,
    )
    if existing is not None:
        if existing.model_dump(exclude={"identity": {"verified_at"}, "decided_at": True}) != approval.model_dump(exclude={"identity": {"verified_at"}, "decided_at": True}):
            raise GateEvidenceConflictError("approval identity has incompatible evidence")
        return _project_outcome(canvas_id, current_proposal_hash, current_validation_result_hash, state_store, existing)
    prospective = evidence.approvals + (approval,)
    require_transition(current_state, project_gate_state(result, prospective), _transition_evidence(result, prospective))
    return _project_outcome(canvas_id, current_proposal_hash, current_validation_result_hash, state_store, state_store.append_gate_approval(canvas_id, approval))

def _required_role(state: DecisionState) -> ApprovalRole:
    if state is DecisionState.NEEDS_SCIENTIST_REVIEW:
        return ApprovalRole.SCIENTIST
    if state is DecisionState.NEEDS_LAB_LEAD_APPROVAL:
        return ApprovalRole.LAB_LEAD
    raise ApprovalServiceError(f"current gate state {state.value} does not accept approvals")

def _project_outcome(
    canvas_id: str, proposal_hash: str, validation_result_hash: str, state_store: StateStore, approval: GateApproval
) -> ApprovalOutcome:
    evidence = state_store.load_current_gate_evidence(
        canvas_id, proposal_hash=proposal_hash, validation_result_hash=validation_result_hash
    )
    if evidence.validation_result is None:
        raise ApprovalServiceError("persisted validation evidence disappeared")
    state = project_gate_state(evidence.validation_result, evidence.approvals)
    eligible = evidence.validation_result.mode is ValidationMode.REAL and all(
        item.identity.production_eligible for item in evidence.approvals
    )
    return ApprovalOutcome(approval, state, eligible, state_store.tenant_id, canvas_id)

def _transition_evidence(result: InSilicoResult, approvals: tuple[GateApproval, ...]) -> TransitionEvidence:
    slots = {approval.identity.role: approval for approval in approvals}
    return TransitionEvidence(result.proposal_hash, result, slots.get(ApprovalRole.SCIENTIST), slots.get(ApprovalRole.LAB_LEAD))

def _validate_request_context(canvas_id: str, proposal_hash: str, validation_result_hash: str) -> None:
    if not canvas_id or len(canvas_id) > 200:
        raise ApprovalServiceError("canvas_id must be between 1 and 200 characters")
    if not _HASH_PATTERN.fullmatch(proposal_hash):
        raise ApprovalServiceError("current_proposal_hash must be a SHA-256 hash")
    if not _HASH_PATTERN.fullmatch(validation_result_hash):
        raise ApprovalServiceError("current_validation_result_hash must be a SHA-256 hash")

__all__ = ["ApprovalOutcome", "ApprovalServiceError", "ApprovalSubmission", "ExecutionAuthorization", "RobotExecutionAuthorizationError", "has_manual_execution_activation", "require_current_execution_authorization", "submit_approval", "submit_manual_execution_activation"]
