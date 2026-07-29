"""Deterministic, append-only interpretation and knowledge preservation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from lab_agent.artifact_lifecycle_payloads import DRY_RUN_LABEL
from lab_agent.integrations.knowledge import KnowledgeAdapter
from lab_agent.models.execution import (
    ArtifactRef,
    ConflictRecord,
    EvidenceKind,
    InterpretationDisposition,
    InterpretationResult,
    KnowledgeVersion,
)
from lab_agent.state_store import StateStore


class KnowledgeUpdateError(RuntimeError):
    """Typed interpretation or append-only knowledge constraints were violated."""


@dataclass(frozen=True)
class KnowledgeUpdateOutcome:
    """Canonical durable records to project only after successful persistence."""

    interpretation: InterpretationResult
    version: KnowledgeVersion
    conflict: ConflictRecord | None


def _key(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


def interpret_dry_run(
    *,
    tenant_id: str,
    canvas_id: str,
    execution_run_id: str,
    analysis_run_id: str,
    original_hypothesis: str,
    refs: tuple[ArtifactRef, ...],
    prior_knowledge_version_id: str | None,
) -> InterpretationResult:
    """Create a bounded mock interpretation from explicit retained references."""

    if not original_hypothesis.strip() or len(original_hypothesis) > 2000:
        raise KnowledgeUpdateError("a bounded original hypothesis is required")
    if not refs or any(ref.tenant_id != tenant_id or ref.canvas_id != canvas_id for ref in refs):
        raise KnowledgeUpdateError("interpretation references must be current-tenant canvas evidence")
    if any(ref.evidence_kind is not EvidenceKind.MOCK_OR_DRY_RUN for ref in refs):
        raise KnowledgeUpdateError("mixed or measured evidence is unavailable in dry-run interpretation")
    ref_ids = tuple(ref.artifact_ref_id for ref in refs)
    hypothesis_hash = hashlib.sha256(original_hypothesis.encode("utf-8")).hexdigest()
    disposition = (
        InterpretationDisposition.CONFLICT
        if prior_knowledge_version_id is not None
        else InterpretationDisposition.APPEND
    )
    interpretation = (
        f"{DRY_RUN_LABEL}. Deterministic interpretation derived from retained mock refs "
        f"({len(ref_ids)}); no scientific conclusion or conflict resolution is claimed."
    )
    return InterpretationResult(
        interpretation_id=_key("interpretation", tenant_id, execution_run_id, analysis_run_id, *ref_ids),
        tenant_id=tenant_id,
        canvas_id=canvas_id,
        execution_run_id=execution_run_id,
        analysis_run_id=analysis_run_id,
        source_artifact_ref_ids=ref_ids,
        original_hypothesis=original_hypothesis,
        original_hypothesis_hash=hypothesis_hash,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        caveats=(DRY_RUN_LABEL, "No scientific conclusion or conflict resolution is claimed."),
        interpretation=interpretation,
        disposition=disposition,
        prior_knowledge_version_id=prior_knowledge_version_id,
    )


async def append_interpretation(
    store: StateStore,
    adapter: KnowledgeAdapter,
    *,
    interpretation: InterpretationResult,
    proposal_hash: str,
) -> KnowledgeUpdateOutcome:
    """Append canonical version/conflict records before adapter or Browser projection."""

    if interpretation.tenant_id != store.tenant_id:
        raise KnowledgeUpdateError("interpretation tenant does not match StateStore tenant")
    if interpretation.evidence_kind is not EvidenceKind.MOCK_OR_DRY_RUN:
        raise KnowledgeUpdateError("Phase 8 knowledge accepts mock_or_dry_run evidence only")
    now = datetime.now(UTC)
    version_key = _key("knowledge", interpretation.interpretation_id, proposal_hash)
    version_id = _key("knowledge-version", version_key)
    version = store.get_knowledge_version(interpretation.canvas_id, version_id)
    if version is None:
        version = KnowledgeVersion(
            knowledge_version_id=version_id,
            tenant_id=interpretation.tenant_id,
            canvas_id=interpretation.canvas_id,
            execution_run_id=interpretation.execution_run_id,
            analysis_run_id=interpretation.analysis_run_id,
            proposal_hash=proposal_hash,
            hypothesis=interpretation.original_hypothesis,
            hypothesis_hash=interpretation.original_hypothesis_hash,
            evidence_kind=interpretation.evidence_kind,
            provenance_artifact_ref_ids=interpretation.source_artifact_ref_ids,
            payload=interpretation.interpretation,
            content_hash=hashlib.sha256(interpretation.interpretation.encode("utf-8")).hexdigest(),
            idempotency_key=version_key,
            created_at=now,
        )
        version, _ = store.append_knowledge_version(version)
    persisted = await adapter.append(version)
    if persisted != version:
        raise KnowledgeUpdateError("knowledge adapter returned incompatible version")
    conflict = _conflict(store, interpretation, version, now)
    if conflict is not None:
        existing_conflict = store.get_conflict_record(interpretation.canvas_id, conflict.conflict_id)
        if existing_conflict is None:
            conflict, _ = store.append_conflict_record(conflict)
        else:
            conflict = existing_conflict
        persisted_conflict = await adapter.record_conflict(conflict)
        if persisted_conflict != conflict:
            raise KnowledgeUpdateError("knowledge adapter returned incompatible conflict")
    return KnowledgeUpdateOutcome(interpretation, version, conflict)


def _conflict(
    store: StateStore,
    interpretation: InterpretationResult,
    version: KnowledgeVersion,
    created_at: datetime,
) -> ConflictRecord | None:
    prior_id = interpretation.prior_knowledge_version_id
    if prior_id is None:
        return None
    prior = store.get_knowledge_version(interpretation.canvas_id, prior_id)
    if prior is None:
        raise KnowledgeUpdateError("requested prior knowledge version is unavailable")
    key = _key("knowledge-conflict", prior.knowledge_version_id, version.knowledge_version_id)
    return ConflictRecord(
        conflict_id=_key("conflict", key),
        tenant_id=interpretation.tenant_id,
        canvas_id=interpretation.canvas_id,
        prior_knowledge_version_id=prior.knowledge_version_id,
        proposed_knowledge_version_id=version.knowledge_version_id,
        old_hypothesis_hash=prior.hypothesis_hash,
        new_hypothesis_hash=version.hypothesis_hash,
        evidence_artifact_ref_ids=interpretation.source_artifact_ref_ids,
        reason_code="dry_run_prior_version_preserved",
        reason_text="Dry-run preservation record; no semantic conflict resolution is claimed.",
        idempotency_key=key,
        created_at=created_at,
    )


__all__ = ["KnowledgeUpdateError", "KnowledgeUpdateOutcome", "append_interpretation", "interpret_dry_run"]
