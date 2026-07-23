"""StateStore facade for Phase 8 durable execution and knowledge records."""
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
    """Expose small canvas-scoped persistence operations without growing StateStore."""

    conn: sqlite3.Connection
    clock: Clock

    def prepare_execution_run(
        self, request: ExecutionRequest
    ) -> tuple[ExecutionRun, bool]:
        return external_runs.prepare_execution_run(self.conn, request=request)

    def prepare_analysis_run(
        self, request: AnalysisRequest
    ) -> tuple[AnalysisRun, bool]:
        return external_runs.prepare_analysis_run(self.conn, request=request)

    def get_execution_run(
        self, canvas_id: str, execution_run_id: str
    ) -> ExecutionRun | None:
        return external_runs.get_execution_run(
            self.conn, canvas_id=canvas_id, execution_run_id=execution_run_id
        )

    def get_analysis_run(
        self, canvas_id: str, analysis_run_id: str
    ) -> AnalysisRun | None:
        return external_runs.get_analysis_run(
            self.conn, canvas_id=canvas_id, analysis_run_id=analysis_run_id
        )

    def list_execution_runs(
        self, canvas_id: str, *, status: ExternalRunStatus | None = None
    ) -> list[ExecutionRun]:
        return external_runs.list_execution_runs(
            self.conn, canvas_id=canvas_id, status=status
        )

    def list_analysis_runs(
        self, canvas_id: str, *, status: ExternalRunStatus | None = None
    ) -> list[AnalysisRun]:
        return external_runs.list_analysis_runs(
            self.conn, canvas_id=canvas_id, status=status
        )

    def transition_execution_run(
        self,
        canvas_id: str,
        execution_run_id: str,
        *,
        status: ExternalRunStatus,
        provider_execution_id: str | None = None,
        failure_code: ExternalFailureCode | None = None,
        abort_intent_key: str | None = None,
    ) -> ExecutionRun:
        return external_runs.transition_execution_run(
            self.conn,
            clock=self.clock,
            canvas_id=canvas_id,
            execution_run_id=execution_run_id,
            status=status,
            provider_execution_id=provider_execution_id,
            failure_code=failure_code,
            abort_intent_key=abort_intent_key,
        )

    def transition_analysis_run(
        self,
        canvas_id: str,
        analysis_run_id: str,
        *,
        status: ExternalRunStatus,
        provider_job_id: str | None = None,
        failure_code: ExternalFailureCode | None = None,
        abort_intent_key: str | None = None,
    ) -> AnalysisRun:
        return external_runs.transition_analysis_run(
            self.conn,
            clock=self.clock,
            canvas_id=canvas_id,
            analysis_run_id=analysis_run_id,
            status=status,
            provider_job_id=provider_job_id,
            failure_code=failure_code,
            abort_intent_key=abort_intent_key,
        )

    def append_artifact_ref(
        self, artifact_ref: ArtifactRef
    ) -> tuple[ArtifactRef, bool]:
        return knowledge.append_artifact_ref(self.conn, artifact_ref=artifact_ref)

    def get_artifact_ref(
        self, canvas_id: str, artifact_ref_id: str
    ) -> ArtifactRef | None:
        return knowledge.get_artifact_ref(
            self.conn, canvas_id=canvas_id, artifact_ref_id=artifact_ref_id
        )

    def list_artifact_refs(
        self,
        canvas_id: str,
        *,
        execution_run_id: str | None = None,
        analysis_run_id: str | None = None,
    ) -> list[ArtifactRef]:
        return knowledge.list_artifact_refs(
            self.conn,
            canvas_id=canvas_id,
            execution_run_id=execution_run_id,
            analysis_run_id=analysis_run_id,
        )

    def append_knowledge_version(
        self, version: KnowledgeVersion
    ) -> tuple[KnowledgeVersion, bool]:
        return knowledge.append_knowledge_version(self.conn, version=version)

    def get_knowledge_version(
        self, canvas_id: str, knowledge_version_id: str
    ) -> KnowledgeVersion | None:
        return knowledge.get_knowledge_version(
            self.conn, canvas_id=canvas_id, knowledge_version_id=knowledge_version_id
        )

    def list_knowledge_versions(self, canvas_id: str) -> list[KnowledgeVersion]:
        return knowledge.list_knowledge_versions(self.conn, canvas_id=canvas_id)

    def append_conflict_record(
        self, conflict: ConflictRecord
    ) -> tuple[ConflictRecord, bool]:
        return knowledge.append_conflict_record(self.conn, conflict=conflict)

    def get_conflict_record(
        self, canvas_id: str, conflict_id: str
    ) -> ConflictRecord | None:
        return knowledge.get_conflict_record(
            self.conn, canvas_id=canvas_id, conflict_id=conflict_id
        )

    def list_conflict_records(self, canvas_id: str) -> list[ConflictRecord]:
        return knowledge.list_conflict_records(self.conn, canvas_id=canvas_id)

__all__ = ["ExecutionStoreMixin"]
