from __future__ import annotations

import asyncio
import json
import re
from functools import partial
from typing import Literal

import structlog

from lab_agent import durable_browser, nodes
from lab_agent.adapters.base import ModelAdapter
from lab_agent.approval_service import (
    RobotExecutionAuthorizationError,
    has_manual_execution_activation,
    require_current_execution_authorization,
)
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.integrations.in_silico import DeterministicInSilicoAdapter, InSilicoAdapter
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import GovernanceContext, LocalityDeniedError
from lab_agent.models.evidence import GroundingDecision
from lab_agent.models.validation import hash_proposal, hash_validation_result
from lab_agent.notification_outbox import NotificationOutbox
from lab_agent.orchestrator import generate_setup
from lab_agent.orchestrator_approval_status import reconcile_approval_statuses
from lab_agent.orchestrator_needs_input import write_needs_input_node
from lab_agent.orchestrator_robot import run_on_robot
from lab_agent.orchestrator_validation import (
    TypedSetupArtifactError,
    load_typed_setup,
    run_in_silico_validation,
)
from lab_agent.policy import BudgetExceededError
from lab_agent.runtime import build_notification_outbox, release_lease_with_audit
from lab_agent.state_store import LeaseHeldByOtherError, StateStore
from lab_agent.trigger_governance import close_trigger_governance_error
from lab_agent.watch_attempts import failure_code, process_trigger
from lab_agent.watch_loops import process_loops

log = structlog.get_logger(__name__)
_ROUND_RE = re.compile(r"v(\d+)")
ExecutionMode = Literal["manual", "auto"]

def _round_of(title: object) -> int:
    return int(match.group(1)) if isinstance(title, str) and (match := _ROUND_RE.search(title)) else 1

