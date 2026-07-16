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
from lab_agent.state_store import StateStore


def _existing_terminal(
    store: StateStore, canvas_id: str, trigger_id: str,
) -> TerminalStopEvent | None:
    """Return the validated closure event for this loop before any model call."""
    audit = store.find_terminal_event(canvas_id, trigger_id)
    if audit is None or audit.payload.get("notification_eligible") is not True:
        return None
    payload = audit.payload
    required = ("canvas_id", "trigger_id", "predecessor_id", "reason", "closure_id")
    if not all(isinstance(payload.get(key), str) for key in required):
        return None
    if not isinstance(payload.get("round"), int):
        return None
    try:
        event = TerminalStopEvent(
            canvas_id=payload["canvas_id"], trigger_id=payload["trigger_id"],
            predecessor_id=payload["predecessor_id"], reason=StopReason(payload["reason"]),
            round_index=payload["round"], closure_id=payload["closure_id"],
        )
    except (KeyError, ValueError):
        return None
    if event.canvas_id == canvas_id and event.trigger_id == trigger_id and payload == event.audit_payload:
        return event
    return None


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
    """Run a mock experiment loop until an idempotent terminal condition occurs."""
    round_index = int(loop.get("round") or 1)
    setup_id, result_id = loop["setup_id"], loop["result_id"]
    trigger_id = f"loop:{loop.get('loop_connector_id') or result_id}"
    summary = LoopSummary(rounds=max(0, round_index - 1), setup_ids=[setup_id], result_ids=[result_id])
    if existing := _existing_terminal(store, canvas_id, trigger_id):
        summary.terminal_notification_ready = reconcile_terminal_event(store, settings, existing)
        summary.closed_id = existing.closure_id or ""
        summary.stopped_reason = existing.reason.value
        summary.rounds = existing.round_index
        return summary

    idea_id = loop.get("idea_id", "")
    idea_text = await nodes.read_note_text(mcp, canvas_id, idea_id) if idea_id else ""
    return await run_mock_loop(
        mcp, adapter, settings, store, summary, canvas_id=canvas_id, setup_id=setup_id,
        result_id=result_id, robot_id=loop.get("robot_id", ""),
        ragcluster_id=loop.get("ragcluster_id", ""), idea_id=idea_id, idea_text=idea_text,
        loop_scope=idea_id or loop.get("loop_connector_id") or setup_id, round_index=round_index,
        gov=gov, tracker=make_stop_tracker(gov) if gov is not None else None, trigger_id=trigger_id,
    )


__all__ = ["generate_setup", "run_loop", "run_on_robot"]
