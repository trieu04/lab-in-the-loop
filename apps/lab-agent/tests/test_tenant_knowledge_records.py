"""Tenant-qualified knowledge and conflict identities remain independent."""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from lab_agent.models.execution import (
    AnalysisRequest,
    ArtifactRef,
    ArtifactRole,
    ConflictRecord,
    EvidenceKind,
    ExecutionRequest,
    KnowledgeVersion,
    RunMode,
)
from lab_agent.state_store import StateStore
from lab_agent.tenant import TenantContext


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _store(path: Path, tenant_id: str) -> StateStore:
    return StateStore(path, tenant_context=TenantContext(tenant_id, {"canvas-1"}, "example.test"))


def _prepare_graph(store: StateStore, tenant_id: str) -> str:
    now = datetime.now(UTC)
    store.prepare_execution_run(ExecutionRequest(
        tenant_id=tenant_id, request_id="request", execution_run_id="execution",
        canvas_id="canvas-1", setup_id="setup", round=0, proposal_hash=_hash("proposal"),
        validation_result_hash=_hash("validation"), adapter_name="adapter", adapter_version="1",
        mode=RunMode.DRY_RUN, evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        submit_intent_key="execution-submit", input_hash=_hash("execution-input"), requested_at=now,
    ))
    store.prepare_analysis_run(AnalysisRequest(
        tenant_id=tenant_id, request_id="analysis-request", analysis_run_id="analysis",
        canvas_id="canvas-1", execution_run_id="execution", source_artifact_ref_ids=("raw",),
        adapter_name="adapter", adapter_version="1", mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN, submit_intent_key="analysis-submit",
        input_hash=_hash("analysis-input"), requested_at=now,
    ))
    store.append_artifact_ref(ArtifactRef(
        tenant_id=tenant_id, artifact_ref_id="raw", canvas_id="canvas-1", execution_run_id="execution",
        content_hash=_hash("raw"), logical_uri="mock://dry-run/raw/one", media_type="application/json",
        classification="mock", role=ArtifactRole.RAW, evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        retention_until=now, recorded_at=now,
    ))
    return "raw"


def _version(tenant_id: str, version_id: str, key: str, ref_id: str) -> KnowledgeVersion:
    hypothesis = f"hypothesis-{version_id}"
    return KnowledgeVersion(
        tenant_id=tenant_id, knowledge_version_id=version_id, canvas_id="canvas-1",
        execution_run_id="execution", analysis_run_id="analysis", proposal_hash=_hash("proposal"),
        hypothesis=hypothesis, hypothesis_hash=_hash(hypothesis), evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        provenance_artifact_ref_ids=(ref_id,), payload=hypothesis, content_hash=_hash(hypothesis),
        idempotency_key=key, created_at=datetime.now(UTC),
    )


def test_duplicate_knowledge_and_conflict_keys_are_tenant_isolated(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first, second = _store(path, "tenant-a"), _store(path, "tenant-b")
    try:
        first_ref, second_ref = _prepare_graph(first, "tenant-a"), _prepare_graph(second, "tenant-b")
        first_prior, _ = first.append_knowledge_version(_version("tenant-a", "prior", "prior-key", first_ref))
        first_proposed, _ = first.append_knowledge_version(_version("tenant-a", "proposed", "proposed-key", first_ref))
        second_prior, _ = second.append_knowledge_version(_version("tenant-b", "prior", "prior-key", second_ref))
        second_proposed, _ = second.append_knowledge_version(_version("tenant-b", "proposed", "proposed-key", second_ref))
        first.append_conflict_record(_conflict("tenant-a", first_prior, first_proposed, first_ref))
        second.append_conflict_record(_conflict("tenant-b", second_prior, second_proposed, second_ref))

        assert [item.knowledge_version_id for item in first.list_knowledge_versions("canvas-1")] == ["prior", "proposed"]
        assert [item.knowledge_version_id for item in second.list_knowledge_versions("canvas-1")] == ["prior", "proposed"]
        assert first.get_conflict_record("canvas-1", "conflict").tenant_id == "tenant-a"
        assert second.get_conflict_record("canvas-1", "conflict").tenant_id == "tenant-b"
    finally:
        first.close()
        second.close()


def _conflict(tenant_id: str, prior: KnowledgeVersion, proposed: KnowledgeVersion, ref_id: str) -> ConflictRecord:
    return ConflictRecord(
        tenant_id=tenant_id, conflict_id="conflict", canvas_id="canvas-1",
        prior_knowledge_version_id=prior.knowledge_version_id,
        proposed_knowledge_version_id=proposed.knowledge_version_id,
        old_hypothesis_hash=prior.hypothesis_hash, new_hypothesis_hash=proposed.hypothesis_hash,
        evidence_artifact_ref_ids=(ref_id,), reason_code="test", reason_text="test conflict",
        idempotency_key="conflict-key", created_at=datetime.now(UTC),
    )
