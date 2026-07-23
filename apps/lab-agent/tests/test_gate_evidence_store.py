"""Restart-safe append-only validation and approval evidence tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from lab_agent.models.validation import (
    ApprovalDecision,
    ApprovalRole,
    GateApproval,
    IdentityAssertion,
    InSilicoResult,
    ValidationDecision,
    ValidationMode,
    hash_validation_result,
)
from lab_agent.state_store import GateEvidenceConflictError, StateStore

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
PROPOSAL_A = "a" * 64
PROPOSAL_B = "b" * 64


def _result(proposal_hash: str, *, validation_id: str = "validation-1") -> InSilicoResult:
    return InSilicoResult(
        validation_id=validation_id,
        proposal_hash=proposal_hash,
        decision=ValidationDecision.PROCEED,
        predicted_outcome="The simulated signal remains measurable.",
        confidence=0.8,
        uncertainty="Instrument drift is outside the dry-run model.",
        assumptions=("Input metadata is accurate.",),
        risk_flags=("dry-run-only",),
        recommended_changes=(),
        adapter_name="deterministic-mock",
        adapter_version="1",
        algorithm_version="rules-v1",
        mode=ValidationMode.DRY_RUN,
        completed_at=NOW,
    )


def _approval(
    result: InSilicoResult,
    *,
    approval_id: str,
    actor_id: str,
    role: ApprovalRole,
    decided_at: datetime = NOW,
) -> GateApproval:
    return GateApproval(
        approval_id=approval_id,
        proposal_hash=result.proposal_hash,
        validation_result_hash=hash_validation_result(result),
        identity=IdentityAssertion(
            actor_id=actor_id,
            role=role,
            credential_domain="research.example",
            provider="test-idp",
            verified_at=decided_at,
            production_eligible=False,
        ),
        decision=ApprovalDecision.APPROVE,
        rationale="The bounded dry-run result is acceptable for the next review gate.",
        decided_at=decided_at,
        validation_adapter=result.adapter_name,
        validation_adapter_version=result.adapter_version,
        validation_algorithm_version=result.algorithm_version,
    )


def test_gate_evidence_survives_restart_and_loads_ordered_current_projection(tmp_path, clock, rng) -> None:
    path = tmp_path / "state.db"
    result = _result(PROPOSAL_A)
    scientist = _approval(result, approval_id="approval-1", actor_id="scientist-1", role=ApprovalRole.SCIENTIST)
    lead = _approval(
        result,
        approval_id="approval-2",
        actor_id="lead-1",
        role=ApprovalRole.LAB_LEAD,
        decided_at=NOW + timedelta(minutes=1),
    )
    first = StateStore(path, clock=clock, rng=rng)
    first.append_validation_result("canvas-1", result)
    first.append_gate_approval("canvas-1", scientist)
    first.append_gate_approval("canvas-1", lead)
    events = first.list_audit_events("canvas-1")
    assert [event.event for event in events] == [
        "in_silico_result_recorded", "gate_approval_recorded", "gate_approval_recorded"
    ]
    payload_text = str([event.payload for event in events])
    assert result.predicted_outcome not in payload_text
    assert scientist.rationale not in payload_text
    first.close()

    restarted = StateStore(path, clock=clock, rng=rng)
    try:
        projection = restarted.load_current_gate_evidence(
            "canvas-1", proposal_hash=PROPOSAL_A, validation_result_hash=hash_validation_result(result)
        )
        assert projection.validation_result == result
        assert projection.approvals == (scientist, lead)
        assert restarted.get_validation_result("canvas-1", result.validation_id) == result
        assert restarted.get_gate_approval("canvas-1", scientist.approval_id) == scientist
    finally:
        restarted.close()


def test_validation_replay_is_idempotent_but_conflicting_id_fails_closed(store) -> None:
    result = _result(PROPOSAL_A)

    assert store.append_validation_result("canvas-1", result) == result
    assert store.append_validation_result("canvas-1", result) == result
    with pytest.raises(GateEvidenceConflictError, match="validation identity"):
        store.append_validation_result("canvas-1", result.model_copy(update={"confidence": 0.9}))

    assert store.list_validation_results("canvas-1") == [result]


def test_durable_evidence_ids_are_scoped_to_the_canvas(store) -> None:
    result = _result(PROPOSAL_A)
    approval = _approval(
        result,
        approval_id="approval-1",
        actor_id="scientist-1",
        role=ApprovalRole.SCIENTIST,
    )

    for canvas_id in ("canvas-1", "canvas-2"):
        store.append_validation_result(canvas_id, result)
        store.append_gate_approval(canvas_id, approval)

    assert store.get_validation_result("canvas-1", result.validation_id) == result
    assert store.get_validation_result("canvas-2", result.validation_id) == result
    assert store.get_gate_approval("canvas-1", approval.approval_id) == approval
    assert store.get_gate_approval("canvas-2", approval.approval_id) == approval


def test_duplicate_approval_requires_an_exact_replay(store) -> None:
    result = _result(PROPOSAL_A)
    approval = _approval(result, approval_id="approval-1", actor_id="scientist-1", role=ApprovalRole.SCIENTIST)
    store.append_validation_result("canvas-1", result)

    assert store.append_gate_approval("canvas-1", approval) == approval
    assert store.append_gate_approval("canvas-1", approval) == approval
    with pytest.raises(GateEvidenceConflictError, match="approval identity"):
        store.append_gate_approval("canvas-1", approval.model_copy(update={"rationale": "Changed rationale."}))
    with pytest.raises(GateEvidenceConflictError, match="duplicate approval"):
        store.append_gate_approval("canvas-1", approval.model_copy(update={"approval_id": "approval-2"}))
    other_actor = _approval(
        result,
        approval_id="approval-other-actor",
        actor_id="scientist-2",
        role=ApprovalRole.SCIENTIST,
    )
    with pytest.raises(GateEvidenceConflictError, match="duplicate approval for role"):
        store.append_gate_approval("canvas-1", other_actor)
    mismatched = approval.model_copy(
        update={"approval_id": "approval-3", "validation_adapter_version": "different"}
    )
    with pytest.raises(GateEvidenceConflictError, match="metadata conflicts"):
        store.append_gate_approval("canvas-1", mismatched)

    assert store.list_gate_approvals("canvas-1") == [approval]


def test_current_projection_marks_hash_mismatches_stale_without_deleting_history(store) -> None:
    old_result = _result(PROPOSAL_A)
    old_approval = _approval(
        old_result, approval_id="approval-1", actor_id="scientist-1", role=ApprovalRole.SCIENTIST
    )
    new_result = _result(PROPOSAL_B, validation_id="validation-2")
    store.append_validation_result("canvas-1", old_result)
    store.append_gate_approval("canvas-1", old_approval)

    stale = store.load_current_gate_evidence(
        "canvas-1", proposal_hash=PROPOSAL_B, validation_result_hash=hash_validation_result(new_result)
    )
    assert stale.validation_result is None
    assert stale.approvals == ()

    store.append_validation_result("canvas-1", new_result)
    current = store.load_current_gate_evidence(
        "canvas-1", proposal_hash=PROPOSAL_B, validation_result_hash=hash_validation_result(new_result)
    )
    assert current.validation_result == new_result
    assert current.approvals == ()
    assert store.list_validation_results("canvas-1") == [old_result, new_result]
    assert store.list_gate_approvals("canvas-1") == [old_approval]


def test_gate_evidence_schema_has_no_secret_or_credential_storage(store) -> None:
    rows = store.conn.execute(
        "SELECT sql FROM sqlite_master WHERE name IN "
        "('in_silico_results', 'gate_approvals', 'idx_in_silico_results_canvas_proposal', "
        "'idx_in_silico_results_current', 'idx_gate_approvals_canvas_evidence')"
    ).fetchall()
    schema = "\n".join(row["sql"] or "" for row in rows).lower()

    assert schema
    for forbidden in ("credential", "secret", "password", "token"):
        assert forbidden not in schema
