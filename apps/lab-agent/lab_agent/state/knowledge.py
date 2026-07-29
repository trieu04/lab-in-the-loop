"""Append-only tenant- and canvas-scoped artifact, knowledge, and conflict records."""
from __future__ import annotations

import sqlite3

from lab_agent.models.execution import ArtifactRef, ConflictRecord, EvidenceKind, KnowledgeVersion
from lab_agent.state.external_models import (
    artifact_from_row,
    conflict_from_row,
    dump_json,
    dump_model,
    knowledge_from_row,
)


class KnowledgeConflictError(RuntimeError):
    """An immutable identifier or idempotency key was reused with new content."""


class KnowledgeScopeError(RuntimeError):
    """A record or lineage reference does not belong to the requesting tenant and canvas."""


class KnowledgeLineageError(RuntimeError):
    """A referenced owner, artifact, or evidence basis is invalid for the write."""


def append_artifact_ref(conn: sqlite3.Connection, *, tenant_id: str, artifact_ref: ArtifactRef) -> tuple[ArtifactRef, bool]:
    _require_tenant(tenant_id, artifact_ref)
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT * FROM artifact_refs WHERE tenant_id=? AND canvas_id=? AND artifact_ref_id=?",
            (tenant_id, artifact_ref.canvas_id, artifact_ref.artifact_ref_id),
        ).fetchone()
        if existing is not None:
            result = artifact_from_row(existing)
            if dump_model(result) != dump_model(artifact_ref):
                raise KnowledgeConflictError("artifact reference identity has incompatible content")
            conn.execute("COMMIT")
            return result, False
        _require_artifact_owner(conn, tenant_id, artifact_ref)
        conn.execute("INSERT INTO artifact_refs (tenant_id,artifact_ref_id,canvas_id,execution_run_id,analysis_run_id,role,evidence_kind,content_hash,logical_uri,media_type,classification,retention_until,recorded_at,measured_receipt_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (tenant_id, artifact_ref.artifact_ref_id, artifact_ref.canvas_id, artifact_ref.execution_run_id, artifact_ref.analysis_run_id, artifact_ref.role.value, artifact_ref.evidence_kind.value, artifact_ref.content_hash, artifact_ref.logical_uri, artifact_ref.media_type, artifact_ref.classification, artifact_ref.retention_until.isoformat(), artifact_ref.recorded_at.isoformat(), None if artifact_ref.measured_receipt is None else dump_model(artifact_ref.measured_receipt)))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return artifact_ref, True


def get_artifact_ref(conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, artifact_ref_id: str) -> ArtifactRef | None:
    row = conn.execute("SELECT * FROM artifact_refs WHERE tenant_id=? AND canvas_id=? AND artifact_ref_id=?", (tenant_id, canvas_id, artifact_ref_id)).fetchone()
    return None if row is None else artifact_from_row(row)


def list_artifact_refs(conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, execution_run_id: str | None = None, analysis_run_id: str | None = None) -> list[ArtifactRef]:
    sql, values = "SELECT * FROM artifact_refs WHERE tenant_id=? AND canvas_id=?", [tenant_id, canvas_id]
    if execution_run_id is not None:
        sql += " AND execution_run_id=?"
        values.append(execution_run_id)
    if analysis_run_id is not None:
        sql += " AND analysis_run_id=?"
        values.append(analysis_run_id)
    return [artifact_from_row(row) for row in conn.execute(sql + " ORDER BY recorded_at", values)]


def append_knowledge_version(conn: sqlite3.Connection, *, tenant_id: str, version: KnowledgeVersion) -> tuple[KnowledgeVersion, bool]:
    _require_tenant(tenant_id, version)
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT * FROM knowledge_versions WHERE tenant_id=? AND canvas_id=? AND idempotency_key=?",
            (tenant_id, version.canvas_id, version.idempotency_key),
        ).fetchone()
        if existing is not None:
            result = knowledge_from_row(existing)
            if dump_model(result) != dump_model(version):
                raise KnowledgeConflictError("knowledge idempotency key has incompatible content")
            conn.execute("COMMIT")
            return result, False
        same_id = conn.execute(
            "SELECT 1 FROM knowledge_versions WHERE tenant_id=? AND canvas_id=? AND knowledge_version_id=?",
            (tenant_id, version.canvas_id, version.knowledge_version_id),
        ).fetchone()
        if same_id is not None:
            raise KnowledgeConflictError("knowledge version identity already exists")
        _require_version_lineage(conn, tenant_id, version)
        conn.execute("INSERT INTO knowledge_versions (tenant_id,knowledge_version_id,canvas_id,execution_run_id,analysis_run_id,proposal_hash,hypothesis,hypothesis_hash,evidence_kind,provenance_ref_ids_json,payload_json,content_hash,idempotency_key,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (tenant_id, version.knowledge_version_id, version.canvas_id, version.execution_run_id, version.analysis_run_id, version.proposal_hash, version.hypothesis, version.hypothesis_hash, version.evidence_kind.value, dump_json(version.provenance_artifact_ref_ids), version.payload, version.content_hash, version.idempotency_key, version.created_at.isoformat()))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return version, True


def get_knowledge_version(conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, knowledge_version_id: str) -> KnowledgeVersion | None:
    row = conn.execute("SELECT * FROM knowledge_versions WHERE tenant_id=? AND canvas_id=? AND knowledge_version_id=?", (tenant_id, canvas_id, knowledge_version_id)).fetchone()
    return None if row is None else knowledge_from_row(row)


def list_knowledge_versions(conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str) -> list[KnowledgeVersion]:
    return [knowledge_from_row(row) for row in conn.execute("SELECT * FROM knowledge_versions WHERE tenant_id=? AND canvas_id=? ORDER BY created_at", (tenant_id, canvas_id))]


def append_conflict_record(conn: sqlite3.Connection, *, tenant_id: str, conflict: ConflictRecord) -> tuple[ConflictRecord, bool]:
    _require_tenant(tenant_id, conflict)
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT * FROM conflict_records WHERE tenant_id=? AND canvas_id=? AND idempotency_key=?",
            (tenant_id, conflict.canvas_id, conflict.idempotency_key),
        ).fetchone()
        if existing is not None:
            result = conflict_from_row(existing)
            if dump_model(result) != dump_model(conflict):
                raise KnowledgeConflictError("conflict idempotency key has incompatible content")
            conn.execute("COMMIT")
            return result, False
        same_id = conn.execute(
            "SELECT 1 FROM conflict_records WHERE tenant_id=? AND canvas_id=? AND conflict_id=?",
            (tenant_id, conflict.canvas_id, conflict.conflict_id),
        ).fetchone()
        if same_id is not None:
            raise KnowledgeConflictError("conflict identity already exists")
        _require_conflict_lineage(conn, tenant_id, conflict)
        conn.execute("INSERT INTO conflict_records (tenant_id,conflict_id,canvas_id,prior_knowledge_version_id,proposed_knowledge_version_id,old_hypothesis_hash,new_hypothesis_hash,evidence_ref_ids_json,reason_code,reason_text,idempotency_key,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (tenant_id, conflict.conflict_id, conflict.canvas_id, conflict.prior_knowledge_version_id, conflict.proposed_knowledge_version_id, conflict.old_hypothesis_hash, conflict.new_hypothesis_hash, dump_json(conflict.evidence_artifact_ref_ids), conflict.reason_code, conflict.reason_text, conflict.idempotency_key, conflict.created_at.isoformat()))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return conflict, True


def get_conflict_record(conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, conflict_id: str) -> ConflictRecord | None:
    row = conn.execute("SELECT * FROM conflict_records WHERE tenant_id=? AND canvas_id=? AND conflict_id=?", (tenant_id, canvas_id, conflict_id)).fetchone()
    return None if row is None else conflict_from_row(row)


def list_conflict_records(conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str) -> list[ConflictRecord]:
    return [conflict_from_row(row) for row in conn.execute("SELECT * FROM conflict_records WHERE tenant_id=? AND canvas_id=? ORDER BY created_at", (tenant_id, canvas_id))]


def _require_tenant(tenant_id: str, record: ArtifactRef | KnowledgeVersion | ConflictRecord) -> None:
    if not tenant_id or record.tenant_id != tenant_id:
        raise KnowledgeScopeError("record tenant does not match persistence tenant")


def _require_artifact_owner(conn: sqlite3.Connection, tenant_id: str, artifact_ref: ArtifactRef) -> None:
    table, key = ("execution_runs", "execution_run_id") if artifact_ref.execution_run_id else ("analysis_runs", "analysis_run_id")
    owner_id = artifact_ref.execution_run_id or artifact_ref.analysis_run_id
    row = conn.execute(f"SELECT evidence_kind FROM {table} WHERE tenant_id=? AND canvas_id=? AND {key}=?", (tenant_id, artifact_ref.canvas_id, owner_id)).fetchone()
    if row is None or row["evidence_kind"] != artifact_ref.evidence_kind.value:
        raise KnowledgeLineageError("artifact owner is missing or has different evidence")


def _require_version_lineage(conn: sqlite3.Connection, tenant_id: str, version: KnowledgeVersion) -> None:
    rows = conn.execute("SELECT evidence_kind FROM execution_runs WHERE tenant_id=? AND canvas_id=? AND execution_run_id=? UNION ALL SELECT evidence_kind FROM analysis_runs WHERE tenant_id=? AND canvas_id=? AND analysis_run_id=?", (tenant_id, version.canvas_id, version.execution_run_id, tenant_id, version.canvas_id, version.analysis_run_id)).fetchall()
    if len(rows) != 2 or any(row["evidence_kind"] != version.evidence_kind.value for row in rows):
        raise KnowledgeLineageError("knowledge source runs are missing or have mixed evidence")
    _require_refs(conn, tenant_id, version.canvas_id, version.provenance_artifact_ref_ids, version.evidence_kind)


def _require_conflict_lineage(conn: sqlite3.Connection, tenant_id: str, conflict: ConflictRecord) -> None:
    rows = conn.execute("SELECT knowledge_version_id, hypothesis_hash, evidence_kind FROM knowledge_versions WHERE tenant_id=? AND canvas_id=? AND knowledge_version_id IN (?, ?)", (tenant_id, conflict.canvas_id, conflict.prior_knowledge_version_id, conflict.proposed_knowledge_version_id)).fetchall()
    if len(rows) != 2:
        raise KnowledgeLineageError("conflict versions are missing or cross-tenant")
    hashes = {row["knowledge_version_id"]: row["hypothesis_hash"] for row in rows}
    if hashes[conflict.prior_knowledge_version_id] != conflict.old_hypothesis_hash or hashes[conflict.proposed_knowledge_version_id] != conflict.new_hypothesis_hash:
        raise KnowledgeLineageError("conflict hypothesis hashes do not match versions")
    _require_refs(conn, tenant_id, conflict.canvas_id, conflict.evidence_artifact_ref_ids, EvidenceKind(rows[0]["evidence_kind"]))


def _require_refs(conn: sqlite3.Connection, tenant_id: str, canvas_id: str, ref_ids: tuple[str, ...], kind: EvidenceKind) -> None:
    marks = ",".join("?" for _ in ref_ids)
    rows = conn.execute(f"SELECT evidence_kind FROM artifact_refs WHERE tenant_id=? AND canvas_id=? AND artifact_ref_id IN ({marks})", [tenant_id, canvas_id, *ref_ids]).fetchall()
    if len(rows) != len(set(ref_ids)) or any(row["evidence_kind"] != kind.value for row in rows):
        raise KnowledgeLineageError("artifact references are missing or have mixed evidence")


__all__ = ["KnowledgeConflictError", "KnowledgeLineageError", "KnowledgeScopeError", "append_artifact_ref", "append_conflict_record", "append_knowledge_version", "get_artifact_ref", "get_conflict_record", "get_knowledge_version", "list_artifact_refs", "list_conflict_records", "list_knowledge_versions"]
