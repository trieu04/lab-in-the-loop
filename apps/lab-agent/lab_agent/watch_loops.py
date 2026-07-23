"""Durable watcher dispatch for loop snapshots."""

from __future__ import annotations

from functools import partial

from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import GovernanceContext
from lab_agent.orchestrator import run_loop
from lab_agent.state_store import StateStore
from lab_agent.watch_attempts import process_trigger


def _ids(loop: dict[str, object]) -> tuple[str, str, str] | None:
    setup_id, result_id = loop.get("setup_id"), loop.get("result_id")
    connector_id = loop.get("loop_connector_id")
    if not isinstance(setup_id, str) or not setup_id or not isinstance(result_id, str) or not result_id:
        return None
    scope = connector_id if isinstance(connector_id, str) and connector_id else result_id
    return setup_id, result_id, scope


async def _run_loop(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    canvas_id: str,
    loop: dict[str, object],
    gov: GovernanceContext | None,
) -> tuple[bool, str]:
    identifiers = _ids(loop)
    if identifiers is None:
        return False, "schema_validation_failed"
    _, _, scope = identifiers
    trigger_id = f"loop:{scope}"
    if gov is not None:
        gov.start_run(trigger_id, [loop])
    summary = await run_loop(mcp, adapter, settings, store, canvas_id=canvas_id, loop=loop, gov=gov)
    if not summary.closed_id:
        return False, (summary.stopped_reason or "schema_validation_failed")[:200]
    completed = summary.terminal_notification_ready
    detail = summary.stopped_reason if completed else "notification_enqueue_failed"
    return completed, detail[:200]


async def process_loops(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    runtime_instance_id: str,
    canvas_id: str,
    loops: list[dict[str, object]],
    gov: GovernanceContext | None,
) -> int:
    """Run each valid loop through the fenced trigger lifecycle once."""
    completed = 0
    for loop in loops:
        identifiers = _ids(loop)
        if identifiers is None:
            continue
        _, _, scope = identifiers
        if await process_trigger(
            store,
            canvas_id=canvas_id,
            trigger_id=f"loop:{scope}",
            runtime_instance_id=runtime_instance_id,
            settings=settings,
            work=partial(_run_loop, mcp, adapter, settings, store, canvas_id, loop, gov),
        ):
            completed += 1
    return completed


__all__ = ["process_loops"]
