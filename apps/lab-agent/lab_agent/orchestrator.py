"""Experiment-loop orchestrator (connector-driven, UC Lab-in-the-Loop).

Drives the workflow the background/agent detects on a canvas:
idea → setup → robot → result → (analyse) → stop | next setup …

Design: the model READS to ground itself and emits structured setups/results/
decisions; the orchestrator performs all canvas WRITES. The loop's stop
condition is the model's `LoopDecision` (UC step 3); `loop_max_rounds` is only a
runaway backstop, not the decision.

Emit/write stage machinery lives in :mod:`lab_agent.orchestrator_support`; this
module is the high-level flow: two public entry points plus the loop driver.
"""

from __future__ import annotations

import structlog

from lab_agent import durable_browser, nodes, render
from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.models.experiment import ExperimentResult, ExperimentSetup
from lab_agent.orchestrator_support import (
    LoopSummary,
    emit_decision,
    emit_result,
    ground_and_emit_setup,
    write_closed_node,
    write_result_node,
    write_setup_node,
)
from lab_agent.state_store import StateStore

log = structlog.get_logger(__name__)


async def generate_setup(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    *,
    canvas_id: str,
    idea_text: str,
    idea_id: str,
    ragcluster_id: str,
    round_index: int,
    prior: str = "",
) -> tuple[str, ExperimentSetup | None]:
    """Ground on the RagCluster + idea and emit an ExperimentSetup node.

    Fail-closed: a malformed emit writes nothing and returns ``("", None)``; the
    caller must not retry within this cycle.
    """
    setup = await ground_and_emit_setup(
        mcp, adapter, settings, canvas_id=canvas_id, idea_text=idea_text,
        ragcluster_id=ragcluster_id, prior=prior,
    )
    if setup is None:
        return "", None
    setup_id = await write_setup_node(
        mcp, store, settings, canvas_id=canvas_id, setup=setup, idea_text=idea_text, idea_id=idea_id,
        round_index=round_index, predecessor_id=idea_id, edge_kind="idea_setup",
    )
    return setup_id, setup


async def run_on_robot(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    *,
    canvas_id: str,
    setup_id: str,
    setup_text: str,
    robot_id: str,
    round_index: int,
) -> tuple[str, ExperimentResult | None]:
    """Mock a robot run of the setup and emit an ExperimentResult node.

    Fail-closed like :func:`generate_setup`: a malformed emit returns ``("", None)``.
    """
    result = await emit_result(adapter, setup_text)
    if result is None:
        return "", None
    result_id = await write_result_node(
        mcp, store, settings, canvas_id=canvas_id, result=result, setup_id=setup_id, robot_id=robot_id,
        round_index=round_index,
    )
    return result_id, result


def _fail_closed(summary: LoopSummary) -> LoopSummary:
    """Mark the loop stopped by a schema-validation failure (already logged)."""
    summary.stopped_reason = "schema_validation_failed"
    return summary


async def run_loop(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    *,
    canvas_id: str,
    loop: dict,
) -> LoopSummary:
    """Run a detected experiment loop until the model (or backstop) stops it."""
    round_index = int(loop.get("round") or 1)
    setup_id = loop["setup_id"]
    result_id = loop["result_id"]
    robot_id = loop.get("robot_id", "")
    ragcluster_id = loop.get("ragcluster_id", "")
    idea_id = loop.get("idea_id", "")
    idea_text = await nodes.read_note_text(mcp, canvas_id, idea_id) if idea_id else ""

    summary = LoopSummary(setup_ids=[setup_id], result_ids=[result_id])

    while True:
        setup_text = await durable_browser.read_stage_text(mcp, store, canvas_id, setup_id)
        result_text = await durable_browser.read_stage_text(mcp, store, canvas_id, result_id)
        decision = await emit_decision(adapter, setup_text, result_text)
        if decision is None:
            return _fail_closed(summary)  # nothing written; canvas left pending, no retry
        summary.rounds += 1
        backstop = summary.rounds >= settings.loop_max_rounds

        if not decision.proceed or backstop:
            reason = decision.reason if not decision.proceed else "loop_max_rounds backstop reached"
            summary.closed_id = await write_closed_node(
                mcp, store, settings, canvas_id=canvas_id, decision=decision, reason=reason,
                backstop=backstop, round_index=round_index, result_id=result_id,
            )
            summary.stopped_reason = reason
            log.info("experiment_loop_stopped", canvas_id=canvas_id, rounds=summary.rounds, reason=reason)
            return summary

        round_index += 1
        prior = f"Setup:\n{setup_text}\n\nResult:\n{result_text}\n\nFocus next: {decision.next_focus}"
        next_idea_text = idea_text or decision.next_focus

        # Validate BOTH the next round's setup and result before writing either:
        # a result-stage failure must not strand an unpaired setup note that gets
        # rewritten every poll (AC-UC-LITL-02-004 / FR-LITL-019). One attempt per
        # schema this poll; no immediate retry.
        next_setup = await ground_and_emit_setup(
            mcp, adapter, settings, canvas_id=canvas_id, idea_text=next_idea_text,
            ragcluster_id=ragcluster_id, prior=prior,
        )
        if next_setup is None:
            return _fail_closed(summary)
        next_result = await emit_result(adapter, render.render_setup(next_setup))
        if next_result is None:
            return _fail_closed(summary)

        next_setup_id = await write_setup_node(
            mcp, store, settings, canvas_id=canvas_id, setup=next_setup, idea_text=next_idea_text,
            idea_id=idea_id, round_index=round_index, predecessor_id=result_id, edge_kind="result_setup",
        )
        next_result_id = await write_result_node(
            mcp, store, settings, canvas_id=canvas_id, result=next_result, setup_id=next_setup_id,
            robot_id=robot_id, round_index=round_index,
        )
        summary.setup_ids.append(next_setup_id)
        summary.result_ids.append(next_result_id)
        setup_id, result_id = next_setup_id, next_result_id


__all__ = ["generate_setup", "run_loop", "run_on_robot"]
