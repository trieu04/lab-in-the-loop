"""Contract tests for Phase 7 validation and approval models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.validation import (
    ApprovalDecision,
    ApprovalRole,
    GateApproval,
    IdentityAssertion,
    InSilicoRequest,
    InSilicoResult,
    ValidationDecision,
    ValidationMode,
    hash_proposal,
    hash_validation_result,
)

NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _setup(**updates: object) -> ExperimentSetup:
    setup = ExperimentSetup(
        rationale="Test the evidence-backed proposal.",
        inputs=["sample-a"],
        conditions=["temperature=25C"],
        steps=["measure baseline"],
        parameters=["duration=10m"],
        expected_readouts=["signal"],
    )
    return setup.model_copy(update=updates)


def _result(proposal_hash: str) -> InSilicoResult:
    return InSilicoResult(
        validation_id="validation-1",
        proposal_hash=proposal_hash,
        decision=ValidationDecision.PROCEED,
        predicted_outcome="The signal should remain measurable.",
        confidence=0.8,
        uncertainty="The dry run does not model instrument drift.",
        assumptions=("Input metadata is accurate.",),
        risk_flags=("dry-run-only",),
        recommended_changes=(),
        adapter_name="deterministic-mock",
        adapter_version="1",
        algorithm_version="rules-v1",
        mode=ValidationMode.DRY_RUN,
        completed_at=NOW,
    )


def test_proposal_hash_is_stable_and_content_bound() -> None:
    setup = _setup()

    assert hash_proposal(setup) == hash_proposal(setup.model_copy(deep=True))
    assert hash_proposal(setup) != hash_proposal(_setup(rationale="Changed proposal."))


def test_in_silico_request_rejects_a_mismatched_proposal_hash() -> None:
    with pytest.raises(ValidationError, match="proposal_hash does not match setup"):
        InSilicoRequest(
            request_id="request-1",
            setup_id="setup-1",
            setup=_setup(),
            proposal_hash="0" * 64,
            requested_at=NOW,
        )


def test_validation_result_hash_is_stable_and_metadata_bound() -> None:
    result = _result(hash_proposal(_setup()))

    assert hash_validation_result(result) == hash_validation_result(result.model_copy(deep=True))
    changed = result.model_copy(update={"algorithm_version": "rules-v2"})
    assert hash_validation_result(result) != hash_validation_result(changed)


def test_gate_approval_requires_verified_identity_and_exact_hash_shapes() -> None:
    proposal_hash = hash_proposal(_setup())
    result = _result(proposal_hash)
    identity = IdentityAssertion(
        actor_id="scientist-1",
        role=ApprovalRole.SCIENTIST,
        credential_domain="research.example",
        provider="test-idp",
        verified_at=NOW,
        production_eligible=False,
    )

    approval = GateApproval(
        approval_id="approval-1",
        proposal_hash=proposal_hash,
        validation_result_hash=hash_validation_result(result),
        identity=identity,
        decision=ApprovalDecision.APPROVE,
        rationale="The dry-run risks are acceptable for review.",
        decided_at=NOW,
        validation_adapter=result.adapter_name,
        validation_adapter_version=result.adapter_version,
        validation_algorithm_version=result.algorithm_version,
    )

    assert approval.identity.actor_id == "scientist-1"
    assert approval.identity.authenticated is True
    assert "credential" not in approval.model_dump_json().lower().replace("credential_domain", "")


def test_identity_assertion_rejects_unverified_or_naive_evidence() -> None:
    with pytest.raises(ValidationError):
        IdentityAssertion(
            actor_id="scientist-1",
            role=ApprovalRole.SCIENTIST,
            credential_domain="research.example",
            provider="test-idp",
            verified_at=NOW,
            authenticated=False,
        )

    with pytest.raises(ValidationError):
        IdentityAssertion(
            actor_id="scientist-1",
            role=ApprovalRole.SCIENTIST,
            credential_domain="research.example",
            provider="test-idp",
            verified_at=datetime(2026, 7, 22, 12, 0),
        )
