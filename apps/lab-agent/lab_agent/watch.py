from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Sequence

import structlog

from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings
from lab_agent.integrations.in_silico import InSilicoAdapter
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import GovernanceContext
from lab_agent.notification_outbox import NotificationOutbox
from lab_agent.observability import EventContext, InProcessMetrics
from lab_agent.orchestrator_approval_status import reconcile_approval_statuses
from lab_agent.runtime import build_notification_outbox, release_lease_with_audit
from lab_agent.state_store import LeaseHeldByOtherError, StateStore
from lab_agent.tenant import CanvasAccessDeniedError, TenantContext
from lab_agent.watch_attempts import failure_code
from lab_agent.watch_execution import process_execution_triggers
from lab_agent.watch_loops import process_loops
from lab_agent.watch_setup import process_setup_triggers
from lab_agent.watch_snapshot import _items

log = structlog.get_logger(__name__)
watch_metrics = InProcessMetrics(max_series=512)
_ROUND_RE = re.compile(r"v(\d+)")


def _require_store_scope(
    store: StateStore, canvas_id: str, *, tenant_context: TenantContext | None = None,
    tenant_id: str | None = None,
) -> None:
    """Reject watch scope that differs from the immutable store scope."""
    if not isinstance(store, StateStore):
        # Test fakes retain their narrow context-only validation contract.
        if tenant_context is not None:
            tenant_context.require_canvas(canvas_id)
        return
    store._require_canvas_scope(canvas_id)
    if tenant_context is not None and store.tenant_context != tenant_context:
        raise CanvasAccessDeniedError("watch tenant context does not match state store")
    if tenant_id is not None and store.tenant_id != tenant_id:
        raise CanvasAccessDeniedError("watch tenant id does not match state store")


def _round_of(title: object) -> int:
    return int(match.group(1)) if isinstance(title, str) and (match := _ROUND_RE.search(title)) else 1

async def process_once(mcp: MCPClient, adapter: ModelAdapter, settings: Settings, store: StateStore, runtime_instance_id: str, canvas_id: str, gov: GovernanceContext | None = None, in_silico_adapter: InSilicoAdapter | None = None, notifications: NotificationOutbox | None = None) -> dict[str, int]:
    _require_store_scope(store, canvas_id)
    counts = {"setups": 0, "runs": 0, "loops": 0, "validations": 0}
    try:
        store.acquire_canvas_lease(canvas_id, runtime_instance_id=runtime_instance_id, ttl_seconds=settings.canvas_lease_ttl_seconds)
    except LeaseHeldByOtherError as exc:
        store.append_audit_event(canvas_id, "canvas_lease_denied", {"reason": str(exc)[:200]})
        log.warning("canvas_lease_denied", canvas_id=canvas_id, error=str(exc))
        return counts
    store.append_audit_event(canvas_id, "canvas_lease_acquired", {"runtime_instance_id": runtime_instance_id})
    try:
        snapshot = json.loads(await mcp.call_tool("scan_experiment_workflow", {"canvas_id": canvas_id}))
    except (json.JSONDecodeError, TypeError):
        snapshot = {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}

    setup_count, validation_count = await process_setup_triggers(
        mcp,
        adapter,
        settings,
        store,
        runtime_instance_id,
        canvas_id,
        snapshot,
        round_of=_round_of,
        gov=gov,
        in_silico_adapter=in_silico_adapter,
    )
    counts["setups"] = setup_count
    counts["validations"] = validation_count

    counts["runs"] += await process_execution_triggers(
        mcp, adapter, settings, store, runtime_instance_id, canvas_id, snapshot, _round_of, gov
    )

    if gov is not None:
        counts["loops"] += await process_loops(mcp, adapter, settings, store, runtime_instance_id, canvas_id, _items(snapshot, "loops"), gov)
    await reconcile_approval_statuses(mcp, settings, store, canvas_id=canvas_id, validation_widget_ids=[str(item["widget_id"]) for item in _items(snapshot, "validations") if item.get("widget_id")])
    release_lease_with_audit(store, runtime_instance_id, canvas_id)
    (notifications or build_notification_outbox(store, settings, runtime_instance_id)).drain()
    return counts


async def run_watch(mcp: MCPClient, adapter: ModelAdapter, settings: Settings, store: StateStore, runtime_instance_id: str, canvas_id: str, gov: GovernanceContext | None = None, in_silico_adapter: InSilicoAdapter | None = None, notifications: NotificationOutbox | None = None) -> None:
    """Poll forever, recovering safe work each cycle (Ctrl-C to stop)."""
    log.info("watch_start", canvas_id=canvas_id, interval=settings.watch_poll_seconds)
    try:
        while True:
            try:
                await process_once(mcp, adapter, settings, store, runtime_instance_id, canvas_id, gov, in_silico_adapter, notifications)
            except Exception as exc:  # noqa: BLE001 - keep watching across cycle-level errors
                error_code, _ = failure_code(exc)
                log.warning("watch_cycle_error", canvas_id=canvas_id, error=error_code)
            await asyncio.sleep(settings.watch_poll_seconds)
    finally:
        release_lease_with_audit(store, runtime_instance_id, canvas_id)


