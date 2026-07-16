"""Append-only storage and current-hash lookup for in-silico results."""

from __future__ import annotations

import json
import sqlite3

from lab_agent.models.validation import InSilicoResult, hash_validation_result
from lab_agent.state.gate_evidence import GateEvidenceConflictError, canonical_evidence_json
from lab_agent.state.models import Clock


def append(
    conn: sqlite3.Connection, *, clock: Clock, canvas_id: str, result: InSilicoResult
) -> tuple[InSilicoResult, bool]:
    """Append ``result`` or return an exact replay; reject changed durable IDs."""

    payload_json = canonical_evidence_json(result)
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            "SELECT result_json FROM in_silico_results WHERE canvas_id=? AND validation_id=?",
            (canvas_id, result.validation_id),
        ).fetchone()
        if existing is not None:
            if existing["result_json"] == payload_json:
                conn.execute("COMMIT")
                return result, False
            raise GateEvidenceConflictError("validation identity has incompatible evidence")
        _insert(conn, canvas_id=canvas_id, result=result, payload_json=payload_json, recorded_at=clock())
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return result, True


def get(conn: sqlite3.Connection, *, canvas_id: str, validation_id: str) -> InSilicoResult | None:
    row = conn.execute(
        "SELECT result_json FROM in_silico_results WHERE canvas_id=? AND validation_id=?",
        (canvas_id, validation_id),
    ).fetchone()
    return None if row is None else InSilicoResult.model_validate_json(row["result_json"])


def get_current(
    conn: sqlite3.Connection, *, canvas_id: str, proposal_hash: str, result_hash: str
) -> InSilicoResult | None:
    row = conn.execute(
        "SELECT result_json FROM in_silico_results "
        "WHERE canvas_id=? AND proposal_hash=? AND result_hash=?",
        (canvas_id, proposal_hash, result_hash),
    ).fetchone()
    return None if row is None else InSilicoResult.model_validate_json(row["result_json"])


def list_results(
    conn: sqlite3.Connection, *, canvas_id: str, proposal_hash: str | None = None
) -> list[InSilicoResult]:
    if proposal_hash is None:
        rows = conn.execute(
            "SELECT result_json FROM in_silico_results WHERE canvas_id=? "
            "ORDER BY julianday(completed_at), validation_id",
            (canvas_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT result_json FROM in_silico_results WHERE canvas_id=? AND proposal_hash=? "
            "ORDER BY julianday(completed_at), validation_id",
            (canvas_id, proposal_hash),
        ).fetchall()
    return [InSilicoResult.model_validate_json(row["result_json"]) for row in rows]


def _insert(
    conn: sqlite3.Connection,
    *,
    canvas_id: str,
    result: InSilicoResult,
    payload_json: str,
    recorded_at: float,
) -> None:
    conn.execute(
        "INSERT INTO in_silico_results "
        "(validation_id, canvas_id, proposal_hash, result_hash, decision, predicted_outcome, confidence, "
        "uncertainty, assumptions_json, risk_flags_json, recommended_changes_json, adapter_name, "
        "adapter_version, algorithm_version, mode, completed_at, result_json, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            result.validation_id,
            canvas_id,
            result.proposal_hash,
            hash_validation_result(result),
            result.decision.value,
            result.predicted_outcome,
            result.confidence,
            result.uncertainty,
            json.dumps(result.assumptions),
            json.dumps(result.risk_flags),
            json.dumps(result.recommended_changes),
            result.adapter_name,
            result.adapter_version,
            result.algorithm_version,
            result.mode.value,
            result.completed_at.isoformat(),
            payload_json,
            recorded_at,
        ),
    )


__all__ = ["append", "get", "get_current", "list_results"]
