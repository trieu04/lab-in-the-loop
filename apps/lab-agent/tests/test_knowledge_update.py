"""Phase 8 knowledge update tests: append-only history, same key converges, conflict preservation, evidence refs."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from lab_agent.models.execution import (
    AnalysisRequest,
    ArtifactRef,
    ArtifactRole,
    ConflictRecord,
    EvidenceKind,
    ExecutionRequest,
    ExternalRunStatus,
    KnowledgeVersion,
    RunMode,
)
from lab_agent.state_store import StateStore


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _now():
    return datetime.now(UTC)


def _setup_execution_run(
    store: StateStore, canvas_id: str = "canvas-1", execution_run_id: str = "exec-1"
) -> str:
    """Create ExecutionRun through StateStore API; return execution_run_id."""
    request = ExecutionRequest(
        request_id="req-1",
        execution_run_id=execution_run_id,
        canvas_id=canvas_id,
        setup_id="setup-1",
        round=0,
        proposal_hash=_hash("proposal"),
        validation_result_hash=_hash("validation"),
        adapter_name="adapter",
        adapter_version="1",
        mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        submit_intent_key=f"exec-intent-{canvas_id}",
        input_hash=_hash("exec-input"),
        requested_at=_now(),
    )
    run, _ = store.prepare_execution_run(request)
    store.transition_execution_run(
        canvas_id,
        execution_run_id,
        status=ExternalRunStatus.SUBMITTED,
        provider_execution_id=f"prov-{execution_run_id}",
    )
    store.transition_execution_run(canvas_id, execution_run_id, status=ExternalRunStatus.RUNNING)
    store.transition_execution_run(canvas_id, execution_run_id, status=ExternalRunStatus.SUCCEEDED)
    return execution_run_id


def _setup_analysis_run(
    store: StateStore,
    canvas_id: str = "canvas-1",
    execution_run_id: str = "exec-1",
    analysis_run_id: str = "analysis-1",
) -> str:
    """Create AnalysisRun through StateStore API; return analysis_run_id."""
    request = AnalysisRequest(
        request_id="req-2",
        analysis_run_id=analysis_run_id,
        canvas_id=canvas_id,
        execution_run_id=execution_run_id,
        source_artifact_ref_ids=("ref-1", "ref-2"),
        adapter_name="adapter",
        adapter_version="1",
        mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        submit_intent_key=f"analysis-intent-{canvas_id}",
        input_hash=_hash("analysis-input"),
        requested_at=_now(),
    )
    run, _ = store.prepare_analysis_run(request)
    store.transition_analysis_run(
        canvas_id, analysis_run_id, status=ExternalRunStatus.SUBMITTED, provider_job_id=f"job-{analysis_run_id}"
    )
    store.transition_analysis_run(canvas_id, analysis_run_id, status=ExternalRunStatus.RUNNING)
    store.transition_analysis_run(canvas_id, analysis_run_id, status=ExternalRunStatus.SUCCEEDED)
    return analysis_run_id

def _setup_artifact_refs(store: StateStore, canvas_id: str = "canvas-1", execution_run_id: str = "exec-1") -> tuple[str, str]:
    """Create two ArtifactRef records through StateStore; return (ref1_id, ref2_id)."""
    ref1_id = f"ref-1-{canvas_id}"
    ref2_id = f"ref-2-{canvas_id}"

    ref1 = ArtifactRef(
        artifact_ref_id=ref1_id,
        canvas_id=canvas_id,
        execution_run_id=execution_run_id,
        analysis_run_id=None,
        content_hash=_hash("content-1"),
        logical_uri="mock://dry-run/execution/result-1",
        media_type="application/json",
        classification="internal",
        role=ArtifactRole.RAW,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        retention_until=_now(),
        recorded_at=_now(),
    )
    store.append_artifact_ref(ref1)

    ref2 = ArtifactRef(
        artifact_ref_id=ref2_id,
        canvas_id=canvas_id,
        execution_run_id=execution_run_id,
        analysis_run_id=None,
        content_hash=_hash("content-2"),
        logical_uri="mock://dry-run/execution/result-2",
        media_type="application/json",
        classification="internal",
        role=ArtifactRole.RAW,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        retention_until=_now(),
        recorded_at=_now(),
    )
    store.append_artifact_ref(ref2)
    return ref1_id, ref2_id


def _knowledge_version(
    canvas_id: str = "canvas-1",
    knowledge_version_id: str = "kv-1",
    execution_run_id: str = "exec-1",
    analysis_run_id: str = "analysis-1",
    idempotency_key: str = "key-1",
    hypothesis: str = "knowledge content",
    artifact_refs: tuple[str, str] | None = None,
) -> KnowledgeVersion:
    """Create KnowledgeVersion with scoped refs and keys."""
    if artifact_refs is None:
        artifact_refs = (f"ref-1-{canvas_id}", f"ref-2-{canvas_id}")

    # Always scope idempotency keys by canvas for global uniqueness
    final_key = f"{canvas_id}-{idempotency_key}" if not idempotency_key.startswith(canvas_id) else idempotency_key

    return KnowledgeVersion(
        knowledge_version_id=knowledge_version_id,
        canvas_id=canvas_id,
        execution_run_id=execution_run_id,
        analysis_run_id=analysis_run_id,
        proposal_hash=_hash("proposal"),
        hypothesis=hypothesis,
        hypothesis_hash=_hash(hypothesis),
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        provenance_artifact_ref_ids=artifact_refs,
        payload=f"{hypothesis} payload",
        content_hash=_hash(f"{hypothesis} payload"),
        idempotency_key=final_key,
        created_at=_now(),
    )


def test_append_knowledge_version_creates_row(store: StateStore) -> None:
    """Append creates a new knowledge version row."""
    _setup_execution_run(store)
    _setup_analysis_run(store)
    ref1_id, ref2_id = _setup_artifact_refs(store)

    version = _knowledge_version(artifact_refs=(ref1_id, ref2_id))
    appended, is_new = store.append_knowledge_version(version)

    assert is_new is True
    assert appended.knowledge_version_id == version.knowledge_version_id
    assert appended.canvas_id == "canvas-1"
    assert appended.evidence_kind is EvidenceKind.MOCK_OR_DRY_RUN


def test_append_knowledge_version_idempotent(store: StateStore) -> None:
    """Same idempotency key returns same version; second append is not new."""
    _setup_execution_run(store)
    _setup_analysis_run(store)
    ref1_id, ref2_id = _setup_artifact_refs(store)

    version = _knowledge_version(idempotency_key="canvas-1-key-1", artifact_refs=(ref1_id, ref2_id))
    appended1, is_new1 = store.append_knowledge_version(version)
    appended2, is_new2 = store.append_knowledge_version(version)

    assert is_new1 is True
    assert is_new2 is False
    assert appended1.knowledge_version_id == appended2.knowledge_version_id


def test_append_only_history_immutable(store: StateStore) -> None:
    """Versions are immutable; later versions do not overwrite earlier ones."""
    _setup_execution_run(store)
    _setup_analysis_run(store)
    ref1_id, ref2_id = _setup_artifact_refs(store)

    version1 = _knowledge_version(knowledge_version_id="v1", idempotency_key="canvas-1-key-v1", hypothesis="v1", artifact_refs=(ref1_id, ref2_id))
    v1, _ = store.append_knowledge_version(version1)

    version2 = _knowledge_version(knowledge_version_id="v2", idempotency_key="canvas-1-key-v2", hypothesis="v2 hypothesis", artifact_refs=(ref1_id, ref2_id))
    v2, _ = store.append_knowledge_version(version2)

    queried_v1 = store.get_knowledge_version("canvas-1", "v1")
    queried_v2 = store.get_knowledge_version("canvas-1", "v2")

    assert queried_v1 is not None
    assert queried_v2 is not None
    assert queried_v1.hypothesis == "v1"
    assert queried_v2.hypothesis == "v2 hypothesis"
    assert queried_v1.knowledge_version_id == "v1"


def test_knowledge_versions_canvas_scoped(store: StateStore) -> None:
    """Get verifies canvas_id; cross-canvas lookup fails."""
    _setup_execution_run(store, canvas_id="canvas-1")
    _setup_analysis_run(store, canvas_id="canvas-1")
    ref1_c1, ref2_c1 = _setup_artifact_refs(store, canvas_id="canvas-1")

    _setup_execution_run(store, canvas_id="canvas-2", execution_run_id="exec-2")
    _setup_analysis_run(store, canvas_id="canvas-2", execution_run_id="exec-2", analysis_run_id="analysis-2")
    ref1_c2, ref2_c2 = _setup_artifact_refs(store, canvas_id="canvas-2", execution_run_id="exec-2")

    version1 = _knowledge_version(canvas_id="canvas-1", knowledge_version_id="v1", idempotency_key="canvas-1-v1", artifact_refs=(ref1_c1, ref2_c1))
    store.append_knowledge_version(version1)

    version2 = _knowledge_version(canvas_id="canvas-2", knowledge_version_id="v2", execution_run_id="exec-2", analysis_run_id="analysis-2", idempotency_key="canvas-2-v2", artifact_refs=(ref1_c2, ref2_c2))
    store.append_knowledge_version(version2)

    found1 = store.get_knowledge_version("canvas-1", "v1")
    assert found1 is not None

    not_found = store.get_knowledge_version("canvas-1", "v2")
    assert not_found is None


def test_list_knowledge_versions_canvas_scoped(store: StateStore) -> None:
    """List only returns versions from the given canvas."""
    _setup_execution_run(store, canvas_id="canvas-1")
    _setup_analysis_run(store, canvas_id="canvas-1")
    ref1_c1, ref2_c1 = _setup_artifact_refs(store, canvas_id="canvas-1")

    _setup_execution_run(store, canvas_id="canvas-2", execution_run_id="exec-2")
    _setup_analysis_run(store, canvas_id="canvas-2", execution_run_id="exec-2", analysis_run_id="analysis-2")
    ref1_c2, ref2_c2 = _setup_artifact_refs(store, canvas_id="canvas-2", execution_run_id="exec-2")

    version1 = _knowledge_version(canvas_id="canvas-1", knowledge_version_id="v1", idempotency_key="canvas-1-v1", artifact_refs=(ref1_c1, ref2_c1))
    store.append_knowledge_version(version1)

    version2 = _knowledge_version(canvas_id="canvas-2", knowledge_version_id="v2", execution_run_id="exec-2", analysis_run_id="analysis-2", idempotency_key="canvas-2-v2", artifact_refs=(ref1_c2, ref2_c2))
    store.append_knowledge_version(version2)

    canvas1_versions = store.list_knowledge_versions("canvas-1")
    assert len(canvas1_versions) == 1
    assert canvas1_versions[0].knowledge_version_id == "v1"

    canvas2_versions = store.list_knowledge_versions("canvas-2")
    assert len(canvas2_versions) == 1
    assert canvas2_versions[0].knowledge_version_id == "v2"


def test_append_conflict_record(store: StateStore) -> None:
    """Append conflict preserves old and new versions plus evidence refs."""
    _setup_execution_run(store)
    _setup_analysis_run(store)
    ref1_id, ref2_id = _setup_artifact_refs(store)

    prior_version = _knowledge_version(knowledge_version_id="prior-v", hypothesis="prior", artifact_refs=(ref1_id, ref2_id))
    prior, _ = store.append_knowledge_version(prior_version)

    proposed_version = _knowledge_version(knowledge_version_id="proposed-v", idempotency_key="canvas-1-key-proposed", hypothesis="proposed", artifact_refs=(ref1_id, ref2_id))
    proposed, _ = store.append_knowledge_version(proposed_version)

    conflict = ConflictRecord(
        conflict_id="conflict-1",
        canvas_id="canvas-1",
        prior_knowledge_version_id=prior.knowledge_version_id,
        proposed_knowledge_version_id=proposed.knowledge_version_id,
        old_hypothesis_hash=prior.hypothesis_hash,
        new_hypothesis_hash=proposed.hypothesis_hash,
        evidence_artifact_ref_ids=(ref1_id, ref2_id),
        reason_code="divergent_paths",
        reason_text="paths diverged",
        idempotency_key="canvas-1-conflict-key-1",
        created_at=_now(),
    )
    appended_conflict, is_new = store.append_conflict_record(conflict)

    assert is_new is True
    assert appended_conflict.prior_knowledge_version_id == prior.knowledge_version_id
    assert appended_conflict.proposed_knowledge_version_id == proposed.knowledge_version_id
    assert ref1_id in appended_conflict.evidence_artifact_ref_ids


def test_append_conflict_record_idempotent(store: StateStore) -> None:
    """Same conflict idempotency key returns same record."""
    _setup_execution_run(store)
    _setup_analysis_run(store)
    ref1_id, ref2_id = _setup_artifact_refs(store)

    prior_version = _knowledge_version(knowledge_version_id="prior-v", hypothesis="prior", artifact_refs=(ref1_id, ref2_id))
    prior, _ = store.append_knowledge_version(prior_version)

    proposed_version = _knowledge_version(knowledge_version_id="proposed-v", idempotency_key="canvas-1-key-proposed", hypothesis="proposed", artifact_refs=(ref1_id, ref2_id))
    proposed, _ = store.append_knowledge_version(proposed_version)

    conflict = ConflictRecord(
        conflict_id="conflict-1",
        canvas_id="canvas-1",
        prior_knowledge_version_id=prior.knowledge_version_id,
        proposed_knowledge_version_id=proposed.knowledge_version_id,
        old_hypothesis_hash=prior.hypothesis_hash,
        new_hypothesis_hash=proposed.hypothesis_hash,
        evidence_artifact_ref_ids=(ref1_id,),
        reason_code="divergent",
        reason_text="diverged",
        idempotency_key="canvas-1-conflict-key",
        created_at=_now(),
    )
    appended1, is_new1 = store.append_conflict_record(conflict)
    appended2, is_new2 = store.append_conflict_record(conflict)

    assert is_new1 is True
    assert is_new2 is False
    assert appended1.conflict_id == appended2.conflict_id


def test_conflict_records_canvas_scoped(store: StateStore) -> None:
    """Get verifies canvas_id; cross-canvas lookup fails."""
    _setup_execution_run(store, canvas_id="canvas-1")
    _setup_analysis_run(store, canvas_id="canvas-1")
    ref1_c1, ref2_c1 = _setup_artifact_refs(store, canvas_id="canvas-1")

    _setup_execution_run(store, canvas_id="canvas-2", execution_run_id="exec-2")
    _setup_analysis_run(store, canvas_id="canvas-2", execution_run_id="exec-2", analysis_run_id="analysis-2")
    ref1_c2, ref2_c2 = _setup_artifact_refs(store, canvas_id="canvas-2", execution_run_id="exec-2")

    prior_v1 = _knowledge_version(canvas_id="canvas-1", knowledge_version_id="prior-v1", hypothesis="prior1", artifact_refs=(ref1_c1, ref2_c1))
    prior1, _ = store.append_knowledge_version(prior_v1)

    proposed_v1 = _knowledge_version(canvas_id="canvas-1", knowledge_version_id="proposed-v1", idempotency_key="canvas-1-key-proposed-1", hypothesis="proposed1", artifact_refs=(ref1_c1, ref2_c1))
    proposed1, _ = store.append_knowledge_version(proposed_v1)

    conflict1 = ConflictRecord(
        conflict_id="conflict-1",
        canvas_id="canvas-1",
        prior_knowledge_version_id=prior1.knowledge_version_id,
        proposed_knowledge_version_id=proposed1.knowledge_version_id,
        old_hypothesis_hash=prior1.hypothesis_hash,
        new_hypothesis_hash=proposed1.hypothesis_hash,
        evidence_artifact_ref_ids=(ref1_c1,),
        reason_code="test",
        reason_text="test reason",
        idempotency_key="canvas-1-key-c1",
        created_at=_now(),
    )
    store.append_conflict_record(conflict1)

    prior_v2 = _knowledge_version(canvas_id="canvas-2", knowledge_version_id="prior-v2", execution_run_id="exec-2", analysis_run_id="analysis-2", hypothesis="prior2", artifact_refs=(ref1_c2, ref2_c2))
    prior2, _ = store.append_knowledge_version(prior_v2)

    proposed_v2 = _knowledge_version(canvas_id="canvas-2", knowledge_version_id="proposed-v2", execution_run_id="exec-2", analysis_run_id="analysis-2", idempotency_key="canvas-2-key-proposed-2", hypothesis="proposed2", artifact_refs=(ref1_c2, ref2_c2))
    proposed2, _ = store.append_knowledge_version(proposed_v2)

    conflict2 = ConflictRecord(
        conflict_id="conflict-2",
        canvas_id="canvas-2",
        prior_knowledge_version_id=prior2.knowledge_version_id,
        proposed_knowledge_version_id=proposed2.knowledge_version_id,
        old_hypothesis_hash=prior2.hypothesis_hash,
        new_hypothesis_hash=proposed2.hypothesis_hash,
        evidence_artifact_ref_ids=(ref1_c2,),
        reason_code="test",
        reason_text="test reason",
        idempotency_key="canvas-2-key-c2",
        created_at=_now(),
    )
    store.append_conflict_record(conflict2)

    found1 = store.get_conflict_record("canvas-1", "conflict-1")
    assert found1 is not None

    not_found = store.get_conflict_record("canvas-1", "conflict-2")
    assert not_found is None


def test_list_conflict_records_canvas_scoped(store: StateStore) -> None:
    """List only returns conflicts from the given canvas."""
    _setup_execution_run(store, canvas_id="canvas-1")
    _setup_analysis_run(store, canvas_id="canvas-1")
    ref1_c1, ref2_c1 = _setup_artifact_refs(store, canvas_id="canvas-1")

    _setup_execution_run(store, canvas_id="canvas-2", execution_run_id="exec-2")
    _setup_analysis_run(store, canvas_id="canvas-2", execution_run_id="exec-2", analysis_run_id="analysis-2")
    ref1_c2, ref2_c2 = _setup_artifact_refs(store, canvas_id="canvas-2", execution_run_id="exec-2")

    prior_v1 = _knowledge_version(canvas_id="canvas-1", knowledge_version_id="prior-v1", hypothesis="prior1", artifact_refs=(ref1_c1, ref2_c1))
    prior1, _ = store.append_knowledge_version(prior_v1)
    proposed_v1 = _knowledge_version(canvas_id="canvas-1", knowledge_version_id="proposed-v1", idempotency_key="canvas-1-key-proposed-1", hypothesis="proposed1", artifact_refs=(ref1_c1, ref2_c1))
    proposed1, _ = store.append_knowledge_version(proposed_v1)

    conflict1 = ConflictRecord(
        conflict_id="conflict-1",
        canvas_id="canvas-1",
        prior_knowledge_version_id=prior1.knowledge_version_id,
        proposed_knowledge_version_id=proposed1.knowledge_version_id,
        old_hypothesis_hash=prior1.hypothesis_hash,
        new_hypothesis_hash=proposed1.hypothesis_hash,
        evidence_artifact_ref_ids=(ref1_c1,),
        reason_code="test",
        reason_text="test",
        idempotency_key="canvas-1-key-1",
        created_at=_now(),
    )
    store.append_conflict_record(conflict1)

    prior_v2 = _knowledge_version(canvas_id="canvas-2", knowledge_version_id="prior-v2", execution_run_id="exec-2", analysis_run_id="analysis-2", hypothesis="prior2", artifact_refs=(ref1_c2, ref2_c2))
    prior2, _ = store.append_knowledge_version(prior_v2)
    proposed_v2 = _knowledge_version(canvas_id="canvas-2", knowledge_version_id="proposed-v2", execution_run_id="exec-2", analysis_run_id="analysis-2", idempotency_key="canvas-2-key-proposed-2", hypothesis="proposed2", artifact_refs=(ref1_c2, ref2_c2))
    proposed2, _ = store.append_knowledge_version(proposed_v2)

    conflict2 = ConflictRecord(
        conflict_id="conflict-2",
        canvas_id="canvas-2",
        prior_knowledge_version_id=prior2.knowledge_version_id,
        proposed_knowledge_version_id=proposed2.knowledge_version_id,
        old_hypothesis_hash=prior2.hypothesis_hash,
        new_hypothesis_hash=proposed2.hypothesis_hash,
        evidence_artifact_ref_ids=(ref1_c2,),
        reason_code="test",
        reason_text="test",
        idempotency_key="canvas-2-key-2",
        created_at=_now(),
    )
    store.append_conflict_record(conflict2)

    canvas1_conflicts = store.list_conflict_records("canvas-1")
    assert len(canvas1_conflicts) == 1
    assert canvas1_conflicts[0].conflict_id == "conflict-1"

    canvas2_conflicts = store.list_conflict_records("canvas-2")
    assert len(canvas2_conflicts) == 1
    assert canvas2_conflicts[0].conflict_id == "conflict-2"
