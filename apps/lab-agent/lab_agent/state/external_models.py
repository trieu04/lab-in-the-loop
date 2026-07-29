"""SQLite row projections and canonical encoders for Phase 8 records."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from pydantic import BaseModel

from lab_agent.models.execution import (
    AnalysisRun,
    ArtifactRef,
    ConflictRecord,
    ExecutionRun,
    KnowledgeVersion,
)


def dump_json(value: object) -> str:
    """Serialize only typed, bounded values in a deterministic representation."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def dump_model(value: BaseModel) -> str:
    return dump_json(value.model_dump(mode="json", exclude_none=True))


def load_json(value: str) -> object:
    return json.loads(value)


def execution_from_row(row: sqlite3.Row) -> ExecutionRun:
    return ExecutionRun.model_validate({
        "tenant_id": row["tenant_id"], "request_id": row["request_id"], "execution_run_id": row["execution_run_id"],
        "canvas_id": row["canvas_id"], "setup_id": row["setup_id"], "round": row["round_index"],
        "proposal_hash": row["proposal_hash"], "validation_result_hash": row["validation_result_hash"],
        "adapter_name": row["adapter_name"], "adapter_version": row["adapter_version"],
        "mode": row["mode"], "evidence_kind": row["evidence_kind"],
        "submit_intent_key": row["submit_intent_key"], "input_hash": row["input_hash"],
        "requested_at": row["created_at"], "rerun_of_execution_id": row["rerun_of_execution_id"],
        "status": row["status"], "abort_intent_key": row["abort_intent_key"],
        "provider_execution_id": row["provider_execution_id"], "submitted_at": row["submitted_at"],
        "finished_at": row["finished_at"], "failure_code": row["failure_code"],
    })


def analysis_from_row(row: sqlite3.Row) -> AnalysisRun:
    return AnalysisRun.model_validate({
        "tenant_id": row["tenant_id"], "request_id": row["request_id"], "analysis_run_id": row["analysis_run_id"],
        "canvas_id": row["canvas_id"], "execution_run_id": row["execution_run_id"],
        "source_artifact_ref_ids": load_json(row["source_artifact_ref_ids_json"]),
        "adapter_name": row["adapter_name"], "adapter_version": row["adapter_version"],
        "mode": row["mode"], "evidence_kind": row["evidence_kind"],
        "submit_intent_key": row["submit_intent_key"], "input_hash": row["input_hash"],
        "requested_at": row["created_at"], "rerun_of_analysis_id": row["rerun_of_analysis_id"],
        "status": row["status"], "abort_intent_key": row["abort_intent_key"],
        "provider_job_id": row["provider_job_id"], "submitted_at": row["submitted_at"],
        "finished_at": row["finished_at"], "failure_code": row["failure_code"],
    })


def artifact_from_row(row: sqlite3.Row) -> ArtifactRef:
    receipt = row["measured_receipt_json"]
    return ArtifactRef.model_validate({
        "tenant_id": row["tenant_id"], "artifact_ref_id": row["artifact_ref_id"], "canvas_id": row["canvas_id"],
        "execution_run_id": row["execution_run_id"], "analysis_run_id": row["analysis_run_id"],
        "content_hash": row["content_hash"], "logical_uri": row["logical_uri"],
        "media_type": row["media_type"], "classification": row["classification"],
        "role": row["role"], "evidence_kind": row["evidence_kind"],
        "retention_until": row["retention_until"], "recorded_at": row["recorded_at"],
        "measured_receipt": None if receipt is None else load_json(receipt),
    })


def knowledge_from_row(row: sqlite3.Row) -> KnowledgeVersion:
    return KnowledgeVersion.model_validate({
        "tenant_id": row["tenant_id"], "knowledge_version_id": row["knowledge_version_id"], "canvas_id": row["canvas_id"],
        "execution_run_id": row["execution_run_id"], "analysis_run_id": row["analysis_run_id"],
        "proposal_hash": row["proposal_hash"], "hypothesis": row["hypothesis"],
        "hypothesis_hash": row["hypothesis_hash"], "evidence_kind": row["evidence_kind"],
        "provenance_artifact_ref_ids": load_json(row["provenance_ref_ids_json"]),
        "payload": row["payload_json"], "content_hash": row["content_hash"],
        "idempotency_key": row["idempotency_key"], "created_at": row["created_at"],
    })


def conflict_from_row(row: sqlite3.Row) -> ConflictRecord:
    return ConflictRecord.model_validate({
        "tenant_id": row["tenant_id"], "conflict_id": row["conflict_id"], "canvas_id": row["canvas_id"],
        "prior_knowledge_version_id": row["prior_knowledge_version_id"],
        "proposed_knowledge_version_id": row["proposed_knowledge_version_id"],
        "old_hypothesis_hash": row["old_hypothesis_hash"], "new_hypothesis_hash": row["new_hypothesis_hash"],
        "evidence_artifact_ref_ids": load_json(row["evidence_ref_ids_json"]),
        "reason_code": row["reason_code"], "reason_text": row["reason_text"],
        "idempotency_key": row["idempotency_key"], "created_at": row["created_at"],
    })


def iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


__all__ = [
    "analysis_from_row", "artifact_from_row", "conflict_from_row", "dump_json", "dump_model",
    "execution_from_row", "iso", "knowledge_from_row", "load_json",
]
