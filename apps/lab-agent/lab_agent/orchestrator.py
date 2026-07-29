"""Canvas orchestration and terminal-event reconciliation at the loop boundary."""

from __future__ import annotations

from lab_agent import nodes
from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings
from lab_agent.loop_governance import make_stop_tracker, reconcile_terminal_event
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import GovernanceContext
from lab_agent.models.governance import StopReason, TerminalStopEvent
from lab_agent.orchestrator_loop import run_mock_loop
from lab_agent.orchestrator_robot import run_on_robot
from lab_agent.orchestrator_setup import generate_setup
from lab_agent.orchestrator_support import LoopSummary
from lab_agent.state.loop_continuations import LoopContinuationLineageError
from lab_agent.state_store import StateStore


def _existing_terminal(
    store: StateStore, canvas_id: str, *trigger_ids: str,
) -> TerminalStopEvent | None:
    """Return a validated current or legacy closure before any model call."""
    for trigger_id in trigger_ids:
        audit = store.find_terminal_event(canvas_id, trigger_id)
        if audit is None or audit.payload.get("notification_eligible") is not True:
            continue
        payload = audit.payload
        required = ("canvas_id", "trigger_id", "predecessor_id", "reason", "closure_id")
        if not all(isinstance(payload.get(key), str) for key in required):
            continue
        if not isinstance(payload.get("round"), int):
            continue
        try:
            event = TerminalStopEvent(
                canvas_id=payload["canvas_id"], trigger_id=payload["trigger_id"],
                predecessor_id=payload["predecessor_id"], reason=StopReason(payload["reason"]),
                round_index=payload["round"], closure_id=payload["closure_id"],
            )
        except (KeyError, ValueError):
            continue
        if event.canvas_id == canvas_id and event.trigger_id == trigger_id and payload == event.audit_payload:
            return event
    return None


def _round(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 1
    try:
        parsed = int(value or 1)
    except ValueError:
        return 1
    return max(1, parsed)


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
    """Evaluate one round, stage a successor, or reconcile a terminal stop."""
    round_index = _round(loop.get("round"))
    setup_id, result_id = str(loop["setup_id"]), str(loop["result_id"])
    connector_scope = str(loop.get("loop_connector_id") or result_id)
    summary = LoopSummary(
        rounds=max(0, round_index - 1), setup_ids=[setup_id], result_ids=[result_id],
    )
    try:
        continuation = store.resolve_loop_continuation(
            canvas_id, setup_id, result_id, round_index,
        )
    except LoopContinuationLineageError:
        summary.stopped_reason = "schema_validation_failed"
        return summary
    stable_trigger = f"loop-terminal:{continuation.run_id}"
    connector_trigger = f"loop:{connector_scope}:round:{round_index}"
    legacy_trigger = f"loop:{connector_scope}"
    if existing := _existing_terminal(
        store, canvas_id, stable_trigger, connector_trigger, legacy_trigger,
    ):
        summary.terminal_notification_ready = reconcile_terminal_event(store, settings, existing)
        summary.closed_id = existing.closure_id or ""
        summary.stopped_reason = existing.reason.value
        summary.rounds = existing.round_index
        return summary

    idea_id = str(loop.get("idea_id") or "")
    loop_scope = continuation.loop_scope
    tracker = None
    if gov is not None:
        classifications = list(gov.classifications)
        gov.start_run(continuation.run_id, [loop])
        if all(item.value == "unknown" for item in gov.classifications):
            gov.classifications = classifications
        tracker = make_stop_tracker(gov)
        tracker.start_time = continuation.started_at
        tracker.prev_signature = continuation.previous_result_signature
        tracker.streak = continuation.no_progress_streak
        tracker._seen = continuation.observed_round > 0
    idea_text = await nodes.read_note_text(mcp, canvas_id, idea_id) if idea_id else ""
    return await run_mock_loop(
        mcp, adapter, settings, store, summary, canvas_id=canvas_id,
        setup_id=setup_id, result_id=result_id,
        ragcluster_id=str(loop.get("ragcluster_id") or ""), idea_id=idea_id,
        idea_text=idea_text, loop_scope=loop_scope, round_index=round_index,
        gov=gov, tracker=tracker, trigger_id=stable_trigger, continuation=continuation,
    )


__all__ = ["generate_setup", "run_loop", "run_on_robot"]
