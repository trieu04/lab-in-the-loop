"""Multi-round mock-loop continuation after terminal-event reconciliation.

This module deliberately emits only synthetic model results. It never imports or
calls the wet-lab robot execution boundary.
"""

from __future__ import annotations

from lab_agent import durable_browser, render
from lab_agent.adapters.base import ModelAdapter
from lab_agent.artifact_provenance import model_provenance
from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.grounding import (
    evaluate_grounding,
    record_grounding_audit,
    write_needs_input_for_verdict,
)
from lab_agent.loop_governance import (
    StopTracker,
    close_governance_error,
    close_with_reason,
    evaluate_and_close,
)
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import GovernanceContext, LocalityDeniedError
from lab_agent.models.evidence import GroundingDecision
from lab_agent.models.governance import StopReason, TaskStage
from lab_agent.orchestrator_support import (
    LoopSummary,
    emit_decision,
    emit_result,
    ground_and_emit_setup,
    write_result_node,
    write_setup_node,
)
from lab_agent.policy import BudgetExceededError
from lab_agent.state_store import StateStore


def _fail_closed(summary: LoopSummary, reason: str = "schema_validation_failed") -> LoopSummary:
    summary.stopped_reason = reason
    return summary


async def run_mock_loop(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    summary: LoopSummary,
    *,
    canvas_id: str,
    setup_id: str,
    result_id: str,
    robot_id: str,
    ragcluster_id: str,
    idea_id: str,
    idea_text: str,
    loop_scope: str,
    round_index: int,
    gov: GovernanceContext | None,
    tracker: StopTracker | None,
    trigger_id: str,
) -> LoopSummary:
    """Continue through grounded setup and synthetic-result rounds until a stop."""
    while True:
        setup_text = await durable_browser.read_stage_text(mcp, store, canvas_id, setup_id)
        result_text = await durable_browser.read_stage_text(mcp, store, canvas_id, result_id)
        if tracker is not None and gov is not None and await evaluate_and_close(
            mcp, settings, store, tracker, gov, summary, canvas_id=canvas_id,
            result_text=result_text, result_id=result_id, round_index=round_index, trigger_id=trigger_id,
        ):
            return summary
        try:
            decision = await emit_decision(adapter, setup_text, result_text, settings=settings)
        except (BudgetExceededError, LocalityDeniedError) as exc:
            return await close_governance_error(
                mcp, settings, store, summary, canvas_id=canvas_id, result_id=result_id,
                round_index=round_index, error=exc, trigger_id=trigger_id,
            )
        if decision is None:
            return _fail_closed(summary)

        summary.rounds += 1
        if tracker is not None and gov is not None and await evaluate_and_close(
            mcp, settings, store, tracker, gov, summary, canvas_id=canvas_id,
            result_text=result_text, result_id=result_id, round_index=round_index,
            observe=False, trigger_id=trigger_id,
        ):
            return summary
        if gov is None and summary.rounds >= settings.loop_max_rounds:
            await close_with_reason(
                mcp, settings, store, summary, canvas_id=canvas_id, result_id=result_id,
                round_index=round_index, reason=StopReason.MAX_ROUNDS, trigger_id=trigger_id,
            )
            return summary
        if not decision.proceed and summary.rounds >= settings.loop_min_rounds:
            await close_with_reason(
                mcp, settings, store, summary, canvas_id=canvas_id, result_id=result_id,
                round_index=round_index, reason=StopReason.MODEL_DECISION, trigger_id=trigger_id,
                decision=decision,
                provenance=model_provenance(
                    adapter, settings, TaskStage.LOOP_DECISION, source_widget_id=result_id,
                    trigger_id=f"closed/result:{result_id}/round:{round_index}",
                ),
            )
            return summary

        round_index += 1
        prior = f"Setup:\n{setup_text}\n\nResult:\n{result_text}\n\nFocus next: {decision.next_focus}"
        next_idea_text = idea_text or decision.next_focus
        ledger = EvidenceLedger(settings.model_evidence_max_items, settings.model_evidence_max_bytes)
        try:
            next_setup = await ground_and_emit_setup(
                mcp, adapter, settings, canvas_id=canvas_id, idea_text=next_idea_text,
                ragcluster_id=ragcluster_id, prior=prior, ledger=ledger,
            )
        except (BudgetExceededError, LocalityDeniedError) as exc:
            return await close_governance_error(
                mcp, settings, store, summary, canvas_id=canvas_id, result_id=result_id,
                round_index=round_index, error=exc, trigger_id=trigger_id,
            )
        if next_setup is None:
            return _fail_closed(summary)
        if tracker is not None and gov is not None and await evaluate_and_close(
            mcp, settings, store, tracker, gov, summary, canvas_id=canvas_id,
            result_text=result_text, result_id=result_id, round_index=round_index,
            observe=False, trigger_id=trigger_id,
        ):
            return summary

        verdict = evaluate_grounding(next_setup, ledger, idea_text=next_idea_text)
        record_grounding_audit(store, canvas_id, verdict, ledger)
        if verdict.decision is GroundingDecision.NEEDS_INPUT:
            await write_needs_input_for_verdict(
                mcp, store, settings, canvas_id=canvas_id, verdict=verdict, round_index=round_index,
                predecessor_id=result_id, edge_kind="result_setup",
            )
            summary.stopped_reason = f"needs_input:{verdict.reason}"
            return summary
        if verdict.decision is GroundingDecision.INVALID_CITATION:
            return _fail_closed(summary, "invalid_citation")

        try:
            next_result = await emit_result(adapter, render.render_setup(next_setup), settings=settings)
        except (BudgetExceededError, LocalityDeniedError) as exc:
            return await close_governance_error(
                mcp, settings, store, summary, canvas_id=canvas_id, result_id=result_id,
                round_index=round_index, error=exc, trigger_id=trigger_id,
            )
        if next_result is None:
            return _fail_closed(summary)
        if tracker is not None and gov is not None and await evaluate_and_close(
            mcp, settings, store, tracker, gov, summary, canvas_id=canvas_id,
            result_text=result_text, result_id=result_id, round_index=round_index,
            observe=False, trigger_id=trigger_id,
        ):
            return summary

        next_setup_id = await write_setup_node(
            mcp, store, settings, canvas_id=canvas_id, setup=next_setup, idea_text=next_idea_text,
            idea_id=idea_id, round_index=round_index, predecessor_id=result_id, edge_kind="result_setup",
            discriminator_scope=loop_scope, reuse_artifact_widget_id=setup_id,
            provenance=model_provenance(
                adapter, settings, TaskStage.SETUP, source_widget_id=result_id,
                trigger_id=f"setup/predecessor:{result_id}/round:{round_index}",
            ),
        )
        next_result_id = await write_result_node(
            mcp, store, settings, canvas_id=canvas_id, result=next_result, setup_id=next_setup_id,
            robot_id=robot_id, round_index=round_index, reuse_artifact_widget_id=result_id,
            provenance=model_provenance(
                adapter, settings, TaskStage.MOCK_RESULT, source_widget_id=next_setup_id,
                trigger_id=f"result/setup:{next_setup_id}/round:{round_index}",
            ),
        )
        summary.setup_ids.append(next_setup_id)
        summary.result_ids.append(next_result_id)
        setup_id, result_id = next_setup_id, next_result_id


__all__ = ["run_mock_loop"]
