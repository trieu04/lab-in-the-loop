"""Experiment-loop orchestrator (connector-driven, UC Lab-in-the-Loop).

Drives the workflow the background/agent detects on a canvas:
idea → setup → robot → result → (analyse) → stop | next setup …

Design: the model READS to ground itself and emits structured setups/results/
decisions; the orchestrator performs all canvas WRITES. The loop's stop
condition is the model's `LoopDecision` (UC step 3); `loop_max_rounds` is only a
runaway backstop, not the decision.
"""

from __future__ import annotations

import structlog

from lab_agent import nodes, prompts, render
from lab_agent.adapters.base import Message, ModelAdapter
from lab_agent.config import Settings
from lab_agent.loop import emit_structured, run_tool_loop
from lab_agent.mcp_client import MCPClient
from lab_agent.models.experiment import ExperimentResult, ExperimentSetup, LoopDecision
from lab_agent.models.states import DecisionState
from lab_agent.orchestrator_support import LoopSummary, coerce, version
from lab_agent.tool_bridge import select_read_tools

log = structlog.get_logger(__name__)

_ROW = 420.0
_SETUP_X = 0.0
_RESULT_X = 520.0
_CLOSED_X = 260.0


async def generate_setup(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    *,
    canvas_id: str,
    idea_text: str,
    idea_id: str,
    ragcluster_id: str,
    round_index: int,
    prior: str = "",
) -> tuple[str, ExperimentSetup]:
    """Ground on the RagCluster + idea and emit an ExperimentSetup node."""
    read_tools = select_read_tools(await mcp.list_tools())
    hint = f" Knowledge scope RagCluster id: {ragcluster_id}." if ragcluster_id else ""
    user = f"Canvas id: {canvas_id}.{hint}\n\nExperiment idea: {idea_text}"
    if prior:
        user += f"\n\nBuild on the previous round:\n{prior}"
    messages: list[Message] = [
        {"role": "system", "content": prompts.SETUP_SYSTEM},
        {"role": "user", "content": user},
    ]
    await run_tool_loop(adapter, mcp, messages, read_tools, settings.max_tool_steps)
    setup = coerce(
        ExperimentSetup,
        await emit_structured(adapter, messages, ExperimentSetup.model_json_schema(), "ExperimentSetup"),
    )
    ver = version(round_index)
    title = f"{nodes.EXP_SETUP} {ver}] {idea_text[:40]}"
    body = f"Idea: {idea_id}\nRound: {round_index}\n\n{render.render_setup(setup)}"
    setup_id = await nodes.create_node(
        mcp, canvas_id, title, body, _SETUP_X, round_index * _ROW, state=DecisionState.RUNNING
    )
    return setup_id, setup


async def run_on_robot(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    *,
    canvas_id: str,
    setup_id: str,
    setup_text: str,
    robot_id: str,
    round_index: int,
) -> tuple[str, ExperimentResult]:
    """Mock a robot run of the setup and emit an ExperimentResult node."""
    messages: list[Message] = [
        {"role": "system", "content": prompts.RESULT_SYSTEM},
        {"role": "user", "content": f"Experiment setup:\n{setup_text}"},
    ]
    result = coerce(
        ExperimentResult,
        await emit_structured(adapter, messages, ExperimentResult.model_json_schema(), "ExperimentResult"),
    )
    ver = version(round_index)
    title = f"{nodes.EXP_RESULT} {ver}]"
    body = f"Setup: {setup_id}\nRound: {round_index}\n\n{render.render_result(result)}"
    result_id = await nodes.create_node(
        mcp, canvas_id, title, body, _RESULT_X, round_index * _ROW, state=DecisionState.ANALYSIS_COMPLETE
    )
    if robot_id:
        await nodes.connect(mcp, canvas_id, robot_id, result_id)
    return result_id, result


async def _decide(
    adapter: ModelAdapter, setup_text: str, result_text: str
) -> LoopDecision:
    messages: list[Message] = [
        {"role": "system", "content": prompts.DECIDE_SYSTEM},
        {"role": "user", "content": f"Setup:\n{setup_text}\n\nResult:\n{result_text}"},
    ]
    return coerce(
        LoopDecision,
        await emit_structured(adapter, messages, LoopDecision.model_json_schema(), "LoopDecision"),
    )


async def run_loop(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
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
        setup_text = await nodes.read_note_text(mcp, canvas_id, setup_id)
        result_text = await nodes.read_note_text(mcp, canvas_id, result_id)
        decision = await _decide(adapter, setup_text, result_text)
        summary.rounds += 1
        backstop = summary.rounds >= settings.loop_max_rounds

        if not decision.proceed or backstop:
            reason = decision.reason if not decision.proceed else "loop_max_rounds backstop reached"
            closed = await nodes.create_node(
                mcp, canvas_id, f"{nodes.CLOSED} after {version(round_index)}",
                render.render_decision(decision) + (f"\n\n({reason})" if backstop else ""),
                _CLOSED_X, (round_index + 1) * _ROW, state=DecisionState.CLOSED,
            )
            await nodes.connect(mcp, canvas_id, result_id, closed)
            summary.stopped_reason = reason
            summary.closed_id = closed
            log.info("experiment_loop_stopped", canvas_id=canvas_id, rounds=summary.rounds, reason=reason)
            return summary

        round_index += 1
        prior = f"Setup:\n{setup_text}\n\nResult:\n{result_text}\n\nFocus next: {decision.next_focus}"
        next_setup_id, next_setup = await generate_setup(
            mcp, adapter, settings, canvas_id=canvas_id, idea_text=idea_text or decision.next_focus,
            idea_id=idea_id, ragcluster_id=ragcluster_id, round_index=round_index, prior=prior,
        )
        await nodes.connect(mcp, canvas_id, result_id, next_setup_id)
        next_result_id, _ = await run_on_robot(
            mcp, adapter, settings, canvas_id=canvas_id, setup_id=next_setup_id,
            setup_text=render.render_setup(next_setup), robot_id=robot_id, round_index=round_index,
        )
        summary.setup_ids.append(next_setup_id)
        summary.result_ids.append(next_result_id)
        setup_id, result_id = next_setup_id, next_result_id


__all__ = ["generate_setup", "run_loop", "run_on_robot"]
