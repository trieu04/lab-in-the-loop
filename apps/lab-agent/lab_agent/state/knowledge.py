"""Append-only canvas-scoped artifact references, knowledge versions, and conflicts."""
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
    """A lineage reference does not belong to the requesting canvas."""
class KnowledgeLineageError(RuntimeError):
    """A referenced owner, artifact, or evidence basis is invalid for the write."""

def append_artifact_ref(conn: sqlite3.Connection, *, artifact_ref: ArtifactRef) -> tuple[ArtifactRef, bool]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute("SELECT * FROM artifact_refs WHERE artifact_ref_id=?", (artifact_ref.artifact_ref_id,)).fetchone()
        if existing is not None:
            result = artifact_from_row(existing)
            if result.canvas_id != artifact_ref.canvas_id:
                raise KnowledgeScopeError("artifact reference belongs to another canvas")
            if dump_model(result) != dump_model(artifact_ref):
                raise KnowledgeConflictError("artifact reference identity has incompatible content")
            conn.execute("COMMIT")
            return result, False
        _require_artifact_owner(conn, artifact_ref)
        conn.execute("INSERT INTO artifact_refs (artifact_ref_id,canvas_id,execution_run_id,analysis_run_id,role,evidence_kind,content_hash,logical_uri,media_type,classification,retention_until,recorded_at,measured_receipt_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (artifact_ref.artifact_ref_id, artifact_ref.canvas_id, artifact_ref.execution_run_id, artifact_ref.analysis_run_id, artifact_ref.role.value, artifact_ref.evidence_kind.value, artifact_ref.content_hash, artifact_ref.logical_uri, artifact_ref.media_type, artifact_ref.classification, artifact_ref.retention_until.isoformat(), artifact_ref.recorded_at.isoformat(), None if artifact_ref.measured_receipt is None else dump_model(artifact_ref.measured_receipt)))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return artifact_ref, True

def get_artifact_ref(conn: sqlite3.Connection, *, canvas_id: str, artifact_ref_id: str) -> ArtifactRef | None:
    row = conn.execute("SELECT * FROM artifact_refs WHERE canvas_id=? AND artifact_ref_id=?", (canvas_id, artifact_ref_id)).fetchone()
    return None if row is None else artifact_from_row(row)

def list_artifact_refs(conn: sqlite3.Connection, *, canvas_id: str, execution_run_id: str | None = None, analysis_run_id: str | None = None) -> list[ArtifactRef]:
    sql, values = "SELECT * FROM artifact_refs WHERE canvas_id=?", [canvas_id]
    if execution_run_id is not None:
        sql += " AND execution_run_id=?"
        values.append(execution_run_id)
    if analysis_run_id is not None:
        sql += " AND analysis_run_id=?"
        values.append(analysis_run_id)
    return [artifact_from_row(row) for row in conn.execute(sql + " ORDER BY recorded_at", values)]

def append_knowledge_version(conn: sqlite3.Connection, *, version: KnowledgeVersion) -> tuple[KnowledgeVersion, bool]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute("SELECT * FROM knowledge_versions WHERE idempotency_key=?", (version.idempotency_key,)).fetchone()
        if existing is not None:
            result = knowledge_from_row(existing)
            if result.canvas_id != version.canvas_id:
                raise KnowledgeScopeError("knowledge key belongs to another canvas")
            if dump_model(result) != dump_model(version):
                raise KnowledgeConflictError("knowledge idempotency key has incompatible content")
            conn.execute("COMMIT")
            return result, False
        same_id = conn.execute("SELECT canvas_id FROM knowledge_versions WHERE knowledge_version_id=?", (version.knowledge_version_id,)).fetchone()
        if same_id is not None:
            if same_id["canvas_id"] != version.canvas_id:
                raise KnowledgeScopeError("knowledge version belongs to another canvas")
            raise KnowledgeConflictError("knowledge version identity already exists")
        _require_version_lineage(conn, version)
        conn.execute("INSERT INTO knowledge_versions (knowledge_version_id,canvas_id,execution_run_id,analysis_run_id,proposal_hash,hypothesis,hypothesis_hash,evidence_kind,provenance_ref_ids_json,payload_json,content_hash,idempotency_key,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (version.knowledge_version_id, version.canvas_id, version.execution_run_id, version.analysis_run_id, version.proposal_hash, version.hypothesis, version.hypothesis_hash, version.evidence_kind.value, dump_json(version.provenance_artifact_ref_ids), version.payload, version.content_hash, version.idempotency_key, version.created_at.isoformat()))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return version, True

def get_knowledge_version(conn: sqlite3.Connection, *, canvas_id: str, knowledge_version_id: str) -> KnowledgeVersion | None:
    row = conn.execute("SELECT * FROM knowledge_versions WHERE canvas_id=? AND knowledge_version_id=?", (canvas_id, knowledge_version_id)).fetchone()
    return None if row is None else knowledge_from_row(row)

def list_knowledge_versions(conn: sqlite3.Connection, *, canvas_id: str) -> list[KnowledgeVersion]:
    return [knowledge_from_row(row) for row in conn.execute("SELECT * FROM knowledge_versions WHERE canvas_id=? ORDER BY created_at", (canvas_id,))]

def append_conflict_record(conn: sqlite3.Connection, *, conflict: ConflictRecord) -> tuple[ConflictRecord, bool]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute("SELECT * FROM conflict_records WHERE idempotency_key=?", (conflict.idempotency_key,)).fetchone()
        if existing is not None:
            result = conflict_from_row(existing)
            if result.canvas_id != conflict.canvas_id:
                raise KnowledgeScopeError("conflict key belongs to another canvas")
            if dump_model(result) != dump_model(conflict):
                raise KnowledgeConflictError("conflict idempotency key has incompatible content")
            conn.execute("COMMIT")
            return result, False
        _require_conflict_lineage(conn, conflict)
        conn.execute("INSERT INTO conflict_records (conflict_id,canvas_id,prior_knowledge_version_id,proposed_knowledge_version_id,old_hypothesis_hash,new_hypothesis_hash,evidence_ref_ids_json,reason_code,reason_text,idempotency_key,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (conflict.conflict_id, conflict.canvas_id, conflict.prior_knowledge_version_id, conflict.proposed_knowledge_version_id, conflict.old_hypothesis_hash, conflict.new_hypothesis_hash, dump_json(conflict.evidence_artifact_ref_ids), conflict.reason_code, conflict.reason_text, conflict.idempotency_key, conflict.created_at.isoformat()))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return conflict, True

def get_conflict_record(conn: sqlite3.Connection, *, canvas_id: str, conflict_id: str) -> ConflictRecord | None:
    row = conn.execute("SELECT * FROM conflict_records WHERE canvas_id=? AND conflict_id=?", (canvas_id, conflict_id)).fetchone()
    return None if row is None else conflict_from_row(row)

def list_conflict_records(conn: sqlite3.Connection, *, canvas_id: str) -> list[ConflictRecord]:
    return [conflict_from_row(row) for row in conn.execute("SELECT * FROM conflict_records WHERE canvas_id=? ORDER BY created_at", (canvas_id,))]

def _require_artifact_owner(conn: sqlite3.Connection, artifact_ref: ArtifactRef) -> None:
    table, key = ("execution_runs", "execution_run_id") if artifact_ref.execution_run_id else ("analysis_runs", "analysis_run_id")
    owner_id = artifact_ref.execution_run_id or artifact_ref.analysis_run_id
    row = conn.execute(f"SELECT evidence_kind FROM {table} WHERE canvas_id=? AND {key}=?", (artifact_ref.canvas_id, owner_id)).fetchone()
    if row is None:
        raise KnowledgeLineageError("artifact owner does not exist on this canvas")
    if row["evidence_kind"] != artifact_ref.evidence_kind.value:
        raise KnowledgeLineageError("artifact evidence kind differs from owner run")

def _require_version_lineage(conn: sqlite3.Connection, version: KnowledgeVersion) -> None:
    rows = conn.execute("SELECT evidence_kind FROM execution_runs WHERE canvas_id=? AND execution_run_id=? UNION ALL SELECT evidence_kind FROM analysis_runs WHERE canvas_id=? AND analysis_run_id=?", (version.canvas_id, version.execution_run_id, version.canvas_id, version.analysis_run_id)).fetchall()
    if len(rows) != 2 or any(row["evidence_kind"] != version.evidence_kind.value for row in rows):
        raise KnowledgeLineageError("knowledge source runs are missing or have mixed evidence")
    _require_refs(conn, canvas_id=version.canvas_id, ref_ids=version.provenance_artifact_ref_ids, kind=version.evidence_kind)

def _require_conflict_lineage(conn: sqlite3.Connection, conflict: ConflictRecord) -> None:
    rows = conn.execute("SELECT knowledge_version_id, hypothesis_hash, evidence_kind FROM knowledge_versions WHERE canvas_id=? AND knowledge_version_id IN (?, ?)", (conflict.canvas_id, conflict.prior_knowledge_version_id, conflict.proposed_knowledge_version_id)).fetchall()
    if len(rows) != 2:
        raise KnowledgeLineageError("conflict versions are missing or cross-canvas")
    hashes = {row["knowledge_version_id"]: row["hypothesis_hash"] for row in rows}
    hashes_match = hashes[conflict.prior_knowledge_version_id] == conflict.old_hypothesis_hash
    hashes_match &= hashes[conflict.proposed_knowledge_version_id] == conflict.new_hypothesis_hash
    if not hashes_match:
        raise KnowledgeLineageError("conflict hypothesis hashes do not match versions")
    _require_refs(conn, canvas_id=conflict.canvas_id, ref_ids=conflict.evidence_artifact_ref_ids, kind=EvidenceKind(rows[0]["evidence_kind"]))

def _require_refs(conn: sqlite3.Connection, *, canvas_id: str, ref_ids: tuple[str, ...], kind: EvidenceKind) -> None:
    marks = ",".join("?" for _ in ref_ids)
    rows = conn.execute(f"SELECT evidence_kind FROM artifact_refs WHERE canvas_id=? AND artifact_ref_id IN ({marks})", [canvas_id, *ref_ids]).fetchall()
    refs_match = len(rows) == len(set(ref_ids))
    refs_match &= all(row["evidence_kind"] == kind.value for row in rows)
    if not refs_match:
        raise KnowledgeLineageError("artifact references are missing or have mixed evidence")

__all__ = ["KnowledgeConflictError", "KnowledgeLineageError", "KnowledgeScopeError", "append_artifact_ref", "append_conflict_record", "append_knowledge_version", "get_artifact_ref", "get_conflict_record", "get_knowledge_version", "list_artifact_refs", "list_conflict_records", "list_knowledge_versions"]
