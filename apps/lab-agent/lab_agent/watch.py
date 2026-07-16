"""Poll the canvus-mcp experiment-workflow snapshot and drive each trigger.

One `scan_experiment_workflow` call per poll surfaces the pending forward
triggers (ideas needing a setup, setups needing a robot run) and any detected
loops; this module dispatches each to the orchestrator. Loop idempotency is
per-session: a detected loop's connector id is processed once.
"""

from __future__ import annotations

import asyncio
import json
import re

import structlog

from lab_agent import nodes
from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.orchestrator import generate_setup, run_loop, run_on_robot

log = structlog.get_logger(__name__)

_ROUND_RE = re.compile(r"v(\d+)")


def _round_of(title: str) -> int:
    m = _ROUND_RE.search(title or "")
    return int(m.group(1)) if m else 1


async def process_once(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    canvas_id: str,
    processed_loops: set[str],
) -> dict[str, int]:
    """Process every pending trigger and loop on the canvas exactly once."""
    raw = await mcp.call_tool("scan_experiment_workflow", {"canvas_id": canvas_id})
    try:
        snap = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        snap = {}

    counts = {"setups": 0, "runs": 0, "loops": 0}

    for idea in snap.get("ideas_needing_setup", []):
        idea_id = idea["widget_id"]
        idea_text = await nodes.read_note_text(mcp, canvas_id, idea_id)
        setup_id, _ = await generate_setup(
            mcp, adapter, settings, canvas_id=canvas_id, idea_text=idea_text,
            idea_id=idea_id, ragcluster_id=idea.get("ragcluster_id", ""), round_index=1,
        )
        await nodes.connect(mcp, canvas_id, idea_id, setup_id)
        counts["setups"] += 1
        log.info("setup_generated", canvas_id=canvas_id, idea_id=idea_id, setup_id=setup_id)

    for s in snap.get("setups_needing_run", []):
        setup_id = s["widget_id"]
        setup_text = await nodes.read_note_text(mcp, canvas_id, setup_id)
        await run_on_robot(
            mcp, adapter, settings, canvas_id=canvas_id, setup_id=setup_id,
            setup_text=setup_text, robot_id=s.get("robot_id", ""), round_index=_round_of(s.get("title", "")),
        )
        counts["runs"] += 1
        log.info("robot_run", canvas_id=canvas_id, setup_id=setup_id)

    for loop in snap.get("loops", []):
        cid = loop.get("loop_connector_id", "")
        if not cid or cid in processed_loops:
            continue
        processed_loops.add(cid)
        summary = await run_loop(mcp, adapter, settings, canvas_id=canvas_id, loop=loop)
        counts["loops"] += 1
        log.info(
            "loop_processed", canvas_id=canvas_id, rounds=summary.rounds, reason=summary.stopped_reason
        )

    return counts


async def run_watch(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    canvas_id: str,
) -> None:
    """Poll forever, processing triggers/loops each cycle (Ctrl-C to stop)."""
    processed_loops: set[str] = set()
    log.info("watch_start", canvas_id=canvas_id, interval=settings.watch_poll_seconds)
    while True:
        try:
            await process_once(mcp, adapter, settings, canvas_id, processed_loops)
        except Exception as exc:  # noqa: BLE001 — keep watching across transient errors
            log.warning("watch_cycle_error", canvas_id=canvas_id, error=str(exc))
        await asyncio.sleep(settings.watch_poll_seconds)


__all__ = ["process_once", "run_watch"]
