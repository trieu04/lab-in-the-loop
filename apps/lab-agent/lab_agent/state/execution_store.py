"""Tenant-bound StateStore facade for Phase 8 execution and knowledge records."""
from __future__ import annotations

import sqlite3

from lab_agent.models.execution import (
    AnalysisRequest,
    AnalysisRun,
    ArtifactRef,
    ConflictRecord,
    ExecutionRequest,
    ExecutionRun,
    ExternalFailureCode,
    ExternalRunStatus,
    KnowledgeVersion,
)
from lab_agent.state import external_runs, knowledge
from lab_agent.state.models import Clock


class ExecutionStoreMixin:
    """Inject the bound tenant and validate canvases before Phase 8 SQL."""

    conn: sqlite3.Connection
    clock: Clock
    tenant_id: str

    def _require_canvas_scope(self, canvas_id: str) -> None: ...

    def _record_scope(
        self, record: ExecutionRequest | AnalysisRequest | ArtifactRef | KnowledgeVersion | ConflictRecord
    ) -> None:
        if record.tenant_id != self.tenant_id:
            raise PermissionError("Phase 8 record tenant does not match StateStore tenant")
        self._require_canvas_scope(record.canvas_id)

    def prepare_execution_run(self, request: ExecutionRequest) -> tuple[ExecutionRun, bool]:
        self._record_scope(request)
        return external_runs.prepare_execution_run(self.conn, tenant_id=self.tenant_id, request=request)

    def prepare_analysis_run(self, request: AnalysisRequest) -> tuple[AnalysisRun, bool]:
        self._record_scope(request)
        return external_runs.prepare_analysis_run(self.conn, tenant_id=self.tenant_id, request=request)

    def get_execution_run(self, canvas_id: str, execution_run_id: str) -> ExecutionRun | None:
        self._require_canvas_scope(canvas_id)
        return external_runs.get_execution_run(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, execution_run_id=execution_run_id)

    def get_analysis_run(self, canvas_id: str, analysis_run_id: str) -> AnalysisRun | None:
        self._require_canvas_scope(canvas_id)
        return external_runs.get_analysis_run(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, analysis_run_id=analysis_run_id)

    def list_execution_runs(self, canvas_id: str, *, status: ExternalRunStatus | None = None) -> list[ExecutionRun]:
        self._require_canvas_scope(canvas_id)
        return external_runs.list_execution_runs(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, status=status)

    def list_analysis_runs(self, canvas_id: str, *, status: ExternalRunStatus | None = None) -> list[AnalysisRun]:
        self._require_canvas_scope(canvas_id)
        return external_runs.list_analysis_runs(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, status=status)

    def transition_execution_run(self, canvas_id: str, execution_run_id: str, *, status: ExternalRunStatus, provider_execution_id: str | None = None, failure_code: ExternalFailureCode | None = None, abort_intent_key: str | None = None) -> ExecutionRun:
        self._require_canvas_scope(canvas_id)
        return external_runs.transition_execution_run(self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id, execution_run_id=execution_run_id, status=status, provider_execution_id=provider_execution_id, failure_code=failure_code, abort_intent_key=abort_intent_key)

    def transition_analysis_run(self, canvas_id: str, analysis_run_id: str, *, status: ExternalRunStatus, provider_job_id: str | None = None, failure_code: ExternalFailureCode | None = None, abort_intent_key: str | None = None) -> AnalysisRun:
        self._require_canvas_scope(canvas_id)
        return external_runs.transition_analysis_run(self.conn, clock=self.clock, tenant_id=self.tenant_id, canvas_id=canvas_id, analysis_run_id=analysis_run_id, status=status, provider_job_id=provider_job_id, failure_code=failure_code, abort_intent_key=abort_intent_key)

    def append_artifact_ref(self, artifact_ref: ArtifactRef) -> tuple[ArtifactRef, bool]:
        self._record_scope(artifact_ref)
        return knowledge.append_artifact_ref(self.conn, tenant_id=self.tenant_id, artifact_ref=artifact_ref)

    def get_artifact_ref(self, canvas_id: str, artifact_ref_id: str) -> ArtifactRef | None:
        self._require_canvas_scope(canvas_id)
        return knowledge.get_artifact_ref(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, artifact_ref_id=artifact_ref_id)

    def list_artifact_refs(self, canvas_id: str, *, execution_run_id: str | None = None, analysis_run_id: str | None = None) -> list[ArtifactRef]:
        self._require_canvas_scope(canvas_id)
        return knowledge.list_artifact_refs(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, execution_run_id=execution_run_id, analysis_run_id=analysis_run_id)

    def append_knowledge_version(self, version: KnowledgeVersion) -> tuple[KnowledgeVersion, bool]:
        self._record_scope(version)
        return knowledge.append_knowledge_version(self.conn, tenant_id=self.tenant_id, version=version)

    def get_knowledge_version(self, canvas_id: str, knowledge_version_id: str) -> KnowledgeVersion | None:
        self._require_canvas_scope(canvas_id)
        return knowledge.get_knowledge_version(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, knowledge_version_id=knowledge_version_id)

    def list_knowledge_versions(self, canvas_id: str) -> list[KnowledgeVersion]:
        self._require_canvas_scope(canvas_id)
        return knowledge.list_knowledge_versions(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id)

    def append_conflict_record(self, conflict: ConflictRecord) -> tuple[ConflictRecord, bool]:
        self._record_scope(conflict)
        return knowledge.append_conflict_record(self.conn, tenant_id=self.tenant_id, conflict=conflict)

    def get_conflict_record(self, canvas_id: str, conflict_id: str) -> ConflictRecord | None:
        self._require_canvas_scope(canvas_id)
        return knowledge.get_conflict_record(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id, conflict_id=conflict_id)

    def list_conflict_records(self, canvas_id: str) -> list[ConflictRecord]:
        self._require_canvas_scope(canvas_id)
        return knowledge.list_conflict_records(self.conn, tenant_id=self.tenant_id, canvas_id=canvas_id)


__all__ = ["ExecutionStoreMixin"]
