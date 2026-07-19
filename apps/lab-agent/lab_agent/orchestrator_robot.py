"""Robot-result orchestration for one prepared experiment setup."""

from __future__ import annotations

from lab_agent.adapters.base import ModelAdapter
from lab_agent.artifact_provenance import model_provenance
from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient
from lab_agent.models.experiment import ExperimentResult
from lab_agent.models.governance import TaskStage
from lab_agent.orchestrator_support import emit_result, write_result_node
from lab_agent.state_store import StateStore


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
) -> tuple[str, ExperimentResult | None]:
    """Mock a robot run and emit its validated experiment-result node."""
    result = await emit_result(adapter, setup_text)
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


__all__ = ["run_on_robot"]