def _items(snapshot: dict[str, object], key: str) -> list[dict[str, object]]:
    value = snapshot.get(key, [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

def _mode(item: dict[str, object]) -> ExecutionMode | None:
    value = item.get("execution_mode", "manual")
    return value if value in ("manual", "auto") else None

async def _write_mode_error(mcp: MCPClient, settings: Settings, store: StateStore, canvas_id: str, item: dict[str, object]) -> tuple[bool, str]:
    widget_id = str(item.get("widget_id", ""))
    error = str(item.get("parse_error", "unsupported_mode"))
    if not widget_id:
        return False, "schema_validation_failed"
    await write_needs_input_node(mcp, store, settings, canvas_id=canvas_id, message="Select manual or auto execution mode.", reason=f"execution_mode:{error}", context={"widget_id": widget_id, "error": error}, round_index=1, predecessor_id=widget_id, edge_kind="execution_mode_error")
    return True, ""

async def _close_governance_error(mcp: MCPClient, settings: Settings, store: StateStore, canvas_id: str, predecessor_id: str, round_index: int, error: BudgetExceededError | LocalityDeniedError) -> tuple[bool, str]:
    reason = await close_trigger_governance_error(mcp, settings, store, canvas_id=canvas_id, predecessor_id=predecessor_id, round_index=round_index, error=error)
    return True, reason.value

async def _generate_setup(mcp: MCPClient, adapter: ModelAdapter, settings: Settings, store: StateStore, canvas_id: str, idea: dict[str, object], idea_id: str, gov: GovernanceContext | None) -> tuple[bool, str]:
    trigger_id = f"idea_setup:{idea_id}"
    if gov is not None:
        gov.start_run(trigger_id, [idea])
    try:
        outcome = await generate_setup(mcp, adapter, settings, store, canvas_id=canvas_id, idea_text=await nodes.read_note_text(mcp, canvas_id, idea_id), idea_id=idea_id, ragcluster_id=str(idea.get("ragcluster_id", "")), round_index=1)
    except (BudgetExceededError, LocalityDeniedError) as exc:
        return await _close_governance_error(mcp, settings, store, canvas_id, idea_id, 1, exc)
    if outcome.decision is GroundingDecision.EXECUTABLE:
        return True, ""
    if outcome.decision is GroundingDecision.NEEDS_INPUT:
        return True, f"needs_input:{outcome.reason}"[:200]
    reason = "invalid_citation" if outcome.decision is GroundingDecision.INVALID_CITATION else "schema_validation_failed"
    return False, reason

async def _validate_setup(mcp: MCPClient, settings: Settings, store: StateStore, validator: InSilicoAdapter, canvas_id: str, setup_id: str, round_index: int) -> tuple[bool, str]:
    await run_in_silico_validation(mcp, settings, store, validator, canvas_id, setup_id, round_index)
    return True, ""

async def _run_setup(mcp: MCPClient, adapter: ModelAdapter, settings: Settings, store: StateStore, canvas_id: str, setup: dict[str, object], setup_id: str, robot_id: str, round_index: int, proposal_hash: str, phase: str, authorized: bool, manual_activation_required: bool, gov: GovernanceContext | None) -> tuple[bool, str]:
    if phase == "waiting":
        if authorized and manual_activation_required:
            await write_needs_input_node(mcp, store, settings, canvas_id=canvas_id, message="Activate this approved setup to begin the manual run.", reason="manual_execution_activation_required", context={"setup_id": setup_id, "proposal_hash": proposal_hash}, round_index=round_index, predecessor_id=setup_id, edge_kind="manual_execution_activation")
        return True, ""
    if gov is not None:
        gov.start_run(f"setup_run:{setup_id}:{proposal_hash}", [setup])
    try:
        result_id, result = await run_on_robot(mcp, adapter, settings, store, canvas_id=canvas_id, setup_id=setup_id, setup_text=await durable_browser.read_stage_text(mcp, store, canvas_id, setup_id), robot_id=robot_id, round_index=round_index, execution_mode="manual" if manual_activation_required else "auto")
    except (BudgetExceededError, LocalityDeniedError) as exc:
        return await _close_governance_error(mcp, settings, store, canvas_id, setup_id, round_index, exc)
    return (bool(result_id and result), "" if result else "schema_validation_failed")

async def process_once(mcp: MCPClient, adapter: ModelAdapter, settings: Settings, store: StateStore, runtime_instance_id: str, canvas_id: str, gov: GovernanceContext | None = None, in_silico_adapter: InSilicoAdapter | None = None, notifications: NotificationOutbox | None = None) -> dict[str, int]:
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

    for item in _items(snapshot, "mode_errors"):
        widget_id = str(item.get("widget_id", ""))
        error = str(item.get("parse_error", "unsupported_mode"))
        await process_trigger(store, canvas_id=canvas_id, trigger_id=f"mode_error:{widget_id}:{error}", runtime_instance_id=runtime_instance_id, settings=settings, work=partial(_write_mode_error, mcp, settings, store, canvas_id, item))
    for idea in _items(snapshot, "ideas_needing_setup"):
        idea_id = str(idea.get("widget_id", ""))
        if not idea_id:
            continue
        if _mode(idea) is None:
            await process_trigger(store, canvas_id=canvas_id, trigger_id=f"mode_error:{idea_id}:unsupported_mode", runtime_instance_id=runtime_instance_id, settings=settings, work=partial(_write_mode_error, mcp, settings, store, canvas_id, idea))
            continue
        completed = await process_trigger(store, canvas_id=canvas_id, trigger_id=f"idea_setup:{idea_id}", runtime_instance_id=runtime_instance_id, settings=settings, work=partial(_generate_setup, mcp, adapter, settings, store, canvas_id, idea, idea_id, gov))
        if completed:
            counts["setups"] += 1

    validator = in_silico_adapter or DeterministicInSilicoAdapter()
    validation_key = "setups_needing_validation" if "setups_needing_validation" in snapshot else "setups"
    validation_items = _items(snapshot, validation_key)
    for setup in validation_items:
        setup_id = str(setup.get("widget_id", ""))
        try:
            proposal_hash = hash_proposal(load_typed_setup(store, canvas_id, setup_id))
        except TypedSetupArtifactError:
            continue
        completed = await process_trigger(store, canvas_id=canvas_id, trigger_id=f"in_silico:{setup_id}:{proposal_hash}", runtime_instance_id=runtime_instance_id, settings=settings, work=partial(_validate_setup, mcp, settings, store, validator, canvas_id, setup_id, _round_of(setup.get("title"))))
        if completed:
            counts["validations"] += 1

    modes = {str(item["widget_id"]): mode for item in _items(snapshot, "ideas") if item.get("widget_id") and (mode := _mode(item)) is not None}
    invalid_mode_ids = {str(item["widget_id"]) for item in _items(snapshot, "mode_errors") if item.get("widget_id")}
    invalid_mode_ids.update(str(item["widget_id"]) for item in _items(snapshot, "ideas") if item.get("widget_id") and "execution_mode" in item and _mode(item) is None)
    execution_items = _items(snapshot, "setups_needing_run") if settings.wet_lab_execution_enabled else []
    for setup in execution_items:
        setup_id = str(setup.get("widget_id", ""))
        if not setup_id:
            continue
        round_index = _round_of(setup.get("title"))
        try:
            proposal_hash = hash_proposal(load_typed_setup(store, canvas_id, setup_id))
        except TypedSetupArtifactError:
            continue
        results = store.list_validation_results(canvas_id, proposal_hash=proposal_hash)
        if len(results) != 1:
            continue
        result_hash = hash_validation_result(results[0])
        direct_mode = _mode(setup)
        if "execution_mode" in setup and direct_mode is None:
            continue
        document = ArtifactStore(store.conn).get_artifact_by_widget(canvas_id=canvas_id, widget_id=setup_id)
        source_id = document.provenance.source_widget_id if document else ""
        if source_id in invalid_mode_ids:
            continue
        mode = direct_mode if "execution_mode" in setup else modes.get(source_id, "manual")
        try:
            require_current_execution_authorization(store, canvas_id, proposal_hash, result_hash)
            authorized = True
        except RobotExecutionAuthorizationError:
            authorized = False
        manual_activation_required = mode == "manual" and round_index == 1
        activated = has_manual_execution_activation(store, canvas_id, setup_id, proposal_hash, result_hash)
        phase = "approved" if authorized and (not manual_activation_required or activated) else "waiting"
        completed = await process_trigger(store, canvas_id=canvas_id, trigger_id=f"setup_run:{setup_id}:{proposal_hash}:{result_hash}:{mode}:{phase}", runtime_instance_id=runtime_instance_id, settings=settings, work=partial(_run_setup, mcp, adapter, settings, store, canvas_id, setup, setup_id, str(setup.get("robot_id", "")), round_index, proposal_hash, phase, authorized, manual_activation_required, gov))
        if completed and phase == "approved":
            counts["runs"] += 1

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
__all__ = ["process_once", "run_watch"]
