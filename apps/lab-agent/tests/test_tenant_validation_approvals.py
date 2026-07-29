"""Tenant isolation and legacy migration coverage for gate evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

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
from lab_agent.state import gate_approvals, validation_evidence
from lab_agent.state.connection import connect, migrate
from lab_agent.state.gate_evidence import ValidationEvidenceNotFoundError, canonical_evidence_json
from lab_agent.state_store import StateStore
from lab_agent.tenant import CanvasAccessDeniedError, TenantContext

_CANVAS = "canvas-shared"
_HASH = "a" * 64
_NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)


def _result() -> InSilicoResult:
    return InSilicoResult(
        validation_id="validation-shared",
        proposal_hash=_HASH,
        decision=ValidationDecision.PROCEED,
        predicted_outcome="The deterministic structural check passed.",
        confidence=0.8,
        uncertainty="No scientific simulation was performed.",
        adapter_name="deterministic-mock",
        adapter_version="1",
        algorithm_version="rules-v1",
        mode=ValidationMode.DRY_RUN,
        completed_at=_NOW,
    )


def _approval(result: InSilicoResult) -> GateApproval:
    return GateApproval(
        approval_id="approval-shared",
        proposal_hash=result.proposal_hash,
        validation_result_hash=hash_validation_result(result),
        identity=IdentityAssertion(
            actor_id="scientist-1",
            role=ApprovalRole.SCIENTIST,
            credential_domain="research.example",
            provider="test-idp",
            verified_at=_NOW,
        ),
        decision=ApprovalDecision.APPROVE,
        rationale="The current validation evidence supports review.",
        decided_at=_NOW,
        validation_adapter=result.adapter_name,
        validation_adapter_version=result.adapter_version,
        validation_algorithm_version=result.algorithm_version,
    )


def _store(path: Path, tenant_id: str) -> StateStore:
    return StateStore(
        path,
        tenant_context=TenantContext(tenant_id, (_CANVAS,), "research.example"),
    )


def test_evidence_and_approvals_are_isolated_by_tenant_on_shared_canvas(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first = _store(path, "tenant-a")
    second = _store(path, "tenant-b")
    result = _result()
    approval = _approval(result)

    assert first.append_validation_result(_CANVAS, result) == result
    assert second.append_validation_result(_CANVAS, result) == result
    assert first.append_gate_approval(_CANVAS, approval) == approval
    assert second.append_gate_approval(_CANVAS, approval) == approval

    assert first.get_validation_result(_CANVAS, result.validation_id) == result
    assert second.get_validation_result(_CANVAS, result.validation_id) == result
    assert first.get_gate_approval(_CANVAS, approval.approval_id) == approval
    assert second.get_gate_approval(_CANVAS, approval.approval_id) == approval
    assert first.conn.execute("SELECT COUNT(*) FROM in_silico_results").fetchone()[0] == 2
    assert first.conn.execute("SELECT COUNT(*) FROM gate_approvals").fetchone()[0] == 2
    assert {event.tenant_id for event in first.list_audit_events(_CANVAS)} == {"tenant-a"}
    first.close()
    second.close()


def test_low_level_lineage_requires_evidence_from_the_same_tenant(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first = _store(path, "tenant-a")
    second = _store(path, "tenant-b")
    result = _result()
    approval = _approval(result)

    validation_evidence.append(
        first.conn, clock=first.clock, tenant_id="tenant-a", canvas_id=_CANVAS, result=result
    )
    with pytest.raises(ValidationEvidenceNotFoundError):
        gate_approvals.append(
            second.conn,
            clock=second.clock,
            tenant_id="tenant-b",
            canvas_id=_CANVAS,
            approval=approval,
        )
    with pytest.raises(CanvasAccessDeniedError):
        first.list_validation_results("canvas-denied")
    first.close()
    second.close()


def test_migration_017_preserves_pre_tenant_evidence_as_default(tmp_path: Path) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    package = resources.files("lab_agent.migrations")
    for entry in package.iterdir():
        if entry.name.endswith(".sql") and int(entry.name.split("_", 1)[0]) <= 16:
            (migrations_dir / entry.name).write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")
    path = tmp_path / "legacy.db"
    conn = connect(path)
    migrate(conn, clock=lambda: 1.0, migrations_dir=migrations_dir)
    result = _result()
    approval = _approval(result)
    conn.execute(
        "INSERT INTO in_silico_results "
        "(validation_id, canvas_id, proposal_hash, result_hash, decision, predicted_outcome, confidence, "
        "uncertainty, assumptions_json, risk_flags_json, recommended_changes_json, adapter_name, "
        "adapter_version, algorithm_version, mode, completed_at, result_json, recorded_at, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (result.validation_id, _CANVAS, result.proposal_hash, hash_validation_result(result), result.decision.value,
         result.predicted_outcome, result.confidence, result.uncertainty, "[]", "[]", "[]", result.adapter_name,
         result.adapter_version, result.algorithm_version, result.mode.value, result.completed_at.isoformat(),
         canonical_evidence_json(result), 1.0, "default"),
    )
    conn.execute(
        "INSERT INTO gate_approvals "
        "(approval_id, canvas_id, proposal_hash, validation_result_hash, actor_id, role, decision, "
        "identity_provider, identity_domain, identity_verified_at, production_eligible, rationale, decided_at, "
        "validation_adapter, validation_adapter_version, validation_algorithm_version, approval_json, recorded_at, tenant_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (approval.approval_id, _CANVAS, approval.proposal_hash, approval.validation_result_hash,
         approval.identity.actor_id, approval.identity.role.value, approval.decision.value,
         approval.identity.provider, approval.identity.credential_domain, approval.identity.verified_at.isoformat(),
         0, approval.rationale, approval.decided_at.isoformat(), approval.validation_adapter,
         approval.validation_adapter_version, approval.validation_algorithm_version, canonical_evidence_json(approval),
         1.0, "default"),
    )
    conn.close()

    upgraded = StateStore(path)
    assert upgraded.get_validation_result(_CANVAS, result.validation_id) == result
    assert upgraded.get_gate_approval(_CANVAS, approval.approval_id) == approval
    foreign_keys = upgraded.conn.execute("PRAGMA foreign_key_list('gate_approvals')").fetchall()
    assert any(row["table"] == "in_silico_results" for row in foreign_keys)
    upgraded.close()
