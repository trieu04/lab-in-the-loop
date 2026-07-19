"""Poll the canvus-mcp experiment-workflow snapshot and drive each trigger.

Each poll scans once, leases due triggers through the durable state store, and
dispatches them to the orchestrator. A foreign canvas lease causes a safe skip;
one failed trigger is persisted without aborting the rest of the poll.
"""

from __future__ import annotations

import asyncio
import json
import re

import structlog

from lab_agent import durable_browser, nodes
from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import GovernanceContext, LocalityDeniedError
from lab_agent.models.evidence import GroundingDecision
from lab_agent.orchestrator import generate_setup, run_loop, run_on_robot
from lab_agent.policy import BudgetExceededError
from lab_agent.runtime import release_lease_with_audit
from lab_agent.state_store import LeaseHeldByOtherError, StateStore
from lab_agent.trigger_governance import close_trigger_governance_error
from lab_agent.watch_attempts import failure_code, process_trigger

log = structlog.get_logger(__name__)

_ROUND_RE = re.compile(r"v(\d+)")

def _round_of(title: str) -> int:
    m = _ROUND_RE.search(title or "")
    return int(m.group(1)) if m else 1


async def process_once(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    runtime_instance_id: str,
    canvas_id: str,
    gov: GovernanceContext | None = None,
) -> dict[str, int]:
    """Process every due trigger on the canvas exactly once (durable dedup).

    When ``gov`` is supplied the loop enforces the harness stop policy; the
    ``adapter`` is expected to be a governed adapter so every provider call is
    locality-checked, budget-accounted, and durably intent-guarded.
    """
    counts = {"setups": 0, "runs": 0, "loops": 0}
    try:
        store.acquire_canvas_lease(
            canvas_id, runtime_instance_id=runtime_instance_id, ttl_seconds=settings.canvas_lease_ttl_seconds
        )
    except LeaseHeldByOtherError as exc:
        store.append_audit_event(canvas_id, "canvas_lease_denied", {"reason": str(exc)[:200]})
        log.warning("canvas_lease_denied", canvas_id=canvas_id, error=str(exc))
        return counts
    store.append_audit_event(canvas_id, "canvas_lease_acquired", {"runtime_instance_id": runtime_instance_id})

    raw = await mcp.call_tool("scan_experiment_workflow", {"canvas_id": canvas_id})
    try:
        snap = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        snap = {}

    for idea in snap.get("ideas_needing_setup", []):
        idea_id = idea["widget_id"]
        trigger_id = f"idea_setup:{idea_id}"

        async def setup_work(
            idea_id: str = idea_id,
            idea: dict = idea,
            trigger_id: str = trigger_id,
        ) -> tuple[bool, str]:
            if gov is not None:
                gov.start_run(trigger_id, [idea])
            idea_text = await nodes.read_note_text(mcp, canvas_id, idea_id)
            try:
                outcome = await generate_setup(
                    mcp, adapter, settings, store, canvas_id=canvas_id, idea_text=idea_text,
                    idea_id=idea_id, ragcluster_id=idea.get("ragcluster_id", ""), round_index=1,
                )
            except (BudgetExceededError, LocalityDeniedError) as exc:
                stop_reason = await close_trigger_governance_error(
                    mcp, settings, store, canvas_id=canvas_id, predecessor_id=idea_id,
                    round_index=1, error=exc,
                )
                return True, stop_reason.value
            if outcome.decision is GroundingDecision.EXECUTABLE:
                log.info("setup_generated", canvas_id=canvas_id, idea_id=idea_id, setup_id=outcome.setup_id)
                return True, ""
            if outcome.decision is GroundingDecision.NEEDS_INPUT:
                return True, f"needs_input:{outcome.reason}"[:200]
            reason = "invalid_citation" if outcome.decision is GroundingDecision.INVALID_CITATION else "schema_validation_failed"
            return False, reason

        if await process_trigger(
            store, canvas_id=canvas_id, trigger_id=trigger_id,
            runtime_instance_id=runtime_instance_id, settings=settings, work=setup_work,
        ):
            counts["setups"] += 1

    for s in snap.get("setups_needing_run", []):
        setup_id = s["widget_id"]
        trigger_id = f"setup_run:{setup_id}"

        async def run_work(
            setup_id: str = setup_id,
            s: dict = s,
            trigger_id: str = trigger_id,
        ) -> tuple[bool, str]:
            if gov is not None:
                gov.start_run(trigger_id, [s])
            setup_text = await durable_browser.read_stage_text(mcp, store, canvas_id, setup_id)
            round_index = _round_of(s.get("title", ""))
            try:
                _, result = await run_on_robot(
                    mcp, adapter, settings, store, canvas_id=canvas_id, setup_id=setup_id,
                    setup_text=setup_text, robot_id=s.get("robot_id", ""), round_index=round_index,
                )
            except (BudgetExceededError, LocalityDeniedError) as exc:
                stop_reason = await close_trigger_governance_error(
                    mcp, settings, store, canvas_id=canvas_id, predecessor_id=setup_id,
                    round_index=round_index, error=exc,
                )
                return True, stop_reason.value
            if result is None:
                return False, "schema_validation_failed"
            log.info("robot_run", canvas_id=canvas_id, setup_id=setup_id)
            return True, ""

        if await process_trigger(
            store, canvas_id=canvas_id, trigger_id=trigger_id,
            runtime_instance_id=runtime_instance_id, settings=settings, work=run_work,
        ):
            counts["runs"] += 1

    for loop in snap.get("loops", []):
        cid = loop.get("loop_connector_id", "")
        if not cid:
            continue
        trigger_id = f"loop:{cid}"

        async def loop_work(
            loop: dict = loop,
            trigger_id: str = trigger_id,
        ) -> tuple[bool, str]:
            if gov is not None:
                gov.start_run(trigger_id, [loop])
            summary = await run_loop(
                mcp, adapter, settings, store, canvas_id=canvas_id, loop=loop, gov=gov
            )
            if summary.stopped_reason in ("schema_validation_failed", "invalid_citation"):
                return False, summary.stopped_reason
            log.info("loop_processed", canvas_id=canvas_id, rounds=summary.rounds, reason=summary.stopped_reason)
            return True, ""

        if await process_trigger(
            store, canvas_id=canvas_id, trigger_id=trigger_id,
            runtime_instance_id=runtime_instance_id, settings=settings, work=loop_work,
        ):
            counts["loops"] += 1

    return counts


async def run_watch(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    runtime_instance_id: str,
    canvas_id: str,
    gov: GovernanceContext | None = None,
) -> None:
    """Poll forever, processing due triggers each cycle (Ctrl-C to stop)."""
    log.info("watch_start", canvas_id=canvas_id, interval=settings.watch_poll_seconds)
    try:
        while True:
            try:
                await process_once(mcp, adapter, settings, store, runtime_instance_id, canvas_id, gov)
            except Exception as exc:  # noqa: BLE001 - keep watching across cycle-level errors
                error_code, _ = failure_code(exc)
                log.warning("watch_cycle_error", canvas_id=canvas_id, error=error_code)
            await asyncio.sleep(settings.watch_poll_seconds)
    finally:
        release_lease_with_audit(store, runtime_instance_id, canvas_id)


__all__ = ["process_once", "run_watch"]
