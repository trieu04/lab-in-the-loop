"""Restart-safe, mock-only analysis lifecycle over retained execution refs."""

from __future__ import annotations

import hashlib

from lab_agent.integrations.flywheel import FlywheelAdapter, FlywheelAdapterError
from lab_agent.models.execution import (
    AnalysisRequest,
    AnalysisRun,
    ArtifactRef,
    ArtifactRole,
    EvidenceKind,
    ExternalFailureCode,
    ExternalRunStatus,
)
from lab_agent.state.intents import IntentAlreadyClaimedError
from lab_agent.state.models import IntentStatus
from lab_agent.state_store import StateStore


class AnalysisLifecycleError(RuntimeError):
    """Analysis progression cannot safely continue."""


_ACTIVE = frozenset({
    ExternalRunStatus.SUBMITTED, ExternalRunStatus.RUNNING, ExternalRunStatus.RECONCILING,
    ExternalRunStatus.AMBIGUOUS,
})

def _failure(error: Exception) -> ExternalFailureCode:
    return error.code if isinstance(error, FlywheelAdapterError) else ExternalFailureCode.PROVIDER_FAILURE

def _request(run: AnalysisRun) -> AnalysisRequest:
    return AnalysisRequest.model_validate(run.model_dump(exclude={
        "status", "abort_intent_key", "provider_job_id", "submitted_at", "finished_at", "failure_code",
    }))

def _valid_remote(request: AnalysisRequest, remote: AnalysisRun) -> bool:
    return (
        remote.tenant_id == request.tenant_id
        and remote.canvas_id == request.canvas_id
        and remote.analysis_run_id == request.analysis_run_id
        and remote.execution_run_id == request.execution_run_id
        and remote.submit_intent_key == request.submit_intent_key
        and remote.input_hash == request.input_hash
        and remote.source_artifact_ref_ids == request.source_artifact_ref_ids
        and remote.mode is request.mode
        and remote.evidence_kind is request.evidence_kind
    )

def _append_derived_refs(store: StateStore, run: AnalysisRun, refs: tuple[ArtifactRef, ...]) -> None:
    for ref in refs:
        if (
            ref.tenant_id != run.tenant_id
            or ref.canvas_id != run.canvas_id
            or ref.analysis_run_id != run.analysis_run_id
            or ref.role is not ArtifactRole.DERIVED
            or ref.evidence_kind is not EvidenceKind.MOCK_OR_DRY_RUN
        ):
            raise AnalysisLifecycleError("analysis adapter returned invalid derived artifact reference")
        store.append_artifact_ref(ref)

def _source_refs(store: StateStore, request: AnalysisRequest) -> None:
    refs = [store.get_artifact_ref(request.canvas_id, ref_id) for ref_id in request.source_artifact_ref_ids]
    if any(
        ref is None
        or ref.execution_run_id != request.execution_run_id
        or ref.role is not ArtifactRole.RAW
        or ref.evidence_kind is not EvidenceKind.MOCK_OR_DRY_RUN
        for ref in refs
    ):
        raise AnalysisLifecycleError("analysis requires retained matching mock raw references")

async def run_analysis(store: StateStore, adapter: FlywheelAdapter, request: AnalysisRequest) -> AnalysisRun:
    """Persist analysis row and submit intent before entering the adapter."""

    _source_refs(store, request)
    run, _ = store.prepare_analysis_run(request)
    intent = store.prepare_intent(
        idempotency_key=request.submit_intent_key,
        canvas_id=request.canvas_id,
        kind="analysis_submit",
        input_hash=request.input_hash,
    )
    if run.status in _ACTIVE or intent.status in {IntentStatus.SUBMITTED, IntentStatus.EXECUTED, IntentStatus.RECONCILED}:
        if run.status is ExternalRunStatus.PENDING:
            run = store.transition_analysis_run(
                request.canvas_id, request.analysis_run_id, status=ExternalRunStatus.SUBMITTED
            )
        return await reconcile_analysis(store, adapter, run)
    if run.status is not ExternalRunStatus.PENDING:
        return run
    try:
        store.mark_intent_submitted(request.submit_intent_key, canvas_id=request.canvas_id)
    except IntentAlreadyClaimedError:
        return store.get_analysis_run(request.canvas_id, request.analysis_run_id) or run
    run = store.transition_analysis_run(
        request.canvas_id, request.analysis_run_id, status=ExternalRunStatus.SUBMITTED
    )
    return await _submit_analysis(store, adapter, run)

async def _submit_analysis(store: StateStore, adapter: FlywheelAdapter, run: AnalysisRun) -> AnalysisRun:
    try:
        remote = await adapter.submit(_request(run))
        if not _valid_remote(run, remote) or not remote.provider_job_id:
            raise AnalysisLifecycleError("analysis adapter returned an incompatible run")
        store.mark_intent_executed(run.submit_intent_key, canvas_id=run.canvas_id, external_id=remote.provider_job_id)
        if run.status is ExternalRunStatus.SUBMITTED and remote.status in {ExternalRunStatus.SUCCEEDED, ExternalRunStatus.FAILED, ExternalRunStatus.ABORTED}:
            run = store.transition_analysis_run(run.canvas_id, run.analysis_run_id, status=ExternalRunStatus.RUNNING)
        updated = store.transition_analysis_run(
            run.canvas_id, run.analysis_run_id, status=remote.status,
            provider_job_id=remote.provider_job_id, failure_code=remote.failure_code,
        )
        if updated.status is ExternalRunStatus.SUCCEEDED:
            _append_derived_refs(store, updated, await adapter.result(remote))
            store.mark_intent_reconciled(run.submit_intent_key, canvas_id=run.canvas_id, external_id=remote.provider_job_id)
        return updated
    except FlywheelAdapterError as exc:
        store.mark_intent_failed(run.submit_intent_key, canvas_id=run.canvas_id, error=exc.code.value)
        return store.transition_analysis_run(run.canvas_id, run.analysis_run_id, status=ExternalRunStatus.FAILED, failure_code=exc.code)
    except Exception as exc:
        store.mark_intent_ambiguous(run.submit_intent_key, canvas_id=run.canvas_id, error=_failure(exc).value, external_id=None)
        return store.transition_analysis_run(run.canvas_id, run.analysis_run_id, status=ExternalRunStatus.AMBIGUOUS)

