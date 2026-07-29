"""Render durable approval projections without accepting Canvas approvals."""

from __future__ import annotations

import re
from collections.abc import Mapping

import structlog

from lab_agent import durable_browser
from lab_agent.approval import TransitionDeniedError, project_gate_state
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.validation import (
    GateApproval,
    InSilicoResult,
    ValidationMode,
    hash_validation_result,
)
from lab_agent.state_store import StateStore

log = structlog.get_logger(__name__)
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _artifact_hashes(payload: Mapping[str, object]) -> tuple[str, str] | None:
    proposal_hash = payload.get("proposal_hash")
    result_hash = payload.get("result_hash")
    if not isinstance(proposal_hash, str) or not isinstance(result_hash, str):
        return None
    if not _HASH.fullmatch(proposal_hash) or not _HASH.fullmatch(result_hash):
        return None
    return proposal_hash, result_hash


def _approval_view(approval: GateApproval) -> dict[str, object]:
    identity = approval.identity
    return {
        "actor_id": identity.actor_id,
        "role": identity.role.value,
        "decision": approval.decision.value,
        "rationale": approval.rationale,
        "decided_at": approval.decided_at.isoformat(),
        "verified_at": identity.verified_at.isoformat(),
        "provider": identity.provider,
        "credential_domain": identity.credential_domain,
        "production_eligible": identity.production_eligible,
    }


def _payload(
    result: InSilicoResult, approvals: tuple[GateApproval, ...], state: str, round_index: int
) -> tuple[str, dict[str, object]]:
    title = "[EXP:Validation] Approval Status"
    mode_label = "DETERMINISTIC DRY RUN — NOT EXECUTABLE" if result.mode is ValidationMode.DRY_RUN else "REAL VALIDATION — EXECUTION DISABLED"
    rendered = "\n".join(
        (
            title,
            f"Validation: {result.validation_id}",
            f"Round: {round_index}",
            f"Projected state: {state}",
            "Execution enabled: false",
            f"Mode: {result.mode.value}",
            mode_label,
        )
    )
    production_eligible = (
        result.mode is ValidationMode.REAL
        and bool(approvals)
        and all(approval.identity.production_eligible for approval in approvals)
    )
    return title, {
        "title": title,
        "round": round_index,
        "proposal_hash": result.proposal_hash,
        "result_hash": hash_validation_result(result),
        "validation_id": result.validation_id,
        "approval_history": [_approval_view(approval) for approval in approvals],
        "approval_status": {
            "state": state,
            "approved_for_wet_lab": state == "APPROVED_FOR_WET_LAB",
            "production_eligible": production_eligible,
        },
        "execution_enabled": False,
        "validation_mode": result.mode.value,
        "mode_label": mode_label,
        durable_browser.RENDERED_TEXT_KEY: rendered,
    }


async def reconcile_approval_statuses(
    mcp: MCPClient,
    settings: Settings,
    store: StateStore,
    *,
    canvas_id: str,
    validation_widget_ids: list[str],
) -> int:
    """Write one status Browser for every canonical in-silico validation Browser.

    Canvas markers and Notes are intentionally absent from this boundary: durable
    evidence is projected only, never created or authenticated here.
    """

    artifacts = ArtifactStore(store.conn, tenant_context=store.tenant_context)
    reconciled = 0
    for widget_id in dict.fromkeys(validation_widget_ids):
        document = artifacts.get_artifact_by_widget(canvas_id=canvas_id, widget_id=widget_id)
        if document is None or document.artifact_type is not ArtifactType.IN_SILICO:
            continue
        hashes = _artifact_hashes(document.payload)
        if hashes is None:
            log.warning("approval_status_invalid_validation_payload", canvas_id=canvas_id, widget_id=widget_id)
            continue
        proposal_hash, result_hash = hashes
        evidence = store.load_current_gate_evidence(
            canvas_id, proposal_hash=proposal_hash, validation_result_hash=result_hash
        )
        if evidence.validation_result is None:
            continue
        try:
            state = project_gate_state(evidence.validation_result, evidence.approvals)
        except TransitionDeniedError as exc:
            log.warning(
                "approval_status_projection_denied",
                canvas_id=canvas_id,
                widget_id=widget_id,
                error_type=type(exc).__name__,
            )
            continue
        title, payload = _payload(
            evidence.validation_result, evidence.approvals, state.value, document.round
        )
        try:
            await durable_browser.write_artifact_browser_durable(
                mcp,
                store,
                settings,
                canvas_id=canvas_id,
                artifact_type=ArtifactType.APPROVAL_STATUS,
                state=state,
                title=title,
                payload=payload,
                provenance=ArtifactProvenance(
                    provider="harness",
                    model_name="approval-status-reconciler",
                    trigger_id=f"approval-status:{evidence.validation_result.validation_id}",
                    source_widget_id=widget_id,
                ),
                discriminator=f"approval-status/validation:{evidence.validation_result.validation_id}",
                round_index=document.round,
                predecessor_id=widget_id,
                edge_kind="validation_approval_status",
            )
        except Exception as exc:  # durable writer records the failed intent for retry
            log.warning(
                "approval_status_reconciliation_failed",
                canvas_id=canvas_id,
                widget_id=widget_id,
                error_type=type(exc).__name__,
            )
            continue
        reconciled += 1
    return reconciled


__all__ = ["reconcile_approval_statuses"]
