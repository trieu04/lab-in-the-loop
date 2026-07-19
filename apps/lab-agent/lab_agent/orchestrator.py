"""Canvas workflow orchestration and loop backstops."""

from __future__ import annotations

import structlog

from lab_agent import durable_browser, nodes, render
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
    close_governance_error,
    evaluate_and_close,
    make_stop_tracker,
)
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import GovernanceContext, LocalityDeniedError
from lab_agent.models.evidence import GroundingDecision
from lab_agent.models.governance import StopReason, TaskStage
from lab_agent.orchestrator_robot import run_on_robot
from lab_agent.orchestrator_setup import generate_setup
from lab_agent.orchestrator_support import (
    LoopSummary,
    emit_decision,
    emit_result,
    ground_and_emit_setup,
    write_closed_node,
    write_result_node,
    write_setup_node,
)
from lab_agent.policy import BudgetExceededError
from lab_agent.state_store import StateStore

log = structlog.get_logger(__name__)


def _fail_closed(summary: LoopSummary, reason: str = "schema_validation_failed") -> LoopSummary:
    """Mark the loop stopped by a fail-closed condition (already logged/audited)."""
    summary.stopped_reason = reason
    return summary


async def run_loop(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    *,
    canvas_id: str,
    loop: dict,
    gov: GovernanceContext | None = None,
) -> LoopSummary:
    """Run a detected experiment loop until the model (or a backstop) stops it.

    When ``gov`` is supplied the harness stop policy (token/cost/wall-time/
    no-progress/max-rounds) is enforced each round before any further model or
    canvas work; without it the legacy model-decision + round backstop applies.
    """
    round_index = int(loop.get("round") or 1)
    setup_id = loop["setup_id"]
    result_id = loop["result_id"]
    robot_id = loop.get("robot_id", "")
    ragcluster_id = loop.get("ragcluster_id", "")
    idea_id = loop.get("idea_id", "")
    idea_text = await nodes.read_note_text(mcp, canvas_id, idea_id) if idea_id else ""

    summary = LoopSummary(rounds=max(0, round_index - 1), setup_ids=[setup_id], result_ids=[result_id])
    tracker = make_stop_tracker(gov) if gov is not None else None

    while True:
        setup_text = await durable_browser.read_stage_text(mcp, store, canvas_id, setup_id)
        result_text = await durable_browser.read_stage_text(mcp, store, canvas_id, result_id)
        if tracker is not None and gov is not None and await evaluate_and_close(
            mcp, settings, store, tracker, gov, summary, canvas_id=canvas_id,
            result_text=result_text, result_id=result_id, round_index=round_index,
        ):
            return summary
        try:
            decision = await emit_decision(adapter, setup_text, result_text, settings=settings)
        except (BudgetExceededError, LocalityDeniedError) as exc:
            return await close_governance_error(
                mcp, settings, store, summary, canvas_id=canvas_id,
                result_id=result_id, round_index=round_index, error=exc,
            )
        if decision is None:
            return _fail_closed(summary)  # nothing written; canvas left pending, no retry
        summary.rounds += 1
        if tracker is not None and gov is not None and await evaluate_and_close(
            mcp, settings, store, tracker, gov, summary, canvas_id=canvas_id,
            result_text=result_text, result_id=result_id, round_index=round_index, observe=False,
        ):
            return summary
        backstop = gov is None and summary.rounds >= settings.loop_max_rounds

        if not decision.proceed or backstop:
            reason = decision.reason if not decision.proceed else "loop_max_rounds backstop reached"
            summary.closed_id = await write_closed_node(
                mcp, store, settings, canvas_id=canvas_id, decision=decision, reason=reason,
                backstop=backstop, round_index=round_index, result_id=result_id,
                provenance=model_provenance(
                    adapter, settings, TaskStage.LOOP_DECISION, source_widget_id=result_id,
                    trigger_id=f"closed/result:{result_id}/round:{round_index}",
                ),
            )
            summary.stopped_reason = reason
            if gov is not None:
                stop_reason = (
                    StopReason.MODEL_DECISION if not decision.proceed else StopReason.MAX_ROUNDS
                )
                store.append_audit_event(
                    canvas_id,
                    "loop_stopped",
                    {"reason": stop_reason.value, "rounds": summary.rounds},
                    round=round_index,
                )
            log.info("experiment_loop_stopped", canvas_id=canvas_id, rounds=summary.rounds, reason=reason)
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
                mcp, settings, store, summary, canvas_id=canvas_id,
                result_id=result_id, round_index=round_index, error=exc,
            )
        if next_setup is None:
            return _fail_closed(summary)
        if tracker is not None and gov is not None and await evaluate_and_close(
            mcp, settings, store, tracker, gov, summary, canvas_id=canvas_id,
            result_text=result_text, result_id=result_id, round_index=round_index, observe=False,
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
                mcp, settings, store, summary, canvas_id=canvas_id,
                result_id=result_id, round_index=round_index, error=exc,
            )
        if next_result is None:
            return _fail_closed(summary)
        if tracker is not None and gov is not None and await evaluate_and_close(
            mcp, settings, store, tracker, gov, summary, canvas_id=canvas_id,
            result_text=result_text, result_id=result_id, round_index=round_index, observe=False,
        ):
            return summary

        next_setup_id = await write_setup_node(
            mcp, store, settings, canvas_id=canvas_id, setup=next_setup, idea_text=next_idea_text,
            idea_id=idea_id, round_index=round_index, predecessor_id=result_id, edge_kind="result_setup",
            provenance=model_provenance(
                adapter, settings, TaskStage.SETUP, source_widget_id=result_id,
                trigger_id=f"setup/predecessor:{result_id}/round:{round_index}",
            ),
        )
        next_result_id = await write_result_node(
            mcp, store, settings, canvas_id=canvas_id, result=next_result, setup_id=next_setup_id,
            robot_id=robot_id, round_index=round_index,
            provenance=model_provenance(
                adapter, settings, TaskStage.MOCK_RESULT, source_widget_id=next_setup_id,
                trigger_id=f"result/setup:{next_setup_id}/round:{round_index}",
            ),
        )
        summary.setup_ids.append(next_setup_id)
        summary.result_ids.append(next_result_id)
        setup_id, result_id = next_setup_id, next_result_id


__all__ = ["generate_setup", "run_loop", "run_on_robot"]
