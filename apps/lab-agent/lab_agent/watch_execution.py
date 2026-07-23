"""Watcher handoff for Phase 8 and preserved legacy mock execution."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from typing import Literal

from lab_agent import durable_browser
from lab_agent.adapters.base import ModelAdapter
from lab_agent.approval_service import (
    RobotExecutionAuthorizationError,
    has_manual_execution_activation,
    require_current_execution_authorization,
)
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.execution_orchestrator import Phase8Orchestrator
from lab_agent.mcp_client import MCPClient
from lab_agent.model_gateway import GovernanceContext, LocalityDeniedError
from lab_agent.models.validation import hash_proposal, hash_validation_result
from lab_agent.orchestrator_robot import run_on_robot
from lab_agent.orchestrator_validation import TypedSetupArtifactError, load_typed_setup
from lab_agent.policy import BudgetExceededError
from lab_agent.runtime import build_phase8_orchestrator
from lab_agent.state_store import StateStore
from lab_agent.trigger_governance import close_trigger_governance_error
from lab_agent.watch_attempts import process_trigger

ExecutionMode = Literal["manual", "auto"]


def _items(snapshot: dict[str, object], key: str) -> list[dict[str, object]]:
    value = snapshot.get(key, [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _mode(item: dict[str, object]) -> ExecutionMode | None:
    value = item.get("execution_mode", "manual")
    return value if value in ("manual", "auto") else None


def _mode_context(snapshot: dict[str, object]) -> tuple[dict[str, ExecutionMode], set[str]]:
    ideas = _items(snapshot, "ideas")
    modes = {str(item["widget_id"]): mode for item in ideas if item.get("widget_id") and (mode := _mode(item)) is not None}
    invalid = {str(item["widget_id"]) for item in _items(snapshot, "mode_errors") if item.get("widget_id")}
    invalid.update(str(item["widget_id"]) for item in ideas if item.get("widget_id") and "execution_mode" in item and _mode(item) is None)
    return modes, invalid


async def _run_legacy(
    mcp: MCPClient, adapter: ModelAdapter, settings: Settings, store: StateStore, canvas_id: str,
    setup: dict[str, object], setup_id: str, robot_id: str, round_index: int,
    proposal_hash: str, phase: str, authorized: bool, manual_activation_required: bool,
    gov: GovernanceContext | None,
) -> tuple[bool, str]:
    if phase == "waiting":
        if authorized and manual_activation_required:
            from lab_agent.orchestrator_needs_input import write_needs_input_node
            await write_needs_input_node(mcp, store, settings, canvas_id=canvas_id, message="Activate this approved setup to begin the manual run.", reason="manual_execution_activation_required", context={"setup_id": setup_id}, round_index=round_index, predecessor_id=setup_id, edge_kind="manual_execution_activation")
        return True, ""
    if gov is not None:
        gov.start_run(f"setup_run:{setup_id}", [setup])
    try:
        result_id, result = await run_on_robot(
            mcp, adapter, settings, store, canvas_id=canvas_id, setup_id=setup_id,
            setup_text=await durable_browser.read_stage_text(mcp, store, canvas_id, setup_id),
            robot_id=robot_id, round_index=round_index,
            execution_mode="manual" if manual_activation_required else "auto",
            stale_result_id=str(setup.get("stale_result_id", "")),
        )
    except (BudgetExceededError, LocalityDeniedError) as exc:
        reason, notification_ready = await close_trigger_governance_error(
            mcp, settings, store, canvas_id=canvas_id, predecessor_id=setup_id,
            round_index=round_index, error=exc, trigger_id=f"setup_run:{setup_id}",
        )
        return notification_ready, reason.value if notification_ready else "notification_enqueue_failed"
    if result_id and result is not None:
        store.record_loop_result(
            canvas_id, setup_id, round_index, result_id, proposal_hash,
        )
    return bool(result_id and result), "" if result else "schema_validation_failed"


async def process_execution_triggers(
    mcp: MCPClient, adapter: ModelAdapter, settings: Settings, store: StateStore,
    runtime_instance_id: str, canvas_id: str, snapshot: dict[str, object],
    round_of: Callable[[object], int], gov: GovernanceContext | None,
) -> int:
    """Use the opt-in Phase 8 facade or the unchanged legacy mock branch."""

    setups = _items(snapshot, "setups_needing_run")
    if settings.phase8_execution_enabled:
        lifecycle = build_phase8_orchestrator(store, settings, mcp=mcp)
        await lifecycle.reconcile_canvas(canvas_id)
        modes, invalid = _mode_context(snapshot)
        completed = 0
        for setup in setups:
            setup_id, direct_mode = str(setup.get("widget_id", "")), _mode(setup)
            if not setup_id or ("execution_mode" in setup and direct_mode is None):
                continue
            source = ArtifactStore(store.conn).get_artifact_by_widget(
                canvas_id=canvas_id, widget_id=setup_id
            )
            source_id = source.provenance.source_widget_id if source else ""
            if source_id in invalid:
                continue
            mode = direct_mode if "execution_mode" in setup else modes.get(source_id, "manual")
            round_index = round_of(setup.get("title"))
            try:
                proposal_hash = hash_proposal(load_typed_setup(store, canvas_id, setup_id))
                results = store.list_validation_results(canvas_id, proposal_hash=proposal_hash)
                if len(results) != 1:
                    continue
                result_hash = hash_validation_result(results[0])
                require_current_execution_authorization(store, canvas_id, proposal_hash, result_hash)
            except (RobotExecutionAuthorizationError, TypedSetupArtifactError):
                continue
            manual = mode == "manual" and round_index == 1
            activated = has_manual_execution_activation(store, canvas_id, setup_id, proposal_hash, result_hash)
            phase = "approved" if not manual or activated else "waiting"
            if phase == "approved":
                work = partial(_run_phase8, lifecycle, canvas_id, setup_id, round_index)
            else:
                work = partial(
                    _run_legacy, mcp, adapter, settings, store, canvas_id, setup, setup_id,
                    str(setup.get("robot_id", "")), round_index, proposal_hash,
                    phase, True, manual, gov,
                )
            did_run = await process_trigger(
                store, canvas_id=canvas_id, trigger_id=f"phase8:{setup_id}:{proposal_hash}:{result_hash}:{mode}:{phase}",
                runtime_instance_id=runtime_instance_id, settings=settings, work=work,
            )
            completed += int(did_run and phase == "approved")
        return completed
    if not settings.wet_lab_execution_enabled:
        return 0
    modes, invalid = _mode_context(snapshot)
    completed = 0
    for setup in setups:
        setup_id = str(setup.get("widget_id", ""))
        if not setup_id:
            continue
        try:
            proposal_hash = hash_proposal(load_typed_setup(store, canvas_id, setup_id))
        except TypedSetupArtifactError:
            continue
        results = store.list_validation_results(canvas_id, proposal_hash=proposal_hash)
        if len(results) != 1:
            continue
        direct_mode = _mode(setup)
        if "execution_mode" in setup and direct_mode is None:
            continue
        source = ArtifactStore(store.conn).get_artifact_by_widget(canvas_id=canvas_id, widget_id=setup_id)
        source_id = source.provenance.source_widget_id if source else ""
        if source_id in invalid:
            continue
        mode = direct_mode if "execution_mode" in setup else modes.get(source_id, "manual")
        result_hash, round_index = hash_validation_result(results[0]), round_of(setup.get("title"))
        try:
            require_current_execution_authorization(store, canvas_id, proposal_hash, result_hash)
            authorized = True
        except RobotExecutionAuthorizationError:
            authorized = False
        manual = mode == "manual" and round_index == 1
        activated = has_manual_execution_activation(store, canvas_id, setup_id, proposal_hash, result_hash)
        phase = "approved" if authorized and (not manual or activated) else "waiting"
        did_run = await process_trigger(
            store, canvas_id=canvas_id, trigger_id=f"setup_run:{setup_id}:{proposal_hash}:{result_hash}:{mode}:{phase}", runtime_instance_id=runtime_instance_id, settings=settings,
            work=partial(
                _run_legacy, mcp, adapter, settings, store, canvas_id, setup, setup_id,
                str(setup.get("robot_id", "")), round_index, proposal_hash,
                phase, authorized, manual, gov,
            ),
        )
        completed += int(did_run and phase == "approved")
    return completed


async def _run_phase8(lifecycle: Phase8Orchestrator, canvas_id: str, setup_id: str, round_index: int) -> tuple[bool, str]:
    outcome = await lifecycle.execute_setup(canvas_id=canvas_id, setup_id=setup_id, round_index=round_index)
    return outcome.execution.status.value == "succeeded", ""


__all__ = ["process_execution_triggers"]
