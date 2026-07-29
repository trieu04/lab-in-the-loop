"""Phase 9 tenant isolation for the complete Phase 8 durable graph."""

from __future__ import annotations

import hashlib
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lab_agent.analysis_lifecycle import run_analysis
from lab_agent.execution_lifecycle import run_execution
from lab_agent.integrations.flywheel import DeterministicFlywheelAdapter
from lab_agent.integrations.lab_execution import DeterministicLabExecutionAdapter
from lab_agent.models.execution import (
    AnalysisRequest,
    ArtifactRef,
    ArtifactRole,
    EvidenceKind,
    ExecutionRequest,
    ExternalRunStatus,
    KnowledgeVersion,
    RunMode,
)
from lab_agent.state.knowledge import KnowledgeLineageError
from lab_agent.state_store import StateStore
from lab_agent.tenant import TenantContext


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _request(tenant_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=tenant_id,
        request_id="request-1",
        execution_run_id="execution-1",
        canvas_id="canvas-1",
        setup_id="setup-1",
        round=0,
        proposal_hash=_hash("proposal"),
        validation_result_hash=_hash("validation"),
        adapter_name="deterministic",
        adapter_version="1",
        mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        submit_intent_key="submit-1",
        input_hash=_hash("input"),
        requested_at=datetime.now(UTC),
    )


def _analysis_request(tenant_id: str, source_id: str) -> AnalysisRequest:
    return AnalysisRequest(
        tenant_id=tenant_id, request_id="analysis-request", analysis_run_id="analysis-1",
        canvas_id="canvas-1", execution_run_id="execution-1", source_artifact_ref_ids=(source_id,),
        adapter_name="deterministic", adapter_version="1", mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN, submit_intent_key="analysis-submit",
        input_hash=_hash("analysis-input"), requested_at=datetime.now(UTC),
    )


def _store(path: Path, tenant_id: str, migrations_dir: Path | None = None) -> StateStore:
    return StateStore(
        path,
        tenant_context=TenantContext(tenant_id, {"canvas-1"}, "example.test"),
        migrations_dir=migrations_dir,
    )


async def test_duplicate_execution_identity_and_lifecycle_are_tenant_isolated(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first, second = _store(path, "tenant-a"), _store(path, "tenant-b")
    try:
        adapter = DeterministicLabExecutionAdapter()
        first_run = await run_execution(first, adapter, _request("tenant-a"), authorize=lambda: None)
        second_run = await run_execution(second, adapter, _request("tenant-b"), authorize=lambda: None)

        first_ref = first.list_artifact_refs("canvas-1")[0]
        second_ref = second.list_artifact_refs("canvas-1")[0]
        analysis_adapter = DeterministicFlywheelAdapter()
        first_analysis = await run_analysis(first, analysis_adapter, _analysis_request("tenant-a", first_ref.artifact_ref_id))
        second_analysis = await run_analysis(second, analysis_adapter, _analysis_request("tenant-b", second_ref.artifact_ref_id))

        assert (first_run.tenant_id, first_analysis.tenant_id) == ("tenant-a", "tenant-a")
        assert (second_run.tenant_id, second_analysis.tenant_id) == ("tenant-b", "tenant-b")
        assert first.list_execution_runs("canvas-1") == [first_run]
        assert second.list_execution_runs("canvas-1") == [second_run]
        assert first_ref.tenant_id == "tenant-a"
        assert second_ref.tenant_id == "tenant-b"
    finally:
        first.close()
        second.close()


def test_same_run_id_transition_cannot_mutate_another_tenant(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first, second = _store(path, "tenant-a"), _store(path, "tenant-b")
    try:
        first.prepare_execution_run(_request("tenant-a"))
        second.prepare_execution_run(_request("tenant-b"))
        first.transition_execution_run("canvas-1", "execution-1", status=ExternalRunStatus.SUBMITTED)

        assert first.get_execution_run("canvas-1", "execution-1").status is ExternalRunStatus.SUBMITTED
        assert second.get_execution_run("canvas-1", "execution-1").status is ExternalRunStatus.PENDING
    finally:
        first.close()
        second.close()


def test_cross_tenant_artifact_cannot_satisfy_knowledge_lineage(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first, second = _store(path, "tenant-a"), _store(path, "tenant-b")
    try:
        first.prepare_execution_run(_request("tenant-a"))
        second.prepare_execution_run(_request("tenant-b"))
        second.prepare_analysis_run(AnalysisRequest(
            tenant_id="tenant-b", request_id="analysis-request", analysis_run_id="analysis-1",
            canvas_id="canvas-1", execution_run_id="execution-1", source_artifact_ref_ids=("raw-1",),
            adapter_name="deterministic", adapter_version="1", mode=RunMode.DRY_RUN,
            evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN, submit_intent_key="analysis-submit",
            input_hash=_hash("analysis-input"), requested_at=datetime.now(UTC),
        ))
        first.append_artifact_ref(ArtifactRef(
            tenant_id="tenant-a", artifact_ref_id="raw-1", canvas_id="canvas-1",
            execution_run_id="execution-1", content_hash=_hash("raw"),
            logical_uri="mock://dry-run/raw/one", media_type="application/json",
            classification="mock", role=ArtifactRole.RAW,
            evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN, retention_until=datetime.now(UTC),
            recorded_at=datetime.now(UTC),
        ))
        version = KnowledgeVersion(
            tenant_id="tenant-b", knowledge_version_id="knowledge-1", canvas_id="canvas-1",
            execution_run_id="execution-1", analysis_run_id="analysis-1", proposal_hash=_hash("proposal"),
            hypothesis="hypothesis", hypothesis_hash=_hash("hypothesis"),
            evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN, provenance_artifact_ref_ids=("raw-1",),
            payload="mock", content_hash=_hash("mock"), idempotency_key="knowledge-key",
            created_at=datetime.now(UTC),
        )

        with pytest.raises(KnowledgeLineageError):
            second.append_knowledge_version(version)
    finally:
        first.close()
        second.close()


def test_migration_rebuilds_legacy_execution_row_under_default_tenant(tmp_path: Path) -> None:
    package_migrations = Path(__file__).parents[1] / "lab_agent" / "migrations"
    before = tmp_path / "before"
    after = tmp_path / "after"
    before.mkdir()
    after.mkdir()
    for source in package_migrations.glob("*.sql"):
        if source.name < "018_tenant_execution_knowledge.sql":
            shutil.copy(source, before / source.name)
        shutil.copy(source, after / source.name)

    path = tmp_path / "state.db"
    legacy = StateStore(path, migrations_dir=before)
    try:
        legacy.conn.execute(
            "INSERT INTO execution_runs "
            "(execution_run_id,canvas_id,request_id,setup_id,round_index,proposal_hash,"
            "validation_result_hash,adapter_name,adapter_version,mode,evidence_kind,"
            "submit_intent_key,input_hash,status,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy-run", "canvas-1", "request", "setup", 0, _hash("proposal"),
                _hash("validation"), "adapter", "1", "dry_run", "mock_or_dry_run",
                "legacy-submit", _hash("input"), "pending", datetime.now(UTC).isoformat(),
            ),
        )
    finally:
        legacy.close()

    upgraded = StateStore(path, migrations_dir=after)
    try:
        row = upgraded.conn.execute(
            "SELECT tenant_id FROM execution_runs WHERE execution_run_id=?", ("legacy-run",)
        ).fetchone()
        assert row is not None
        assert row["tenant_id"] == "default"
    finally:
        upgraded.close()
