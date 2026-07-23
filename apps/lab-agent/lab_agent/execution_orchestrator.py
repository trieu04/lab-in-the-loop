"""Narrow Phase 8 facade: authorization, dry-run lifecycle, then projection."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial

from lab_agent import durable_browser
from lab_agent.analysis_lifecycle import reconcile_analysis, run_analysis
from lab_agent.approval_service import require_current_execution_authorization
from lab_agent.artifact_lifecycle_payloads import (
    analysis_payload,
    conflict_payload,
    execution_payload,
    knowledge_payload,
)
from lab_agent.config import Settings
from lab_agent.execution_lifecycle import reconcile_execution, run_execution
from lab_agent.integrations.flywheel import FlywheelAdapter
from lab_agent.integrations.knowledge import KnowledgeAdapter
from lab_agent.integrations.lab_execution import LabExecutionAdapter
from lab_agent.knowledge_update import (
    KnowledgeUpdateOutcome,
    append_interpretation,
    interpret_dry_run,
)
from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactType
from lab_agent.models.execution import (
    AnalysisRequest,
    AnalysisRun,
    ArtifactRef,
    ArtifactRole,
    EvidenceKind,
    ExecutionRequest,
    ExecutionRun,
    ExternalRunStatus,
    RunMode,
)
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import hash_proposal, hash_validation_result
from lab_agent.orchestrator_validation import load_typed_setup
from lab_agent.state_store import StateStore


class Phase8DisabledError(RuntimeError):
    """The default-off lifecycle gate declined all adapter work."""


@dataclass(frozen=True)
class Phase8Outcome:
    execution: ExecutionRun
    analysis: AnalysisRun | None
    knowledge: KnowledgeUpdateOutcome | None


def _digest(*parts: str) -> str:
    return hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()


def _identity(adapter: object, field: str) -> str:
    value = getattr(adapter, field, "")
    return value if isinstance(value, str) and value else "dry-run-adapter"


@dataclass
class Phase8Orchestrator:
    """Coordinates only the installed deterministic adapters; no real mode exists."""

    store: StateStore
    settings: Settings
    execution_adapter: LabExecutionAdapter
    analysis_adapter: FlywheelAdapter
    knowledge_adapter: KnowledgeAdapter
    enabled: bool
    mcp: MCPClient | None = None

    async def execute_setup(self, *, canvas_id: str, setup_id: str, round_index: int) -> Phase8Outcome:
        """Run an approved setup only after exact durable authorization is rechecked."""

        self._require_dry_run_enabled()
        setup = load_typed_setup(self.store, canvas_id, setup_id)
        proposal_hash = hash_proposal(setup)
        validation_hash = self._validation_hash(canvas_id, proposal_hash)
        execution_request = self._execution_request(
            canvas_id, setup_id, round_index, proposal_hash, validation_hash
        )
        execution = await run_execution(
            self.store,
            self.execution_adapter,
            execution_request,
            authorize=lambda: require_current_execution_authorization(
                self.store, canvas_id, proposal_hash, validation_hash
            ),
        )
        raw_refs = tuple(
            ref
            for ref in self.store.list_artifact_refs(
                canvas_id, execution_run_id=execution.execution_run_id
            )
            if ref.role is ArtifactRole.RAW
        )
        await self._project_execution(execution, raw_refs, setup_id, round_index)
        if execution.status is not ExternalRunStatus.SUCCEEDED:
            return Phase8Outcome(execution, None, None)
        analysis_request = self._analysis_request(canvas_id, execution, raw_refs)
        analysis = await run_analysis(
            self.store, self.analysis_adapter, analysis_request
        )
        all_refs = tuple(
            self.store.list_artifact_refs(
                canvas_id, analysis_run_id=analysis.analysis_run_id
            )
        )
        await self._project_analysis(analysis, all_refs, setup_id, round_index)
        if analysis.status is not ExternalRunStatus.SUCCEEDED:
            return Phase8Outcome(execution, analysis, None)
        sources = raw_refs + all_refs
        prior = self.store.list_knowledge_versions(canvas_id)
        interpretation = interpret_dry_run(
            canvas_id=canvas_id,
            execution_run_id=execution.execution_run_id,
            analysis_run_id=analysis.analysis_run_id,
            original_hypothesis=setup.hypothesis,
            refs=sources,
            prior_knowledge_version_id=prior[-1].knowledge_version_id if prior else None,
        )
        knowledge = await append_interpretation(
            self.store, self.knowledge_adapter, interpretation=interpretation, proposal_hash=proposal_hash
        )
        await self._project_knowledge(knowledge, setup_id, round_index)
        return Phase8Outcome(execution, analysis, knowledge)

    async def reconcile_canvas(self, canvas_id: str) -> None:
        """Recover lifecycle rows from SQLite without consulting Canvas topology."""

        self._require_dry_run_enabled()
        for execution_run in self.store.list_execution_runs(canvas_id):
            await reconcile_execution(
                self.store,
                self.execution_adapter,
                execution_run,
                authorize=partial(self._authorize_execution, canvas_id, execution_run),
            )
        for analysis_run in self.store.list_analysis_runs(canvas_id):
            await reconcile_analysis(self.store, self.analysis_adapter, analysis_run)

    def _authorize_execution(self, canvas_id: str, run: ExecutionRun) -> object:
        return require_current_execution_authorization(
            self.store, canvas_id, run.proposal_hash, run.validation_result_hash
        )

    def _require_dry_run_enabled(self) -> None:
        if not self.enabled:
            raise Phase8DisabledError("Phase 8 execution lifecycle is disabled")
        if self.settings.phase8_execution_mode != RunMode.DRY_RUN.value:
            raise Phase8DisabledError("Phase 8 only permits dry_run mode")

    def _validation_hash(self, canvas_id: str, proposal_hash: str) -> str:
        results = self.store.list_validation_results(canvas_id, proposal_hash=proposal_hash)
        if len(results) != 1:
            raise Phase8DisabledError("exact current validation evidence is required")
        return hash_validation_result(results[0])

    def _execution_request(self, canvas_id: str, setup_id: str, round_index: int, proposal_hash: str, validation_hash: str) -> ExecutionRequest:
        input_hash = _digest(canvas_id, setup_id, str(round_index), proposal_hash, validation_hash)
        key = f"lab_execution_submit:{input_hash}"
        return ExecutionRequest(
            request_id=f"execution-request:{input_hash}", execution_run_id=f"execution-run:{input_hash}",
            canvas_id=canvas_id, setup_id=setup_id, round=round_index, proposal_hash=proposal_hash,
            validation_result_hash=validation_hash, adapter_name=_identity(self.execution_adapter, "adapter_name"),
            adapter_version=_identity(self.execution_adapter, "adapter_version"), mode=RunMode.DRY_RUN,
            evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN, submit_intent_key=key, input_hash=input_hash,
            requested_at=datetime.now(UTC),
        )

    def _analysis_request(self, canvas_id: str, execution: ExecutionRun, refs: tuple[ArtifactRef, ...]) -> AnalysisRequest:
        source_ids = tuple(ref.artifact_ref_id for ref in refs)
        input_hash = _digest(execution.execution_run_id, *source_ids)
        return AnalysisRequest(
            request_id=f"analysis-request:{input_hash}", analysis_run_id=f"analysis-run:{input_hash}",
            canvas_id=canvas_id, execution_run_id=execution.execution_run_id, source_artifact_ref_ids=source_ids,
            adapter_name=_identity(self.analysis_adapter, "adapter_name"), adapter_version=_identity(self.analysis_adapter, "adapter_version"),
            mode=RunMode.DRY_RUN, evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
            submit_intent_key=f"analysis_submit:{input_hash}", input_hash=input_hash, requested_at=datetime.now(UTC),
        )

    async def _project_execution(self, run: ExecutionRun, refs: tuple[ArtifactRef, ...], setup_id: str, round_index: int) -> None:
        await self._project(run.canvas_id, ArtifactType.EXECUTION, execution_payload(run, refs), run.execution_run_id, setup_id, round_index, "setup_execution")

    async def _project_analysis(self, run: AnalysisRun, refs: tuple[ArtifactRef, ...], setup_id: str, round_index: int) -> None:
        await self._project(run.canvas_id, ArtifactType.ANALYSIS, analysis_payload(run, refs, round_index=round_index), run.analysis_run_id, setup_id, round_index, "execution_analysis")

    async def _project_knowledge(self, outcome: KnowledgeUpdateOutcome, setup_id: str, round_index: int) -> None:
        await self._project(outcome.version.canvas_id, ArtifactType.KNOWLEDGE, knowledge_payload(outcome.version, round_index=round_index), outcome.version.knowledge_version_id, setup_id, round_index, "analysis_knowledge")
        if outcome.conflict is not None:
            await self._project(outcome.conflict.canvas_id, ArtifactType.CONFLICT, conflict_payload(outcome.conflict, round_index=round_index), outcome.conflict.conflict_id, setup_id, round_index, "knowledge_conflict")

    async def _project(self, canvas_id: str, artifact_type: ArtifactType, payload: dict[str, object], discriminator: str, predecessor_id: str, round_index: int, edge_kind: str) -> None:
        if self.mcp is None:
            return
        provenance = durable_browser.provenance_for(
            self.settings,
            source_widget_id=predecessor_id,
            trigger_id=discriminator,
        )
        await durable_browser.write_artifact_browser_durable(
            self.mcp,
            self.store,
            self.settings,
            canvas_id=canvas_id,
            artifact_type=artifact_type,
            state=DecisionState.KNOWLEDGE_UPDATE_PENDING,
            title=str(payload["title"]),
            payload=payload,
            provenance=provenance,
            discriminator=discriminator,
            round_index=round_index,
            predecessor_id=predecessor_id,
            edge_kind=edge_kind,
        )


__all__ = ["Phase8DisabledError", "Phase8Orchestrator", "Phase8Outcome"]