async def reconcile_analysis(store: StateStore, adapter: FlywheelAdapter, run: AnalysisRun) -> AnalysisRun:
    """Locate prior analysis before permitting one proved-absent retry."""

    if run.status in {ExternalRunStatus.SUCCEEDED, ExternalRunStatus.FAILED, ExternalRunStatus.ABORTED}:
        intent = store.get_intent(run.submit_intent_key, canvas_id=run.canvas_id)
        if intent is not None and intent.status is not IntentStatus.RECONCILED:
            store.mark_intent_reconciled(run.submit_intent_key, canvas_id=run.canvas_id, external_id=run.provider_job_id)
        if run.status is ExternalRunStatus.SUCCEEDED:
            _append_derived_refs(store, run, await adapter.result(run))
        return run
    if run.status not in _ACTIVE:
        return run
    if not adapter.reconciliation_supported:
        return store.transition_analysis_run(
            run.canvas_id, run.analysis_run_id, status=ExternalRunStatus.BLOCKED,
            failure_code=ExternalFailureCode.RECONCILIATION_UNSUPPORTED,
        )
    if run.status is not ExternalRunStatus.RECONCILING:
        run = store.transition_analysis_run(
            run.canvas_id, run.analysis_run_id, status=ExternalRunStatus.RECONCILING
        )
    try:
        remote = await adapter.find_by_idempotency_key(run.tenant_id, run.submit_intent_key)
    except Exception:
        return store.transition_analysis_run(
            run.canvas_id, run.analysis_run_id, status=ExternalRunStatus.BLOCKED,
            failure_code=ExternalFailureCode.RECONCILIATION_UNSUPPORTED,
        )
    if remote is not None:
        if not _valid_remote(run, remote) or not remote.provider_job_id:
            return store.transition_analysis_run(run.canvas_id, run.analysis_run_id, status=ExternalRunStatus.BLOCKED, failure_code=ExternalFailureCode.INVALID_SCHEMA)
        store.mark_intent_reconciled(run.submit_intent_key, canvas_id=run.canvas_id, external_id=remote.provider_job_id)
        updated = store.transition_analysis_run(
            run.canvas_id, run.analysis_run_id, status=remote.status,
            provider_job_id=remote.provider_job_id, failure_code=remote.failure_code,
        )
        if updated.status is ExternalRunStatus.SUCCEEDED:
            _append_derived_refs(store, updated, await adapter.result(remote))
        return updated
    try:
        store.mark_intent_submitted(run.submit_intent_key, canvas_id=run.canvas_id)
    except IntentAlreadyClaimedError:
        return store.get_analysis_run(run.canvas_id, run.analysis_run_id) or run
    run = store.transition_analysis_run(run.canvas_id, run.analysis_run_id, status=ExternalRunStatus.RUNNING)
    return await _submit_analysis(store, adapter, run)

async def abort_analysis(store: StateStore, adapter: FlywheelAdapter, run: AnalysisRun) -> AnalysisRun:
    """Abort with a separate intent while preserving raw and derived refs."""

    if run.status in {ExternalRunStatus.SUCCEEDED, ExternalRunStatus.FAILED, ExternalRunStatus.ABORTED, ExternalRunStatus.BLOCKED}:
        return run
    key = f"analysis_abort:{hashlib.sha256(run.submit_intent_key.encode()).hexdigest()}"
    store.prepare_intent(idempotency_key=key, canvas_id=run.canvas_id, kind="analysis_abort", input_hash=run.input_hash)
    run = store.transition_analysis_run(
        run.canvas_id, run.analysis_run_id, status=ExternalRunStatus.ABORT_REQUESTED, abort_intent_key=key
    )
    try:
        store.mark_intent_submitted(key, canvas_id=run.canvas_id)
        remote = await adapter.cancel(run)
        if (remote.tenant_id, remote.canvas_id, remote.analysis_run_id) != (run.tenant_id, run.canvas_id, run.analysis_run_id):
            raise AnalysisLifecycleError("analysis adapter returned an incompatible abort")
        external_id = remote.provider_job_id or run.analysis_run_id
        store.mark_intent_executed(key, canvas_id=run.canvas_id, external_id=external_id)
        store.mark_intent_reconciled(key, canvas_id=run.canvas_id, external_id=external_id)
        return store.transition_analysis_run(run.canvas_id, run.analysis_run_id, status=ExternalRunStatus.ABORTED, provider_job_id=remote.provider_job_id, failure_code=ExternalFailureCode.ABORTED)
    except Exception:
        store.mark_intent_ambiguous(key, canvas_id=run.canvas_id, error="abort_ambiguous", external_id=None)
        return run


__all__ = ["AnalysisLifecycleError", "abort_analysis", "reconcile_analysis", "run_analysis"]