def _validate_canvas_batch(
    store: StateStore,
    canvases: Sequence[str],
    *,
    max_concurrent: int,
    tenant_id: str,
    tenant_context: TenantContext | None,
) -> str:
    if max_concurrent < 1:
        raise ValueError("max_concurrent must be positive")
    if len(canvases) != len(set(canvases)):
        raise ValueError("canvases must not contain duplicates")
    for canvas_id in canvases:
        _require_store_scope(
            store, canvas_id, tenant_context=tenant_context, tenant_id=tenant_id
        )
    return tenant_context.tenant_id if tenant_context is not None else tenant_id


async def _process_canvas_batch(
    mcp: MCPClient,
    adapter_factory: Callable[[str], ModelAdapter],
    settings: Settings,
    store: StateStore,
    runtime_instance_id: str,
    canvases: Sequence[str],
    *,
    governance_factory: Callable[[str], GovernanceContext | None] | None,
    notifications: NotificationOutbox | None,
    max_concurrent: int,
    tenant_id: str,
) -> dict[str, dict[str, int]]:
    results: dict[str, dict[str, int]] = {canvas_id: {} for canvas_id in canvases}
    queue: asyncio.Queue[str] = asyncio.Queue()
    for canvas_id in canvases:
        queue.put_nowait(canvas_id)

    async def worker() -> None:
        while True:
            try:
                canvas_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            context = EventContext(runtime_instance_id, tenant_id, canvas_id, stage="watch")
            with watch_metrics.timer("canvas_cycle_duration", context):
                try:
                    counts = await process_once(
                        mcp,
                        adapter_factory(canvas_id),
                        settings,
                        store,
                        runtime_instance_id,
                        canvas_id,
                        gov=governance_factory(canvas_id) if governance_factory else None,
                        notifications=notifications,
                    )
                    watch_metrics.increment("canvas_cycles", context, status="ok")
                    log.info(
                        "canvas_cycle_complete",
                        tenant_id=tenant_id,
                        canvas_id=canvas_id,
                        status="ok",
                        counts=counts,
                    )
                except Exception as exc:  # noqa: BLE001 - isolate one degraded canvas
                    error_code, _ = failure_code(exc)
                    counts = {"setups": 0, "runs": 0, "loops": 0, "validations": 0}
                    watch_metrics.increment(
                        "canvas_cycles", context, status="degraded", error_class=error_code
                    )
                    log.warning(
                        "canvas_cycle_failed",
                        tenant_id=tenant_id,
                        canvas_id=canvas_id,
                        status="degraded",
                        error_class=error_code,
                    )
                results[canvas_id] = counts

    worker_count = min(max_concurrent, len(canvases))
    await asyncio.gather(*(worker() for _ in range(worker_count)))
    return results


async def process_canvases_once(
    mcp: MCPClient,
    adapter_factory: Callable[[str], ModelAdapter],
    settings: Settings,
    store: StateStore,
    runtime_instance_id: str,
    canvases: Sequence[str],
    *,
    governance_factory: Callable[[str], GovernanceContext | None] | None = None,
    notifications: NotificationOutbox | None = None,
    max_concurrent: int = 1,
    tenant_id: str = "default",
    tenant_context: TenantContext | None = None,
) -> dict[str, dict[str, int]]:
    """Process an allowlisted canvas set with bounded failure isolation."""
    resolved_tenant_id = _validate_canvas_batch(
        store,
        canvases,
        max_concurrent=max_concurrent,
        tenant_id=tenant_id,
        tenant_context=tenant_context,
    )
    return await _process_canvas_batch(
        mcp,
        adapter_factory,
        settings,
        store,
        runtime_instance_id,
        canvases,
        governance_factory=governance_factory,
        notifications=notifications,
        max_concurrent=max_concurrent,
        tenant_id=resolved_tenant_id,
    )


async def run_watch_many(
    mcp: MCPClient,
    adapter_factory: Callable[[str], ModelAdapter],
    settings: Settings,
    store: StateStore,
    runtime_instance_id: str,
    canvases: Sequence[str],
    *,
    governance_factory: Callable[[str], GovernanceContext | None] | None = None,
    notifications: NotificationOutbox | None = None,
    tenant_id: str = "default",
    tenant_context: TenantContext | None = None,
) -> None:
    """Poll all configured canvases until interrupted."""
    if not canvases:
        raise ValueError("at least one canvas is required")
    tenant_id = _validate_canvas_batch(
        store,
        canvases,
        max_concurrent=settings.watch_max_concurrent_canvases,
        tenant_id=tenant_id,
        tenant_context=tenant_context,
    )
    log.info(
        "watch_start",
        tenant_id=tenant_id,
        canvas_count=len(canvases),
        interval=settings.watch_poll_seconds,
        max_concurrent=settings.watch_max_concurrent_canvases,
    )
    try:
        while True:
            await _process_canvas_batch(
                mcp,
                adapter_factory,
                settings,
                store,
                runtime_instance_id,
                canvases,
                governance_factory=governance_factory,
                notifications=notifications,
                max_concurrent=settings.watch_max_concurrent_canvases,
                tenant_id=tenant_id,
            )
            await asyncio.sleep(settings.watch_poll_seconds)
    finally:
        for canvas_id in canvases:
            release_lease_with_audit(store, runtime_instance_id, canvas_id)


__all__ = ["process_canvases_once", "process_once", "run_watch", "run_watch_many"]
