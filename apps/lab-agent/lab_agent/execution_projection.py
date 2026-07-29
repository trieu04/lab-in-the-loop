"""Safe Browser projection helpers for durable Phase 8 records."""
from __future__ import annotations

from lab_agent import durable_browser
from lab_agent.artifact_lifecycle_payloads import (
    analysis_payload,
    conflict_payload,
    execution_payload,
    knowledge_payload,
)
from lab_agent.config import Settings
from lab_agent.knowledge_update import KnowledgeUpdateOutcome
from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactType
from lab_agent.models.execution import AnalysisRun, ArtifactRef, ExecutionRun
from lab_agent.models.states import DecisionState
from lab_agent.state_store import StateStore


async def project_execution(
    mcp: MCPClient | None, store: StateStore, settings: Settings, run: ExecutionRun,
    refs: tuple[ArtifactRef, ...], setup_id: str, round_index: int,
) -> None:
    await _project(
        mcp, store, settings, run.canvas_id, ArtifactType.EXECUTION,
        execution_payload(run, refs), run.execution_run_id, setup_id, round_index,
        "setup_execution",
    )


async def project_analysis(
    mcp: MCPClient | None, store: StateStore, settings: Settings, run: AnalysisRun,
    refs: tuple[ArtifactRef, ...], setup_id: str, round_index: int,
) -> None:
    await _project(
        mcp, store, settings, run.canvas_id, ArtifactType.ANALYSIS,
        analysis_payload(run, refs, round_index=round_index), run.analysis_run_id,
        setup_id, round_index, "execution_analysis",
    )


async def project_knowledge(
    mcp: MCPClient | None, store: StateStore, settings: Settings,
    outcome: KnowledgeUpdateOutcome, setup_id: str, round_index: int,
) -> None:
    await _project(
        mcp, store, settings, outcome.version.canvas_id, ArtifactType.KNOWLEDGE,
        knowledge_payload(outcome.version, round_index=round_index),
        outcome.version.knowledge_version_id, setup_id, round_index, "analysis_knowledge",
    )
    if outcome.conflict is not None:
        await _project(
            mcp, store, settings, outcome.conflict.canvas_id, ArtifactType.CONFLICT,
            conflict_payload(outcome.conflict, round_index=round_index),
            outcome.conflict.conflict_id, setup_id, round_index, "knowledge_conflict",
        )


async def _project(
    mcp: MCPClient | None, store: StateStore, settings: Settings, canvas_id: str,
    artifact_type: ArtifactType, payload: dict[str, object], discriminator: str,
    predecessor_id: str, round_index: int, edge_kind: str,
) -> None:
    if mcp is None:
        return
    provenance = durable_browser.provenance_for(
        settings, source_widget_id=predecessor_id, trigger_id=discriminator,
    )
    await durable_browser.write_artifact_browser_durable(
        mcp, store, settings, canvas_id=canvas_id, artifact_type=artifact_type,
        state=DecisionState.KNOWLEDGE_UPDATE_PENDING, title=str(payload["title"]),
        payload=payload, provenance=provenance, discriminator=discriminator,
        round_index=round_index, predecessor_id=predecessor_id, edge_kind=edge_kind,
    )


__all__ = ["project_analysis", "project_execution", "project_knowledge"]
