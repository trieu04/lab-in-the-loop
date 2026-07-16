"""Safe Browser payloads for the Phase 8 dry-run lifecycle."""

from __future__ import annotations

from lab_agent.models.execution import (
    AnalysisRun,
    ArtifactRef,
    ConflictRecord,
    EvidenceKind,
    ExecutionRun,
    KnowledgeVersion,
)

DRY_RUN_LABEL = "DRY RUN / MOCK — NOT MEASURED"


def _base(title: str, evidence_kind: EvidenceKind, *, round_index: int) -> dict[str, object]:
    return {
        "title": title,
        "round": round_index,
        "evidence_label": DRY_RUN_LABEL,
        "evidence_kind": evidence_kind.value,
        "execution_enabled": False,
    }


def _refs(refs: tuple[ArtifactRef, ...]) -> tuple[dict[str, str], ...]:
    return tuple(
        {
            "artifact_ref_id": ref.artifact_ref_id,
            "content_hash": ref.content_hash,
            "role": ref.role.value,
            "evidence_kind": ref.evidence_kind.value,
        }
        for ref in refs
    )


def execution_payload(run: ExecutionRun, refs: tuple[ArtifactRef, ...]) -> dict[str, object]:
    """Project a persisted execution without provider bodies or arbitrary errors."""

    payload = _base("[EXP:Execution]", run.evidence_kind, round_index=run.round_index)
    payload["execution"] = {
        "execution_run_id": run.execution_run_id,
        "setup_id": run.setup_id,
        "proposal_hash": run.proposal_hash,
        "validation_result_hash": run.validation_result_hash,
        "input_hash": run.input_hash,
        "adapter": f"{run.adapter_name}:{run.adapter_version}",
        "mode": run.mode.value,
        "status": run.status.value,
        "failure_code": run.failure_code.value if run.failure_code else None,
        "provider_execution_id": run.provider_execution_id,
        "rerun_of_execution_id": run.rerun_of_execution_id,
    }
    payload["evidence_refs"] = _refs(refs)
    return payload


def analysis_payload(run: AnalysisRun, refs: tuple[ArtifactRef, ...], *, round_index: int) -> dict[str, object]:
    """Project retained source/derived lineage, never external analysis content."""

    payload = _base("[EXP:Analysis]", run.evidence_kind, round_index=round_index)
    payload["analysis"] = {
        "analysis_run_id": run.analysis_run_id,
        "execution_run_id": run.execution_run_id,
        "input_hash": run.input_hash,
        "source_artifact_ref_ids": run.source_artifact_ref_ids,
        "adapter": f"{run.adapter_name}:{run.adapter_version}",
        "mode": run.mode.value,
        "status": run.status.value,
        "failure_code": run.failure_code.value if run.failure_code else None,
        "provider_job_id": run.provider_job_id,
        "rerun_of_analysis_id": run.rerun_of_analysis_id,
    }
    payload["evidence_refs"] = _refs(refs)
    return payload


def knowledge_payload(version: KnowledgeVersion, *, round_index: int) -> dict[str, object]:
    """Project the immutable mock interpretation and its explicit provenance."""

    payload = _base("[EXP:Knowledge]", version.evidence_kind, round_index=round_index)
    payload["knowledge"] = {
        "knowledge_version_id": version.knowledge_version_id,
        "execution_run_id": version.execution_run_id,
        "analysis_run_id": version.analysis_run_id,
        "proposal_hash": version.proposal_hash,
        "hypothesis_hash": version.hypothesis_hash,
        "content_hash": version.content_hash,
        "provenance_artifact_ref_ids": version.provenance_artifact_ref_ids,
        "interpretation": version.payload,
    }
    return payload


def conflict_payload(conflict: ConflictRecord, *, round_index: int) -> dict[str, object]:
    """Project preserved conflict lineage without pretending to resolve it."""

    payload = _base("[EXP:Conflict]", EvidenceKind.MOCK_OR_DRY_RUN, round_index=round_index)
    payload["conflict"] = {
        "conflict_id": conflict.conflict_id,
        "prior_knowledge_version_id": conflict.prior_knowledge_version_id,
        "proposed_knowledge_version_id": conflict.proposed_knowledge_version_id,
        "old_hypothesis_hash": conflict.old_hypothesis_hash,
        "new_hypothesis_hash": conflict.new_hypothesis_hash,
        "evidence_artifact_ref_ids": conflict.evidence_artifact_ref_ids,
        "reason_code": conflict.reason_code,
        "reason_text": conflict.reason_text,
    }
    return payload


__all__ = ["DRY_RUN_LABEL", "analysis_payload", "conflict_payload", "execution_payload", "knowledge_payload"]
