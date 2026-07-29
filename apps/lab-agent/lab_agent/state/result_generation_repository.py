"""SQLite repository functions for tenant-scoped result generations."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass

from pydantic import ValidationError

from lab_agent.models.experiment import ExperimentResult
from lab_agent.state.models import Clock

_HASH = re.compile(r"^[0-9a-f]{64}$")


class ResultGenerationConflictError(RuntimeError):
    """The same authorized generation key produced incompatible evidence."""


@dataclass(frozen=True)
class ResultGeneration:
    tenant_id: str
    canvas_id: str
    setup_id: str
    round_index: int
    proposal_hash: str
    validation_result_hash: str
    result_hash: str
    result: ExperimentResult
    created_at: float
    updated_at: float


def _canonical(result: ExperimentResult) -> tuple[str, str]:
    encoded = json.dumps(
        result.model_dump(mode="json", exclude_none=False),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_key(
    tenant_id: str,
    canvas_id: str,
    setup_id: str,
    round_index: int,
    proposal_hash: str,
    validation_result_hash: str,
) -> None:
    if not 1 <= len(tenant_id) <= 128 or not 1 <= len(canvas_id) <= 200:
        raise ValueError("result generation tenant or canvas identifier is invalid")
    if not 1 <= len(setup_id) <= 200 or round_index < 1:
        raise ValueError("result generation identifiers are invalid")
    if not _HASH.fullmatch(proposal_hash) or not _HASH.fullmatch(validation_result_hash):
        raise ValueError("result generation authorization hashes are invalid")


def _from_row(row: sqlite3.Row) -> ResultGeneration:
    try:
        result = ExperimentResult.model_validate_json(row["result_json"])
    except ValidationError as exc:
        raise ResultGenerationConflictError("stored result generation is invalid") from exc
    _, digest = _canonical(result)
    if digest != row["result_hash"]:
        raise ResultGenerationConflictError("stored result generation hash is invalid")
    return ResultGeneration(
        tenant_id=row["tenant_id"],
        canvas_id=row["canvas_id"],
        setup_id=row["setup_id"],
        round_index=row["round_index"],
        proposal_hash=row["proposal_hash"],
        validation_result_hash=row["validation_result_hash"],
        result_hash=digest,
        result=result,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get(
    conn: sqlite3.Connection,
    *,
    tenant_id: str,
    canvas_id: str,
    setup_id: str,
    round_index: int,
    proposal_hash: str,
    validation_result_hash: str,
) -> ResultGeneration | None:
    _validate_key(
        tenant_id, canvas_id, setup_id, round_index, proposal_hash, validation_result_hash
    )
    row = conn.execute(
        "SELECT * FROM result_generations WHERE tenant_id=? AND canvas_id=? AND setup_id=? "
        "AND round_index=? AND proposal_hash=? AND validation_result_hash=?",
        (tenant_id, canvas_id, setup_id, round_index, proposal_hash, validation_result_hash),
    ).fetchone()
    return _from_row(row) if row is not None else None


def persist(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    tenant_id: str,
    canvas_id: str,
    setup_id: str,
    round_index: int,
    proposal_hash: str,
    validation_result_hash: str,
    result: ExperimentResult,
) -> ResultGeneration:
    _validate_key(
        tenant_id, canvas_id, setup_id, round_index, proposal_hash, validation_result_hash
    )
    encoded, digest = _canonical(result)
    existing = get(
        conn,
        tenant_id=tenant_id,
        canvas_id=canvas_id,
        setup_id=setup_id,
        round_index=round_index,
        proposal_hash=proposal_hash,
        validation_result_hash=validation_result_hash,
    )
    if existing is not None:
        if existing.result_hash != digest:
            raise ResultGenerationConflictError("authorized generation key has incompatible result")
        return existing
    now = clock()
    conn.execute(
        "INSERT INTO result_generations (tenant_id, canvas_id, setup_id, round_index, "
        "proposal_hash, validation_result_hash, result_hash, result_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            tenant_id,
            canvas_id,
            setup_id,
            round_index,
            proposal_hash,
            validation_result_hash,
            digest,
            encoded,
            now,
            now,
        ),
    )
    stored = get(
        conn,
        tenant_id=tenant_id,
        canvas_id=canvas_id,
        setup_id=setup_id,
        round_index=round_index,
        proposal_hash=proposal_hash,
        validation_result_hash=validation_result_hash,
    )
    if stored is None:  # pragma: no cover - row was just inserted
        raise RuntimeError("result generation missing immediately after persist")
    return stored


__all__ = ["ResultGeneration", "ResultGenerationConflictError", "get", "persist"]
