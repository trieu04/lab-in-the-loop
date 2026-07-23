"""Canvas-scoped, guarded persistence for external execution and analysis runs."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TypeVar

from lab_agent.models.execution import (
    AnalysisRequest,
    AnalysisRun,
    ExecutionRequest,
    ExecutionRun,
    ExternalFailureCode,
    ExternalRunStatus,
)
from lab_agent.state.external_models import analysis_from_row, dump_json, execution_from_row, iso

_Run = TypeVar("_Run", ExecutionRun, AnalysisRun)
_Request = TypeVar("_Request", ExecutionRequest, AnalysisRequest)
_Row = Callable[[sqlite3.Row], _Run]
_TERMINAL = {ExternalRunStatus.SUCCEEDED, ExternalRunStatus.FAILED, ExternalRunStatus.ABORTED, ExternalRunStatus.BLOCKED}
_ALLOWED = {
    ExternalRunStatus.PENDING: {ExternalRunStatus.SUBMITTED, ExternalRunStatus.BLOCKED, ExternalRunStatus.FAILED},
    ExternalRunStatus.SUBMITTED: {ExternalRunStatus.RUNNING, ExternalRunStatus.RECONCILING, ExternalRunStatus.AMBIGUOUS, ExternalRunStatus.FAILED, ExternalRunStatus.ABORT_REQUESTED, ExternalRunStatus.BLOCKED},
    ExternalRunStatus.RUNNING: {ExternalRunStatus.SUCCEEDED, ExternalRunStatus.FAILED, ExternalRunStatus.RECONCILING, ExternalRunStatus.AMBIGUOUS, ExternalRunStatus.ABORT_REQUESTED, ExternalRunStatus.BLOCKED},
    ExternalRunStatus.RECONCILING: {ExternalRunStatus.RUNNING, ExternalRunStatus.SUCCEEDED, ExternalRunStatus.FAILED, ExternalRunStatus.AMBIGUOUS, ExternalRunStatus.ABORT_REQUESTED, ExternalRunStatus.BLOCKED},
    ExternalRunStatus.AMBIGUOUS: {ExternalRunStatus.RECONCILING, ExternalRunStatus.ABORT_REQUESTED, ExternalRunStatus.BLOCKED},
    ExternalRunStatus.ABORT_REQUESTED: {ExternalRunStatus.ABORTED, ExternalRunStatus.RECONCILING, ExternalRunStatus.AMBIGUOUS, ExternalRunStatus.BLOCKED},
}

class ExternalRunConflictError(RuntimeError):
    """A durable run identity or idempotency key was reused incompatibly."""
class ExternalRunNotFoundError(RuntimeError):
    """A requested run does not exist on its claimed canvas."""
class ExternalRunScopeError(RuntimeError):
    """A durable run belongs to another canvas."""
class ExternalRunTransitionError(RuntimeError):
    """A lifecycle transition would rewrite immutable or terminal history."""

def prepare_execution_run(conn: sqlite3.Connection, *, request: ExecutionRequest) -> tuple[ExecutionRun, bool]:
    return _prepare(conn, request=request, table="execution_runs", id_column="execution_run_id", row=execution_from_row)

def prepare_analysis_run(conn: sqlite3.Connection, *, request: AnalysisRequest) -> tuple[AnalysisRun, bool]:
    return _prepare(conn, request=request, table="analysis_runs", id_column="analysis_run_id", row=analysis_from_row)

def get_execution_run(conn: sqlite3.Connection, *, canvas_id: str, execution_run_id: str) -> ExecutionRun | None:
    return _get(conn, table="execution_runs", id_column="execution_run_id", canvas_id=canvas_id, run_id=execution_run_id, row=execution_from_row)

def get_analysis_run(conn: sqlite3.Connection, *, canvas_id: str, analysis_run_id: str) -> AnalysisRun | None:
    return _get(conn, table="analysis_runs", id_column="analysis_run_id", canvas_id=canvas_id, run_id=analysis_run_id, row=analysis_from_row)

def list_execution_runs(conn: sqlite3.Connection, *, canvas_id: str, status: ExternalRunStatus | None = None) -> list[ExecutionRun]:
    return _list(conn, table="execution_runs", canvas_id=canvas_id, status=status, row=execution_from_row)

def list_analysis_runs(conn: sqlite3.Connection, *, canvas_id: str, status: ExternalRunStatus | None = None) -> list[AnalysisRun]:
    return _list(conn, table="analysis_runs", canvas_id=canvas_id, status=status, row=analysis_from_row)

def transition_execution_run(conn: sqlite3.Connection, *, clock: Callable[[], float], canvas_id: str, execution_run_id: str, status: ExternalRunStatus, provider_execution_id: str | None = None, failure_code: ExternalFailureCode | None = None, abort_intent_key: str | None = None) -> ExecutionRun:
    return _transition(conn, clock=clock, table="execution_runs", id_column="execution_run_id", provider_column="provider_execution_id", canvas_id=canvas_id, run_id=execution_run_id, status=status, provider_id=provider_execution_id, failure_code=failure_code, abort_intent_key=abort_intent_key, row=execution_from_row)

def transition_analysis_run(conn: sqlite3.Connection, *, clock: Callable[[], float], canvas_id: str, analysis_run_id: str, status: ExternalRunStatus, provider_job_id: str | None = None, failure_code: ExternalFailureCode | None = None, abort_intent_key: str | None = None) -> AnalysisRun:
    return _transition(conn, clock=clock, table="analysis_runs", id_column="analysis_run_id", provider_column="provider_job_id", canvas_id=canvas_id, run_id=analysis_run_id, status=status, provider_id=provider_job_id, failure_code=failure_code, abort_intent_key=abort_intent_key, row=analysis_from_row)

def _prepare(conn: sqlite3.Connection, *, request: _Request, table: str, id_column: str, row: _Row[_Run]) -> tuple[_Run, bool]:
    run_id = getattr(request, id_column)
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(f"SELECT * FROM {table} WHERE submit_intent_key=?", (request.submit_intent_key,)).fetchone()
        if existing is not None:
            if existing["canvas_id"] != request.canvas_id:
                raise ExternalRunScopeError("idempotency key belongs to another canvas")
            if existing["input_hash"] != request.input_hash:
                raise ExternalRunConflictError("idempotency key has a different input hash")
            conn.execute("COMMIT")
            return row(existing), False
        same_id = conn.execute(f"SELECT canvas_id FROM {table} WHERE {id_column}=?", (run_id,)).fetchone()
        if same_id is not None:
            if same_id["canvas_id"] != request.canvas_id:
                raise ExternalRunScopeError("run id belongs to another canvas")
            raise ExternalRunConflictError("run id already exists with another idempotency key")
        if table == "analysis_runs":
            _require_matching_execution_evidence(conn, request)
        _insert(conn, table=table, request=request)
        created = conn.execute(f"SELECT * FROM {table} WHERE {id_column}=?", (run_id,)).fetchone()
        if created is None:
            raise RuntimeError("run missing immediately after insert")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row(created), True

def _require_matching_execution_evidence(conn: sqlite3.Connection, request: _Request) -> None:
    if not isinstance(request, AnalysisRequest):
        return
    source = conn.execute(
        "SELECT evidence_kind FROM execution_runs WHERE canvas_id=? AND execution_run_id=?",
        (request.canvas_id, request.execution_run_id),
    ).fetchone()
    if source is None or source["evidence_kind"] != request.evidence_kind.value:
        raise ExternalRunConflictError("analysis evidence kind must match its execution source")

def _insert(conn: sqlite3.Connection, *, table: str, request: _Request) -> None:
    values = request.model_dump(mode="json")
    if table == "execution_runs":
        conn.execute("INSERT INTO execution_runs (execution_run_id,canvas_id,request_id,setup_id,round_index,proposal_hash,validation_result_hash,adapter_name,adapter_version,mode,evidence_kind,submit_intent_key,input_hash,rerun_of_execution_id,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (values["execution_run_id"], values["canvas_id"], values["request_id"], values["setup_id"], values["round_index"], values["proposal_hash"], values["validation_result_hash"], values["adapter_name"], values["adapter_version"], values["mode"], values["evidence_kind"], values["submit_intent_key"], values["input_hash"], values["rerun_of_execution_id"], "pending", values["requested_at"]))
    else:
        conn.execute("INSERT INTO analysis_runs (analysis_run_id,canvas_id,request_id,execution_run_id,source_artifact_ref_ids_json,adapter_name,adapter_version,mode,evidence_kind,submit_intent_key,input_hash,rerun_of_analysis_id,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (values["analysis_run_id"], values["canvas_id"], values["request_id"], values["execution_run_id"], dump_json(values["source_artifact_ref_ids"]), values["adapter_name"], values["adapter_version"], values["mode"], values["evidence_kind"], values["submit_intent_key"], values["input_hash"], values["rerun_of_analysis_id"], "pending", values["requested_at"]))

def _get(conn: sqlite3.Connection, *, table: str, id_column: str, canvas_id: str, run_id: str, row: _Row[_Run]) -> _Run | None:
    found = conn.execute(f"SELECT * FROM {table} WHERE {id_column}=? AND canvas_id=?", (run_id, canvas_id)).fetchone()
    return None if found is None else row(found)

def _list(conn: sqlite3.Connection, *, table: str, canvas_id: str, status: ExternalRunStatus | None, row: _Row[_Run]) -> list[_Run]:
    sql, values = f"SELECT * FROM {table} WHERE canvas_id=?", [canvas_id]
    if status is not None:
        sql += " AND status=?"
        values.append(status.value)
    rows = conn.execute(sql + " ORDER BY created_at", values).fetchall()
    return [row(found) for found in rows]

def _transition(conn: sqlite3.Connection, *, clock: Callable[[], float], table: str, id_column: str, provider_column: str, canvas_id: str, run_id: str, status: ExternalRunStatus, provider_id: str | None, failure_code: ExternalFailureCode | None, abort_intent_key: str | None, row: _Row[_Run]) -> _Run:
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = conn.execute(f"SELECT * FROM {table} WHERE {id_column}=?", (run_id,)).fetchone()
        if current is None:
            raise ExternalRunNotFoundError("external run does not exist")
        if current["canvas_id"] != canvas_id:
            raise ExternalRunScopeError("external run belongs to another canvas")
        old = ExternalRunStatus(current["status"])
        if status != old and (old in _TERMINAL or status not in _ALLOWED.get(old, set())):
            raise ExternalRunTransitionError(f"cannot transition {old.value} to {status.value}")
        for column, value in ((provider_column, provider_id), ("abort_intent_key", abort_intent_key)):
            if value is not None and current[column] not in (None, value):
                raise ExternalRunTransitionError(f"{column} is immutable once assigned")
        failed_statuses = {ExternalRunStatus.FAILED, ExternalRunStatus.BLOCKED, ExternalRunStatus.ABORTED}
        if failure_code is not None and status not in failed_statuses:
            raise ExternalRunTransitionError("failure code requires a terminal failure status")
        now = datetime.fromtimestamp(clock(), tz=UTC)
        submitted = iso(now) if status is ExternalRunStatus.SUBMITTED and current["submitted_at"] is None else current["submitted_at"]
        finished = iso(now) if status in _TERMINAL and current["finished_at"] is None else current["finished_at"]
        conn.execute(f"UPDATE {table} SET status=?, {provider_column}=COALESCE(?, {provider_column}), abort_intent_key=COALESCE(?, abort_intent_key), failure_code=COALESCE(?, failure_code), submitted_at=?, finished_at=? WHERE {id_column}=? AND canvas_id=?", (status.value, provider_id, abort_intent_key, None if failure_code is None else failure_code.value, submitted, finished, run_id, canvas_id))
        updated = conn.execute(f"SELECT * FROM {table} WHERE {id_column}=?", (run_id,)).fetchone()
        if updated is None:
            raise RuntimeError("external run missing immediately after transition")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row(updated)

__all__ = ["ExternalRunConflictError", "ExternalRunNotFoundError", "ExternalRunScopeError", "ExternalRunTransitionError", "get_analysis_run", "get_execution_run", "list_analysis_runs", "list_execution_runs", "prepare_analysis_run", "prepare_execution_run", "transition_analysis_run", "transition_execution_run"]
