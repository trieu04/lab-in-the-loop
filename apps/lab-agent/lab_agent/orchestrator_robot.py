"""Robot-result orchestration guarded by current durable validation evidence."""

from __future__ import annotations

from typing import Literal

from lab_agent.adapters.base import ModelAdapter
from lab_agent.approval_service import (
    RobotExecutionAuthorizationError,
    has_manual_execution_activation,
    require_current_execution_authorization,
)
from lab_agent.artifact_provenance import model_provenance
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.intent_audit import connect_durable
from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactType
from lab_agent.models.experiment import ExperimentResult
from lab_agent.models.governance import TaskStage
from lab_agent.models.validation import hash_proposal, hash_validation_result
from lab_agent.orchestrator_support import emit_result, write_result_node
from lab_agent.orchestrator_validation import TypedSetupArtifactError, load_typed_setup
from lab_agent.state_store import StateStore

ExecutionMode = Literal["manual", "auto"]


def _require_execution_evidence(
    store: StateStore, canvas_id: str, setup_id: str, execution_mode: ExecutionMode,
) -> tuple[str, str]:
    try:
        proposal_hash = hash_proposal(load_typed_setup(store, canvas_id, setup_id))
    except TypedSetupArtifactError as exc:
        raise RobotExecutionAuthorizationError("canonical current setup is required") from exc
    results = store.list_validation_results(canvas_id, proposal_hash=proposal_hash)
    if len(results) != 1:
        raise RobotExecutionAuthorizationError("one current validation result is required")
    result_hash = hash_validation_result(results[0])
    require_current_execution_authorization(store, canvas_id, proposal_hash, result_hash)
    if execution_mode == "manual" and not has_manual_execution_activation(
        store, canvas_id, setup_id, proposal_hash, result_hash,
    ):
        raise RobotExecutionAuthorizationError("authenticated manual activation is required")
    return proposal_hash, result_hash


def _safe_result_reuse(
    store: StateStore, canvas_id: str, setup_id: str, stale_result_id: str, round_index: int,
) -> str:
    if not stale_result_id:
        return ""
    document = ArtifactStore(store.conn, tenant_context=store.tenant_context).get_artifact_by_widget(
        canvas_id=canvas_id, widget_id=stale_result_id,
    )
    if document is None or document.artifact_type is not ArtifactType.RESULT:
        return ""
    if document.round >= round_index or document.payload.get("setup_id") != setup_id:
        return ""
    return stale_result_id


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
    execution_mode: ExecutionMode = "manual",
    stale_result_id: str = "",
) -> tuple[str, ExperimentResult | None]:
    """Emit one mock result only after current validation, approvals, and mode gates."""
    if not settings.wet_lab_execution_enabled:
        raise RobotExecutionAuthorizationError("wet-lab execution is disabled")
    proposal_hash, validation_hash = _require_execution_evidence(
        store, canvas_id, setup_id, execution_mode,
    )
    generation = store.get_result_generation(
        canvas_id, setup_id, round_index, proposal_hash, validation_hash,
    )
    if generation is None:
        result = await emit_result(adapter, setup_text, settings=settings)
        if result is None:
            return "", None
        generation = store.persist_result_generation(
            canvas_id, setup_id, round_index, proposal_hash, validation_hash, result,
        )
    result = generation.result
    reusable_result_id = _safe_result_reuse(
        store, canvas_id, setup_id, stale_result_id, round_index,
    )
    result_id = await write_result_node(
        mcp, store, settings, canvas_id=canvas_id, result=result, setup_id=setup_id,
        robot_id=robot_id, round_index=round_index,
        reuse_artifact_widget_id=reusable_result_id,
        provenance=model_provenance(
            adapter, settings, TaskStage.MOCK_RESULT, source_widget_id=setup_id,
            trigger_id=f"result/setup:{setup_id}/round:{round_index}",
        ),
    )
    if stale_result_id and result_id != stale_result_id:
        await connect_durable(
            mcp, store, canvas_id=canvas_id, src_id=result_id, dst_id=setup_id,
            edge_kind="result_setup_loop", round_index=round_index,
        )
    return result_id, result


__all__ = ["ExecutionMode", "RobotExecutionAuthorizationError", "run_on_robot"]
