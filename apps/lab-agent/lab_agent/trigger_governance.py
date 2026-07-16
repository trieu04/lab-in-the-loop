"""Governance closures for setup and robot triggers before a loop starts."""

from __future__ import annotations

from lab_agent.config import Settings
from lab_agent.loop_governance import governance_stop_reason, stop_decision
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import LocalityDeniedError
from lab_agent.models.artifact import ArtifactProvenance
from lab_agent.models.governance import StopReason
from lab_agent.orchestrator_support import write_closed_node
from lab_agent.policy import BudgetExceededError
from lab_agent.state_store import StateStore


async def close_trigger_with_reason(
    mcp: MCPClient,
    settings: Settings,
    store: StateStore,
    *,
    canvas_id: str,
    predecessor_id: str,
    round_index: int,
    reason: StopReason,
) -> str:
    """Write one deduplicated harness closure for a pre-loop trigger."""
    lineage = f"governance/predecessor:{predecessor_id}/reason:{reason.value}"
    closed_id = await write_closed_node(
        mcp,
        store,
        settings,
        canvas_id=canvas_id,
        decision=stop_decision(reason),
        reason=reason.value,
        backstop=True,
        round_index=round_index,
        predecessor_id=predecessor_id,
        edge_kind="governance_closed",
        discriminator=lineage,
        provenance=ArtifactProvenance(
            provider="harness",
            model_name="governance",
            trigger_id=lineage,
            source_widget_id=predecessor_id,
        ),
    )
    store.append_audit_event(
        canvas_id,
        "governance_stopped",
        {"reason": reason.value, "predecessor_id": predecessor_id},
        round=round_index,
    )
    return closed_id


async def close_trigger_governance_error(
    mcp: MCPClient,
    settings: Settings,
    store: StateStore,
    *,
    canvas_id: str,
    predecessor_id: str,
    round_index: int,
    error: BudgetExceededError | LocalityDeniedError,
) -> StopReason:
    """Close a pre-loop trigger after any locality or budget denial."""
    reason = governance_stop_reason(error)
    await close_trigger_with_reason(
        mcp,
        settings,
        store,
        canvas_id=canvas_id,
        predecessor_id=predecessor_id,
        round_index=round_index,
        reason=reason,
    )
    return reason


__all__ = ["close_trigger_governance_error", "close_trigger_with_reason"]
