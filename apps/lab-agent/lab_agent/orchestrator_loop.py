"""One restart-safe loop decision that may stage only the next Setup."""
from __future__ import annotations

from dataclasses import replace

from lab_agent import durable_browser
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
from lab_agent.models.validation import hash_proposal
from lab_agent.orchestrator_support import (
    LoopSummary,
    emit_decision,
    ground_and_emit_setup,
    write_setup_node,
)
from lab_agent.policy import BudgetExceededError, result_signature
from lab_agent.state.loop_continuations import LoopContinuation
from lab_agent.state_store import StateStore

_STAGED = "successor_staged"

def _fail_closed(summary: LoopSummary, reason: str = "schema_validation_failed") -> LoopSummary:
    summary.stopped_reason = reason
    return summary

def _observe(
    continuation: LoopContinuation,
    tracker: StopTracker | None,
    result_text: str,
    round_index: int,
) -> LoopContinuation:
    if continuation.observed_round >= round_index:
        return continuation
    if tracker is not None:
        tracker.observe_result(result_text)
        signature, streak = tracker.prev_signature, tracker.streak
    else:
        signature = result_signature(result_text)
        streak = (
            continuation.no_progress_streak + 1
            if continuation.observed_round and signature == continuation.previous_result_signature
            else 1
        )
    return replace(
        continuation, observed_round=round_index,
        previous_result_signature=signature, no_progress_streak=streak,
    )

def _sync_tracker(continuation: LoopContinuation, tracker: StopTracker | None) -> None:
    if tracker is None:
        return
    tracker.prev_signature = continuation.previous_result_signature
    tracker.streak = continuation.no_progress_streak
    tracker._seen = continuation.observed_round > 0

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
    ragcluster_id: str,
    idea_id: str,
    idea_text: str,
    loop_scope: str,
    round_index: int,
    gov: GovernanceContext | None,
    tracker: StopTracker | None,
    trigger_id: str,
    continuation: LoopContinuation,
) -> LoopSummary:
    """Evaluate one result and stage only a grounded successor Setup."""
    if continuation.round_index > round_index and continuation.staged_setup_id:
        summary.rounds = round_index
        summary.setup_ids.append(continuation.staged_setup_id)
        summary.stopped_reason = _STAGED
        return summary

    setup_text = await durable_browser.read_stage_text(mcp, store, canvas_id, setup_id)
    result_text = await durable_browser.read_stage_text(mcp, store, canvas_id, result_id)
    continuation = _observe(continuation, tracker, result_text, round_index)
    _sync_tracker(continuation, tracker)
    continuation = store.save_loop_continuation(replace(
        continuation, predecessor_setup_id=setup_id, predecessor_result_id=result_id,
        staged_setup_id="", staged_result_id="", staged_proposal_hash=""))
    if tracker is not None and gov is not None and await evaluate_and_close(
        mcp, settings, store, tracker, gov, summary, canvas_id=canvas_id,
        result_text=result_text, result_id=result_id, round_index=round_index,
        observe=False, trigger_id=trigger_id,
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
            decision=decision, provenance=model_provenance(
                adapter, settings, TaskStage.LOOP_DECISION, source_widget_id=result_id,
                trigger_id=f"closed/result:{result_id}/round:{round_index}",
            ),
        )
        return summary

    next_round = round_index + 1
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
            round_index=next_round, error=exc, trigger_id=trigger_id,
        )
    if next_setup is None:
        return _fail_closed(summary)
    if tracker is not None and gov is not None and await evaluate_and_close(
        mcp, settings, store, tracker, gov, summary, canvas_id=canvas_id,
        result_text=result_text, result_id=result_id, round_index=next_round,
        observe=False, trigger_id=trigger_id,
    ):
        return summary

    verdict = evaluate_grounding(next_setup, ledger, idea_text=next_idea_text)
    record_grounding_audit(store, canvas_id, verdict, ledger)
    if verdict.decision is GroundingDecision.NEEDS_INPUT:
        await write_needs_input_for_verdict(
            mcp, store, settings, canvas_id=canvas_id, verdict=verdict,
            round_index=next_round, predecessor_id=result_id, edge_kind="result_setup",
        )
        summary.stopped_reason = f"needs_input:{verdict.reason}"
        return summary
    if verdict.decision is GroundingDecision.INVALID_CITATION:
        return _fail_closed(summary, "invalid_citation")

    next_setup_id = await write_setup_node(
        mcp, store, settings, canvas_id=canvas_id, setup=next_setup,
        idea_text=next_idea_text, idea_id=idea_id, round_index=next_round,
        predecessor_id=result_id, edge_kind="result_setup", discriminator_scope=loop_scope,
        reuse_artifact_widget_id=setup_id, provenance=model_provenance(
            adapter, settings, TaskStage.SETUP, source_widget_id=result_id,
            trigger_id=f"setup/predecessor:{result_id}/round:{next_round}",
        ),
    )
    continuation = store.save_loop_continuation(replace(
        continuation, round_index=next_round, staged_setup_id=next_setup_id,
        staged_result_id="", staged_proposal_hash=hash_proposal(next_setup),
    ))
    summary.setup_ids.append(next_setup_id)
    summary.stopped_reason = _STAGED
    return summary

__all__ = ["run_mock_loop"]
