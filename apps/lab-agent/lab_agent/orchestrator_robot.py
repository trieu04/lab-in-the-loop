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
from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.models.experiment import ExperimentResult
from lab_agent.models.governance import TaskStage
from lab_agent.models.validation import hash_proposal, hash_validation_result
from lab_agent.orchestrator_support import emit_result, write_result_node
from lab_agent.orchestrator_validation import TypedSetupArtifactError, load_typed_setup
from lab_agent.state_store import StateStore

ExecutionMode = Literal["manual", "auto"]


def _require_execution_evidence(
    store: StateStore, canvas_id: str, setup_id: str, execution_mode: ExecutionMode
) -> None:
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
        store, canvas_id, setup_id, proposal_hash, result_hash
    ):
        raise RobotExecutionAuthorizationError("authenticated manual activation is required")


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
) -> tuple[str, ExperimentResult | None]:
    """Emit one mock result only after current validation, approvals, and mode gates."""

    if not settings.wet_lab_execution_enabled:
        raise RobotExecutionAuthorizationError("wet-lab execution is disabled")
    _require_execution_evidence(store, canvas_id, setup_id, execution_mode)
    result = await emit_result(adapter, setup_text, settings=settings)
    if result is None:
        return "", None
    result_id = await write_result_node(
        mcp,
        store,
        settings,
        canvas_id=canvas_id,
        result=result,
        setup_id=setup_id,
        robot_id=robot_id,
        round_index=round_index,
        provenance=model_provenance(
            adapter,
            settings,
            TaskStage.MOCK_RESULT,
            source_widget_id=setup_id,
            trigger_id=f"result/setup:{setup_id}/round:{round_index}",
        ),
    )
    return result_id, result


__all__ = ["ExecutionMode", "RobotExecutionAuthorizationError", "run_on_robot"]
