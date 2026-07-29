"""Append-only, tenant-bound human approval evidence."""

from __future__ import annotations

import sqlite3

from lab_agent.models.validation import GateApproval
from lab_agent.state.gate_evidence import (
    GateEvidenceConflictError,
    ValidationEvidenceNotFoundError,
    canonical_evidence_json,
)
from lab_agent.state.models import Clock


def append(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    tenant_id: str,
    canvas_id: str,
    approval: GateApproval,
) -> tuple[GateApproval, bool]:
    """Append one approval only when its validation lineage shares this tenant."""

    tenant = _require_tenant_id(tenant_id)
    _require_scope(tenant, canvas_id, approval)
    payload_json = canonical_evidence_json(approval)
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT approval_json FROM gate_approvals "
            "WHERE tenant_id=? AND canvas_id=? AND approval_id=?",
            (tenant, canvas_id, approval.approval_id),
        ).fetchone()
        if existing is not None:
            if existing["approval_json"] == payload_json:
                conn.execute("COMMIT")
                return approval, False
            raise GateEvidenceConflictError("approval identity has incompatible evidence")
        _require_matching_result(conn, tenant_id=tenant, canvas_id=canvas_id, approval=approval)
        duplicate = conn.execute(
            "SELECT approval_id FROM gate_approvals WHERE tenant_id=? AND canvas_id=? "
            "AND proposal_hash=? AND validation_result_hash=? AND role=?",
            (
                tenant,
                canvas_id,
                approval.proposal_hash,
                approval.validation_result_hash,
                approval.identity.role.value,
            ),
        ).fetchone()
        if duplicate is not None:
            raise GateEvidenceConflictError("duplicate approval for role")
        _insert(
            conn,
            tenant_id=tenant,
            canvas_id=canvas_id,
            approval=approval,
            payload_json=payload_json,
            recorded_at=clock(),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return approval, True


def get(
    conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, approval_id: str
) -> GateApproval | None:
    """Load one approval identity from the specified tenant/canvas."""

    row = conn.execute(
        "SELECT approval_json FROM gate_approvals "
        "WHERE tenant_id=? AND canvas_id=? AND approval_id=?",
        (_require_tenant_id(tenant_id), canvas_id, approval_id),
    ).fetchone()
    return None if row is None else GateApproval.model_validate_json(row["approval_json"])


def list_approvals(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    canvas_id: str,
    proposal_hash: str | None = None,
    validation_result_hash: str | None = None,
) -> list[GateApproval]:
    """List approvals visible only to one tenant/canvas ledger."""

    clauses = ["tenant_id=?", "canvas_id=?"]
    values: list[str] = [_require_tenant_id(tenant_id), canvas_id]
    if proposal_hash is not None:
        clauses.append("proposal_hash=?")
        values.append(proposal_hash)
    if validation_result_hash is not None:
        clauses.append("validation_result_hash=?")
        values.append(validation_result_hash)
    rows = conn.execute(
        "SELECT approval_json FROM gate_approvals WHERE "
        + " AND ".join(clauses)
        + " ORDER BY julianday(decided_at), approval_id",
        values,
    ).fetchall()
    return [GateApproval.model_validate_json(row["approval_json"]) for row in rows]


def _require_matching_result(
    conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, approval: GateApproval
) -> None:
    row = conn.execute(
        "SELECT adapter_name, adapter_version, algorithm_version FROM in_silico_results "
        "WHERE tenant_id=? AND canvas_id=? AND proposal_hash=? AND result_hash=?",
        (tenant_id, canvas_id, approval.proposal_hash, approval.validation_result_hash),
    ).fetchone()
    if row is None:
        raise ValidationEvidenceNotFoundError("approval references no matching validation evidence")
    if (row["adapter_name"], row["adapter_version"], row["algorithm_version"]) != (
        approval.validation_adapter,
        approval.validation_adapter_version,
        approval.validation_algorithm_version,
    ):
        raise GateEvidenceConflictError("approval validation metadata conflicts with validation evidence")


def _insert(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    canvas_id: str,
    approval: GateApproval,
    payload_json: str,
    recorded_at: float,
) -> None:
    identity = approval.identity
    conn.execute(
        "INSERT INTO gate_approvals "
        "(tenant_id, canvas_id, approval_id, proposal_hash, validation_result_hash, actor_id, role, decision, "
        "identity_provider, identity_domain, identity_verified_at, production_eligible, rationale, decided_at, "
        "validation_adapter, validation_adapter_version, validation_algorithm_version, approval_json, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tenant_id,
            canvas_id,
            approval.approval_id,
            approval.proposal_hash,
            approval.validation_result_hash,
            identity.actor_id,
            identity.role.value,
            approval.decision.value,
            identity.provider,
            identity.credential_domain,
            identity.verified_at.isoformat(),
            int(identity.production_eligible),
            approval.rationale,
            approval.decided_at.isoformat(),
            approval.validation_adapter,
            approval.validation_adapter_version,
            approval.validation_algorithm_version,
            payload_json,
            recorded_at,
        ),
    )


def _require_tenant_id(tenant_id: str) -> str:
    if not isinstance(tenant_id, str) or not tenant_id:
        raise ValueError("tenant_id must be non-empty")
    return tenant_id


def _require_scope(tenant_id: str, canvas_id: str, approval: GateApproval) -> None:
    # Compatibility defaults represent historical scope-less approval JSON.
    if (approval.tenant_id, approval.canvas_id) == ("default", "default"):
        return
    if (approval.tenant_id, approval.canvas_id) != (tenant_id, canvas_id):
        raise GateEvidenceConflictError("approval scope does not match persistence scope")


__all__ = ["append", "get", "list_approvals"]
