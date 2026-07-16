"""StateStore mixin exposing durable Phase 7 evidence operations."""

from __future__ import annotations

import sqlite3

from lab_agent.models.validation import GateApproval, InSilicoResult, hash_validation_result
from lab_agent.state import audit, gate_approvals, validation_evidence
from lab_agent.state.gate_evidence import GateEvidenceProjection
from lab_agent.state.models import Clock


class GateEvidenceStoreMixin:
    """Public facade methods for append-only validation and approval evidence."""

    conn: sqlite3.Connection
    clock: Clock

    def append_validation_result(self, canvas_id: str, result: InSilicoResult) -> InSilicoResult:
        stored, created = validation_evidence.append(self.conn, clock=self.clock, canvas_id=canvas_id, result=result)
        if created:
            audit.append_event(
                self.conn,
                clock=self.clock,
                canvas_id=canvas_id,
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
        return validation_evidence.get(self.conn, canvas_id=canvas_id, validation_id=validation_id)

    def list_validation_results(
        self, canvas_id: str, *, proposal_hash: str | None = None
    ) -> list[InSilicoResult]:
        return validation_evidence.list_results(self.conn, canvas_id=canvas_id, proposal_hash=proposal_hash)

    def append_gate_approval(self, canvas_id: str, approval: GateApproval) -> GateApproval:
        stored, created = gate_approvals.append(self.conn, clock=self.clock, canvas_id=canvas_id, approval=approval)
        if created:
            audit.append_event(
                self.conn,
                clock=self.clock,
                canvas_id=canvas_id,
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
        return gate_approvals.get(self.conn, canvas_id=canvas_id, approval_id=approval_id)

    def list_gate_approvals(
        self,
        canvas_id: str,
        *,
        proposal_hash: str | None = None,
        validation_result_hash: str | None = None,
    ) -> list[GateApproval]:
        return gate_approvals.list_approvals(
            self.conn,
            canvas_id=canvas_id,
            proposal_hash=proposal_hash,
            validation_result_hash=validation_result_hash,
        )

    def load_current_gate_evidence(
        self, canvas_id: str, *, proposal_hash: str, validation_result_hash: str
    ) -> GateEvidenceProjection:
        result = validation_evidence.get_current(
            self.conn,
            canvas_id=canvas_id,
            proposal_hash=proposal_hash,
            result_hash=validation_result_hash,
        )
        if result is None:
            return GateEvidenceProjection(validation_result=None, approvals=())
        approvals = gate_approvals.list_approvals(
            self.conn,
            canvas_id=canvas_id,
            proposal_hash=proposal_hash,
            validation_result_hash=validation_result_hash,
        )
        return GateEvidenceProjection(validation_result=result, approvals=tuple(approvals))


__all__ = ["GateEvidenceStoreMixin"]
