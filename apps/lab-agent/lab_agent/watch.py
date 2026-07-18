"""Poll the canvus-mcp experiment-workflow snapshot and drive each trigger.

One ``scan_experiment_workflow`` call per poll surfaces the pending forward
triggers (ideas needing a setup, setups needing a robot run) and any detected
loops; this module dispatches each to the orchestrator. Trigger idempotency is
durable, not in-memory: every trigger (idea/setup/loop connector) owns a
``workflow_attempts`` row (see ``lab_agent.state_store``), leased for the
duration of its processing and marked completed/failed/quarantined afterward.

A single-writer canvas lease is acquired/renewed once per cycle; a live foreign owner causes a safe skip (no write), not a crash. A trigger's failure is
persisted durably before the next trigger runs -- one bad emit must never abort the rest of the poll.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable

import structlog

from lab_agent import durable_browser, nodes
from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.models.evidence import GroundingDecision
from lab_agent.orchestrator import generate_setup, run_loop, run_on_robot
from lab_agent.runtime import release_lease_with_audit
from lab_agent.state_store import AttemptStatus, LeaseHeldByOtherError, StateStore

log = structlog.get_logger(__name__)

_ROUND_RE = re.compile(r"v(\d+)")

#: One trigger's unit of work: returns (ok, detail). ``ok=False`` (e.g. a
#: fail-closed schema-validation miss) and a raised exception are both
#: durable failures, handled identically by ``_process_trigger``.
_Work = Callable[[], Awaitable[tuple[bool, str]]]


def _round_of(title: str) -> int:
    m = _ROUND_RE.search(title or "")
    return int(m.group(1)) if m else 1


async def _process_trigger(
    store: StateStore,
    *,
    canvas_id: str,
    trigger_id: str,
    runtime_instance_id: str,
    settings: Settings,
    work: _Work,
) -> bool:
    """Lease ``trigger_id`` if due, run ``work``, and durably record the outcome.

    Returns ``True`` iff due and ``work`` succeeded; a non-due attempt returns
    ``False`` without running ``work``.
    """
    store.ensure_attempt(canvas_id, trigger_id)
    ttl = settings.attempt_lease_ttl_seconds
    attempt = store.lease_due_attempt(canvas_id, trigger_id, lease_owner=runtime_instance_id, lease_ttl_seconds=ttl)
    if attempt is None:
        return False
    store.append_audit_event(canvas_id, "attempt_leased", {"trigger_id": trigger_id})

    try:
        ok, detail = await work()
    except Exception as exc:  # noqa: BLE001 - persist failure, keep polling other triggers
        ok, detail = False, str(exc)[:200]

    if ok:
        store.mark_attempt_completed(canvas_id, trigger_id, lease_owner=runtime_instance_id)
        store.append_audit_event(canvas_id, "attempt_completed", {"trigger_id": trigger_id})
        return True

    updated = store.mark_attempt_failed(
        canvas_id, trigger_id, lease_owner=runtime_instance_id, error=detail,
        base_seconds=settings.retry_base_seconds, max_seconds=settings.retry_max_seconds, max_attempts=settings.max_attempts,
    )
    event = "attempt_quarantined" if updated.status == AttemptStatus.QUARANTINED else "attempt_failed"
    store.append_audit_event(canvas_id, event, {"trigger_id": trigger_id, "error": detail[:200]})
    return False


async def process_once(
    mcp: MCPClient,
    adapter: ModelAdapter,
    settings: Settings,
    store: StateStore,
    runtime_instance_id: str,
    canvas_id: str,
) -> dict[str, int]:
    """Process every due trigger on the canvas exactly once (durable dedup)."""
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

        async def setup_work(idea_id: str = idea_id, idea: dict = idea) -> tuple[bool, str]:
            idea_text = await nodes.read_note_text(mcp, canvas_id, idea_id)
            outcome = await generate_setup(
                mcp, adapter, settings, store, canvas_id=canvas_id, idea_text=idea_text,
                idea_id=idea_id, ragcluster_id=idea.get("ragcluster_id", ""), round_index=1,
            )
            if outcome.decision is GroundingDecision.EXECUTABLE:
                log.info("setup_generated", canvas_id=canvas_id, idea_id=idea_id, setup_id=outcome.setup_id)
                return True, ""
            if outcome.decision is GroundingDecision.NEEDS_INPUT:
                return True, f"needs_input:{outcome.reason}"[:200]
            reason = "invalid_citation" if outcome.decision is GroundingDecision.INVALID_CITATION else "schema_validation_failed"
            return False, reason

        if await _process_trigger(
            store, canvas_id=canvas_id, trigger_id=f"idea_setup:{idea_id}",
            runtime_instance_id=runtime_instance_id, settings=settings, work=setup_work,
        ):
            counts["setups"] += 1

    for s in snap.get("setups_needing_run", []):
        setup_id = s["widget_id"]

        async def run_work(setup_id: str = setup_id, s: dict = s) -> tuple[bool, str]:
            setup_text = await durable_browser.read_stage_text(mcp, store, canvas_id, setup_id)
            _, result = await run_on_robot(
                mcp, adapter, settings, store, canvas_id=canvas_id, setup_id=setup_id,
                setup_text=setup_text, robot_id=s.get("robot_id", ""), round_index=_round_of(s.get("title", "")),
            )
            if result is None:
                return False, "schema_validation_failed"
            log.info("robot_run", canvas_id=canvas_id, setup_id=setup_id)
            return True, ""

        if await _process_trigger(
            store, canvas_id=canvas_id, trigger_id=f"setup_run:{setup_id}",
            runtime_instance_id=runtime_instance_id, settings=settings, work=run_work,
        ):
            counts["runs"] += 1

    for loop in snap.get("loops", []):
        cid = loop.get("loop_connector_id", "")
        if not cid:
            continue

        async def loop_work(loop: dict = loop) -> tuple[bool, str]:
            summary = await run_loop(mcp, adapter, settings, store, canvas_id=canvas_id, loop=loop)
            if summary.stopped_reason in ("schema_validation_failed", "invalid_citation"):
                return False, summary.stopped_reason
            log.info("loop_processed", canvas_id=canvas_id, rounds=summary.rounds, reason=summary.stopped_reason)
            return True, ""

        if await _process_trigger(
            store, canvas_id=canvas_id, trigger_id=f"loop:{cid}",
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
) -> None:
    """Poll forever, processing due triggers each cycle (Ctrl-C to stop)."""
    log.info("watch_start", canvas_id=canvas_id, interval=settings.watch_poll_seconds)
    try:
        while True:
            try:
                await process_once(mcp, adapter, settings, store, runtime_instance_id, canvas_id)
            except Exception as exc:  # noqa: BLE001 - keep watching across cycle-level errors
                log.warning("watch_cycle_error", canvas_id=canvas_id, error=str(exc))
            await asyncio.sleep(settings.watch_poll_seconds)
    finally:
        release_lease_with_audit(store, runtime_instance_id, canvas_id)


__all__ = ["process_once", "run_watch"]
