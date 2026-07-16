"""Restart-safe, dry-run-only laboratory execution lifecycle."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

from lab_agent.integrations.lab_execution import LabExecutionAdapter, LabExecutionAdapterError
from lab_agent.models.execution import (
    ArtifactRef,
    ArtifactRole,
    EvidenceKind,
    ExecutionRequest,
    ExecutionRun,
    ExternalFailureCode,
    ExternalRunStatus,
)
from lab_agent.state.intents import IntentAlreadyClaimedError
from lab_agent.state.models import IntentStatus
from lab_agent.state_store import StateStore


class ExecutionLifecycleError(RuntimeError):
    """Execution progression cannot safely continue."""


_AUTHORIZE = Callable[[], object]
_ACTIVE = frozenset({
    ExternalRunStatus.SUBMITTED, ExternalRunStatus.RUNNING, ExternalRunStatus.RECONCILING,
    ExternalRunStatus.AMBIGUOUS,
})

def _failure(error: Exception) -> ExternalFailureCode:
    return error.code if isinstance(error, LabExecutionAdapterError) else ExternalFailureCode.PROVIDER_FAILURE

def _request(run: ExecutionRun) -> ExecutionRequest:
    return ExecutionRequest.model_validate(run.model_dump(by_alias=True, exclude={
        "status", "abort_intent_key", "provider_execution_id", "submitted_at", "finished_at", "failure_code",
    }))

def _valid_remote(request: ExecutionRequest, remote: ExecutionRun) -> bool:
    return (
        remote.canvas_id == request.canvas_id
        and remote.execution_run_id == request.execution_run_id
        and remote.submit_intent_key == request.submit_intent_key
        and remote.input_hash == request.input_hash
        and remote.mode is request.mode
        and remote.evidence_kind is request.evidence_kind
    )

def _append_raw_refs(store: StateStore, run: ExecutionRun, refs: tuple[ArtifactRef, ...]) -> None:
    for ref in refs:
        if (
            ref.canvas_id != run.canvas_id
            or ref.execution_run_id != run.execution_run_id
            or ref.role is not ArtifactRole.RAW
            or ref.evidence_kind is not EvidenceKind.MOCK_OR_DRY_RUN
        ):
            raise ExecutionLifecycleError("execution adapter returned invalid raw artifact reference")
        store.append_artifact_ref(ref)

async def run_execution(
    store: StateStore,
    adapter: LabExecutionAdapter,
    request: ExecutionRequest,
    *,
    authorize: _AUTHORIZE,
) -> ExecutionRun:
    """Prepare durable state before submit; reconcile instead of replaying work."""

    run, _ = store.prepare_execution_run(request)
    intent = store.prepare_intent(
        idempotency_key=request.submit_intent_key, canvas_id=request.canvas_id,
        kind="lab_execution_submit", input_hash=request.input_hash,
    )
    if run.status in _ACTIVE or intent.status in {IntentStatus.SUBMITTED, IntentStatus.EXECUTED}:
        if run.status is ExternalRunStatus.PENDING:
            run = store.transition_execution_run(
                request.canvas_id, request.execution_run_id, status=ExternalRunStatus.SUBMITTED
            )
        return await reconcile_execution(store, adapter, run, authorize=authorize)
    if run.status is not ExternalRunStatus.PENDING:
        return run
    if intent.status is IntentStatus.RECONCILED:
        return await reconcile_execution(store, adapter, run, authorize=authorize)
    authorize()
    try:
        store.mark_intent_submitted(request.submit_intent_key)
    except IntentAlreadyClaimedError:
        return store.get_execution_run(request.canvas_id, request.execution_run_id) or run
    run = store.transition_execution_run(request.canvas_id, request.execution_run_id, status=ExternalRunStatus.SUBMITTED)
    return await _submit_execution(store, adapter, run)

async def _submit_execution(store: StateStore, adapter: LabExecutionAdapter, run: ExecutionRun) -> ExecutionRun:
    try:
        remote = await adapter.submit(_request(run))
        if not _valid_remote(run, remote) or not remote.provider_execution_id:
            raise ExecutionLifecycleError("execution adapter returned an incompatible run")
        store.mark_intent_executed(run.submit_intent_key, external_id=remote.provider_execution_id)
        if run.status is ExternalRunStatus.SUBMITTED and remote.status in {ExternalRunStatus.SUCCEEDED, ExternalRunStatus.FAILED, ExternalRunStatus.ABORTED}:
            run = store.transition_execution_run(run.canvas_id, run.execution_run_id, status=ExternalRunStatus.RUNNING)
        updated = store.transition_execution_run(
            run.canvas_id,
            run.execution_run_id,
            status=remote.status,
            provider_execution_id=remote.provider_execution_id,
            failure_code=remote.failure_code,
        )
        if updated.status is ExternalRunStatus.SUCCEEDED:
            _append_raw_refs(store, updated, await adapter.result(remote))
            store.mark_intent_reconciled(run.submit_intent_key, external_id=remote.provider_execution_id)
        return updated
    except LabExecutionAdapterError as exc:
        store.mark_intent_failed(run.submit_intent_key, error=exc.code.value)
        return store.transition_execution_run(run.canvas_id, run.execution_run_id, status=ExternalRunStatus.FAILED, failure_code=exc.code)
    except Exception as exc:
        code = _failure(exc)
        store.mark_intent_ambiguous(run.submit_intent_key, error=code.value, external_id=None)
        return store.transition_execution_run(run.canvas_id, run.execution_run_id, status=ExternalRunStatus.AMBIGUOUS)

async def reconcile_execution(
    store: StateStore, adapter: LabExecutionAdapter, run: ExecutionRun, *, authorize: _AUTHORIZE
) -> ExecutionRun:
    """Authoritatively locate submitted work before a possible retry."""

    if run.status in {ExternalRunStatus.SUCCEEDED, ExternalRunStatus.FAILED, ExternalRunStatus.ABORTED}:
        intent = store.get_intent(run.submit_intent_key)
        if intent is not None and intent.status is not IntentStatus.RECONCILED:
            store.mark_intent_reconciled(run.submit_intent_key, external_id=run.provider_execution_id)
        if run.status is ExternalRunStatus.SUCCEEDED:
            _append_raw_refs(store, run, await adapter.result(run))
        return run
    if run.status not in _ACTIVE:
        return run
    if not adapter.reconciliation_supported:
        return store.transition_execution_run(
            run.canvas_id, run.execution_run_id, status=ExternalRunStatus.BLOCKED,
            failure_code=ExternalFailureCode.RECONCILIATION_UNSUPPORTED,
        )
    if run.status is not ExternalRunStatus.RECONCILING:
        run = store.transition_execution_run(
            run.canvas_id, run.execution_run_id, status=ExternalRunStatus.RECONCILING
        )
    try:
        remote = await adapter.find_by_idempotency_key(run.submit_intent_key)
    except Exception:
        return store.transition_execution_run(
            run.canvas_id, run.execution_run_id, status=ExternalRunStatus.BLOCKED,
            failure_code=ExternalFailureCode.RECONCILIATION_UNSUPPORTED,
        )
    if remote is not None:
        if not _valid_remote(run, remote) or not remote.provider_execution_id:
            return store.transition_execution_run(run.canvas_id, run.execution_run_id, status=ExternalRunStatus.BLOCKED, failure_code=ExternalFailureCode.INVALID_SCHEMA)
        store.mark_intent_reconciled(run.submit_intent_key, external_id=remote.provider_execution_id)
        updated = store.transition_execution_run(
            run.canvas_id, run.execution_run_id, status=remote.status,
            provider_execution_id=remote.provider_execution_id, failure_code=remote.failure_code,
        )
        if updated.status is ExternalRunStatus.SUCCEEDED:
            _append_raw_refs(store, updated, await adapter.result(remote))
        return updated
    authorize()
    try:
        store.mark_intent_submitted(run.submit_intent_key)
    except IntentAlreadyClaimedError:
        return store.get_execution_run(run.canvas_id, run.execution_run_id) or run
    run = store.transition_execution_run(run.canvas_id, run.execution_run_id, status=ExternalRunStatus.RUNNING)
    return await _submit_execution(store, adapter, run)

async def abort_execution(store: StateStore, adapter: LabExecutionAdapter, run: ExecutionRun) -> ExecutionRun:
    """Persist an abort intent without deleting refs or retrying an ambiguity."""

    if run.status in {ExternalRunStatus.SUCCEEDED, ExternalRunStatus.FAILED, ExternalRunStatus.ABORTED, ExternalRunStatus.BLOCKED}:
        return run
    key = f"lab_execution_abort:{hashlib.sha256(run.submit_intent_key.encode()).hexdigest()}"
    store.prepare_intent(idempotency_key=key, canvas_id=run.canvas_id, kind="lab_execution_abort", input_hash=run.input_hash)
    run = store.transition_execution_run(
        run.canvas_id, run.execution_run_id, status=ExternalRunStatus.ABORT_REQUESTED, abort_intent_key=key
    )
    try:
        store.mark_intent_submitted(key)
        remote = await adapter.abort(run)
        if remote.execution_run_id != run.execution_run_id:
            raise ExecutionLifecycleError("execution adapter returned an incompatible abort")
        store.mark_intent_executed(key, external_id=remote.provider_execution_id or run.execution_run_id)
        store.mark_intent_reconciled(key, external_id=remote.provider_execution_id or run.execution_run_id)
        return store.transition_execution_run(run.canvas_id, run.execution_run_id, status=ExternalRunStatus.ABORTED, provider_execution_id=remote.provider_execution_id, failure_code=ExternalFailureCode.ABORTED)
    except Exception:
        store.mark_intent_ambiguous(key, error="abort_ambiguous", external_id=None)
        return run


__all__ = ["ExecutionLifecycleError", "abort_execution", "reconcile_execution", "run_execution"]
