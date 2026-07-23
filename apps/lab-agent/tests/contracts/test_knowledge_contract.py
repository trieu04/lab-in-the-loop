"""Knowledge adapter contract tests: typed requests/results, idempotency, append-only, conflict preservation, no secrets/raw bodies."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from lab_agent.integrations.knowledge import (
    DeterministicKnowledgeAdapter,
    KnowledgeAdapter,
    KnowledgeAdapterError,
    KnowledgeNotReadyError,
)
from lab_agent.models.execution import (
    ConflictRecord,
    EvidenceKind,
    ExternalFailureCode,
    KnowledgeVersion,
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _knowledge_version(
    knowledge_version_id: str = "kv-1",
    canvas_id: str = "canvas-1",
    execution_run_id: str = "exec-1",
    analysis_run_id: str = "analysis-1",
    idempotency_key: str = "key-1",
    hypothesis: str = "hypothesis",
    payload_text: str = "knowledge content",
    created_at: datetime | None = None,
) -> KnowledgeVersion:
    if created_at is None:
        created_at = datetime.now(UTC)
    return KnowledgeVersion(
        knowledge_version_id=knowledge_version_id,
        canvas_id=canvas_id,
        execution_run_id=execution_run_id,
        analysis_run_id=analysis_run_id,
        proposal_hash=_hash("proposal"),
        hypothesis=hypothesis,
        hypothesis_hash=_hash(hypothesis),
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        provenance_artifact_ref_ids=("ref-1", "ref-2"),
        payload=payload_text,
        content_hash=_hash(payload_text),
        idempotency_key=idempotency_key,
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_knowledge_adapter_is_protocol() -> None:
    """Adapter protocol is runtime-checkable and verifiable."""
    adapter = DeterministicKnowledgeAdapter()
    assert isinstance(adapter, KnowledgeAdapter)


@pytest.mark.asyncio
async def test_append_returns_typed_knowledge_version() -> None:
    """Append returns a typed KnowledgeVersion with stable idempotency key."""
    adapter = DeterministicKnowledgeAdapter()
    version = _knowledge_version()

    result = await adapter.append(version)

    assert result.knowledge_version_id == version.knowledge_version_id
    assert result.canvas_id == version.canvas_id
    assert result.idempotency_key == version.idempotency_key
    assert result.evidence_kind is EvidenceKind.MOCK_OR_DRY_RUN
    assert len(result.content_hash) == 64  # SHA-256


@pytest.mark.asyncio
async def test_idempotent_append_converges() -> None:
    """Same idempotency key returns same version; different payload fails."""
    adapter = DeterministicKnowledgeAdapter()
    now = datetime.now(UTC)
    version1 = _knowledge_version(
        knowledge_version_id="kv-1",
        idempotency_key="key-1",
        created_at=now,
    )

    result1 = await adapter.append(version1)

    # Same key, same payload → same version (use same created_at for idempotency)
    version2 = _knowledge_version(
        knowledge_version_id="kv-1",
        idempotency_key="key-1",
        created_at=now,
    )
    result2 = await adapter.append(version2)
    assert result2.knowledge_version_id == result1.knowledge_version_id

    # Same key, different payload → error
    version3 = _knowledge_version(
        knowledge_version_id="kv-1",
        idempotency_key="key-1",
        canvas_id="canvas-2",
        created_at=now,
    )
    with pytest.raises(KnowledgeAdapterError) as exc:
        await adapter.append(version3)
    assert exc.value.code is ExternalFailureCode.INVALID_SCHEMA


@pytest.mark.asyncio
async def test_append_only_history() -> None:
    """Versions are immutable; later versions do not overwrite earlier ones."""
    adapter = DeterministicKnowledgeAdapter()
    now = datetime.now(UTC)
    version1 = _knowledge_version(
        knowledge_version_id="v1",
        idempotency_key="key-v1",
        created_at=now,
    )
    v1 = await adapter.append(version1)

    # Append a different version
    version2 = _knowledge_version(
        knowledge_version_id="v2",
        idempotency_key="key-v2",
        hypothesis="hypothesis v2",
        payload_text="v2 content",
        created_at=now,
    )
    v2 = await adapter.append(version2)

    # Query both
    queried_v1 = await adapter.find_by_idempotency_key("key-v1")
    queried_v2 = await adapter.find_by_idempotency_key("key-v2")

    assert queried_v1 is not None
    assert queried_v2 is not None
    assert queried_v1.knowledge_version_id == "v1"
    assert queried_v2.knowledge_version_id == "v2"
    assert queried_v1.hypothesis == v1.hypothesis
    assert queried_v2.hypothesis == v2.hypothesis


@pytest.mark.asyncio
async def test_conflict_preserves_both_sides() -> None:
    """Conflict record preserves old and new versions plus evidence refs."""
    adapter = DeterministicKnowledgeAdapter()
    now = datetime.now(UTC)

    prior_hyp = "prior hypothesis"
    prior_version = _knowledge_version(
        knowledge_version_id="prior-v",
        idempotency_key="prior-key",
        hypothesis=prior_hyp,
        created_at=now,
    )
    prior = await adapter.append(prior_version)

    proposed_hyp = "proposed hypothesis"
    proposed_version = _knowledge_version(
        knowledge_version_id="proposed-v",
        idempotency_key="proposed-key",
        hypothesis=proposed_hyp,
        created_at=now,
    )
    proposed = await adapter.append(proposed_version)

    conflict = ConflictRecord(
        conflict_id="conflict-1",
        canvas_id="canvas-1",
        prior_knowledge_version_id=prior.knowledge_version_id,
        proposed_knowledge_version_id=proposed.knowledge_version_id,
        old_hypothesis_hash=_hash(prior_hyp),
        new_hypothesis_hash=_hash(proposed_hyp),
        evidence_artifact_ref_ids=("ref-1", "ref-2"),
        reason_code="divergent_paths",
        reason_text="divergent_paths reason",
        idempotency_key="conflict-key-1",
        created_at=now,
    )

    recorded = await adapter.record_conflict(conflict)

    assert recorded.prior_knowledge_version_id == prior.knowledge_version_id
    assert recorded.proposed_knowledge_version_id == proposed.knowledge_version_id
    assert "ref-1" in recorded.evidence_artifact_ref_ids
    assert len(recorded.old_hypothesis_hash) == 64
    assert len(recorded.new_hypothesis_hash) == 64


@pytest.mark.asyncio
async def test_conflict_idempotent() -> None:
    """Same conflict idempotency key returns same record."""
    adapter = DeterministicKnowledgeAdapter()
    now = datetime.now(UTC)

    prior_hyp = "prior"
    version1 = _knowledge_version(
        knowledge_version_id="v-prior",
        idempotency_key="v-prior-key",
        hypothesis=prior_hyp,
        created_at=now,
    )
    prior = await adapter.append(version1)

    proposed_hyp = "proposed"
    version2 = _knowledge_version(
        knowledge_version_id="v-proposed",
        idempotency_key="v-proposed-key",
        hypothesis=proposed_hyp,
        created_at=now,
    )
    proposed = await adapter.append(version2)

    conflict1_data = ConflictRecord(
        conflict_id="conflict-1",
        canvas_id="canvas-1",
        prior_knowledge_version_id=prior.knowledge_version_id,
        proposed_knowledge_version_id=proposed.knowledge_version_id,
        old_hypothesis_hash=_hash(prior_hyp),
        new_hypothesis_hash=_hash(proposed_hyp),
        evidence_artifact_ref_ids=("ref-1",),
        reason_code="divergent_paths",
        reason_text="divergent_paths reason",
        idempotency_key="conflict-key",
        created_at=now,
    )
    conflict1 = await adapter.record_conflict(conflict1_data)

    # Same key → same conflict
    conflict2_data = ConflictRecord(
        conflict_id="conflict-1",
        canvas_id="canvas-1",
        prior_knowledge_version_id=prior.knowledge_version_id,
        proposed_knowledge_version_id=proposed.knowledge_version_id,
        old_hypothesis_hash=_hash(prior_hyp),
        new_hypothesis_hash=_hash(proposed_hyp),
        evidence_artifact_ref_ids=("ref-1",),
        reason_code="divergent_paths",
        reason_text="divergent_paths reason",
        idempotency_key="conflict-key",
        created_at=now,
    )
    conflict2 = await adapter.record_conflict(conflict2_data)

    assert conflict2.conflict_id == conflict1.conflict_id


@pytest.mark.asyncio
async def test_dry_run_only_enforcement() -> None:
    """Dry-run adapter rejects measured evidence with NOT_READY."""
    adapter = DeterministicKnowledgeAdapter()

    # Measured evidence not allowed
    version_measured = _knowledge_version()
    version_with_measured = version_measured.model_copy(
        update={"evidence_kind": EvidenceKind.MEASURED}
    )

    with pytest.raises(KnowledgeNotReadyError):
        await adapter.append(version_with_measured)


@pytest.mark.asyncio
async def test_typed_failures_no_raw_provider_text() -> None:
    """Failures are typed, never raw provider error strings."""
    adapter = DeterministicKnowledgeAdapter()

    version_measured = _knowledge_version()
    version_with_measured = version_measured.model_copy(
        update={"evidence_kind": EvidenceKind.MEASURED}
    )

    with pytest.raises(KnowledgeAdapterError) as exc:
        await adapter.append(version_with_measured)
    assert isinstance(exc.value.code, ExternalFailureCode)
    assert str(exc.value) == exc.value.code.value
