"""StateStore facade for tenant-scoped validation and approval evidence."""

from __future__ import annotations

import sqlite3
from typing import Any

from lab_agent.models.validation import GateApproval, InSilicoResult, hash_validation_result
from lab_agent.state import gate_approvals, validation_evidence
from lab_agent.state.gate_evidence import GateEvidenceProjection
from lab_agent.state.models import AuditEvent, Clock


class GateEvidenceStoreMixin:
    """Expose gate evidence through a tenant-bound, canvas-validated facade."""

    conn: sqlite3.Connection
    clock: Clock
    tenant_id: str

    def _require_canvas_scope(self, canvas_id: str) -> None:
        raise NotImplementedError

    def append_audit_event(
        self, canvas_id: str, event: str, payload: dict[str, Any], *, round: int | None = None
    ) -> AuditEvent:
        """Append through the tenant-bound StateStore facade."""
        raise NotImplementedError

    def append_validation_result(self, canvas_id: str, result: InSilicoResult) -> InSilicoResult:
        self._require_canvas_scope(canvas_id)
        stored, created = validation_evidence.append(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            result=result,
        )
        if created:
            self.append_audit_event(
                canvas_id,
                event="in_silico_result_recorded",
                payload={
                    "validation_id": stored.validation_id,
                    "proposal_hash": stored.proposal_hash,
                    "validation_result_hash": hash_validation_result(stored),
                    "decision": stored.decision.value,
                    "adapter": stored.adapter_name,
                    "adapter_version": stored.adapter_version,
                    "algorithm_version": stored.algorithm_version,
                    "mode": stored.mode.value,
                },
            )
        return stored

    def get_validation_result(self, canvas_id: str, validation_id: str) -> InSilicoResult | None:
        self._require_canvas_scope(canvas_id)
        return validation_evidence.get(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, validation_id=validation_id
        )

    def list_validation_results(
        self, canvas_id: str, *, proposal_hash: str | None = None
    ) -> list[InSilicoResult]:
        self._require_canvas_scope(canvas_id)
        return validation_evidence.list_results(
            self.conn,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            proposal_hash=proposal_hash,
        )

    def append_gate_approval(self, canvas_id: str, approval: GateApproval) -> GateApproval:
        self._require_canvas_scope(canvas_id)
        stored, created = gate_approvals.append(
            self.conn,
            clock=self.clock,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            approval=approval,
        )
        if created:
            self.append_audit_event(
                canvas_id,
                event="gate_approval_recorded",
                payload={
                    "approval_id": stored.approval_id,
                    "proposal_hash": stored.proposal_hash,
                    "validation_result_hash": stored.validation_result_hash,
                    "actor_id": stored.identity.actor_id,
                    "role": stored.identity.role.value,
                    "decision": stored.decision.value,
                    "identity_provider": stored.identity.provider,
                    "identity_domain": stored.identity.credential_domain,
                    "production_eligible": stored.identity.production_eligible,
                },
            )
        return stored

    def get_gate_approval(self, canvas_id: str, approval_id: str) -> GateApproval | None:
        self._require_canvas_scope(canvas_id)
        return gate_approvals.get(
            self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, approval_id=approval_id
        )

    def list_gate_approvals(
        self,
        canvas_id: str,
        *,
        proposal_hash: str | None = None,
        validation_result_hash: str | None = None,
    ) -> list[GateApproval]:
        self._require_canvas_scope(canvas_id)
        return gate_approvals.list_approvals(
            self.conn,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            proposal_hash=proposal_hash,
            validation_result_hash=validation_result_hash,
        )

    def load_current_gate_evidence(
        self, canvas_id: str, *, proposal_hash: str, validation_result_hash: str
    ) -> GateEvidenceProjection:
        self._require_canvas_scope(canvas_id)
        result = validation_evidence.get_current(
            self.conn,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            proposal_hash=proposal_hash,
            result_hash=validation_result_hash,
        )
        if result is None:
            return GateEvidenceProjection(
                tenant_id=self.tenant_id,
                canvas_id=canvas_id,
                validation_result=None,
                approvals=(),
            )
        approvals = gate_approvals.list_approvals(
            self.conn,
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            proposal_hash=proposal_hash,
            validation_result_hash=validation_result_hash,
        )
        return GateEvidenceProjection(
            tenant_id=self.tenant_id,
            canvas_id=canvas_id,
            validation_result=result,
            approvals=tuple(approvals),
        )


__all__ = ["GateEvidenceStoreMixin"]
