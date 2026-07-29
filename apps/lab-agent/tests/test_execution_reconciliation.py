"""Phase 8 reconciliation concurrency and restart-recovery regressions."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

from lab_agent.analysis_lifecycle import reconcile_analysis
from lab_agent.execution_lifecycle import reconcile_execution
from lab_agent.models.execution import (
    AnalysisRequest,
    AnalysisRun,
    EvidenceKind,
    ExecutionRequest,
    ExecutionRun,
    ExternalRunStatus,
    RunMode,
)
from lab_agent.state_store import IntentStatus, StateStore


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()

def _now() -> datetime:
    return datetime.now(UTC)

def _execution_request() -> ExecutionRequest:
    return ExecutionRequest(
        request_id="req-1", execution_run_id="run-1", canvas_id="canvas-1", setup_id="setup-1",
        round=0, proposal_hash=_hash("proposal"), validation_result_hash=_hash("validation"),
        adapter_name="adapter", adapter_version="1", mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN, submit_intent_key="execution-intent",
        input_hash=_hash("execution-input"), requested_at=_now(),
    )

def _analysis_request() -> AnalysisRequest:
    return AnalysisRequest(
        request_id="analysis-request", analysis_run_id="analysis-1", canvas_id="canvas-1",
        execution_run_id="run-1", source_artifact_ref_ids=("raw-1",), adapter_name="adapter",
        adapter_version="1", mode=RunMode.DRY_RUN, evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        submit_intent_key="analysis-intent", input_hash=_hash("analysis-input"), requested_at=_now(),
    )

class _AbsentExecutionAdapter:
    reconciliation_supported = True

    def __init__(self) -> None:
        self.find_calls = 0
        self.submit_calls = 0
        self.both_looked_up = asyncio.Event()

    async def find_by_idempotency_key(self, tenant_id: str, key: str) -> ExecutionRun | None:
        del tenant_id, key
        self.find_calls += 1
        if self.find_calls == 2:
            self.both_looked_up.set()
        await self.both_looked_up.wait()
        return None

    async def submit(self, request: ExecutionRequest) -> ExecutionRun:
        self.submit_calls += 1
        return ExecutionRun(**request.model_dump(by_alias=True), status=ExternalRunStatus.SUCCEEDED, provider_execution_id="provider-1")

    async def result(self, run: ExecutionRun) -> tuple[object, ...]:
        del run
        return ()


class _AbsentAnalysisAdapter:
    reconciliation_supported = True

    def __init__(self) -> None:
        self.find_calls = 0
        self.submit_calls = 0
        self.both_looked_up = asyncio.Event()

    async def find_by_idempotency_key(self, tenant_id: str, key: str) -> AnalysisRun | None:
        del tenant_id, key
        self.find_calls += 1
        if self.find_calls == 2:
            self.both_looked_up.set()
        await self.both_looked_up.wait()
        return None

    async def submit(self, request: AnalysisRequest) -> AnalysisRun:
        self.submit_calls += 1
        return AnalysisRun(
            **request.model_dump(),
            status=ExternalRunStatus.SUCCEEDED,
            provider_job_id="analysis-provider-1",
        )

    async def result(self, run: AnalysisRun) -> tuple[object, ...]:
        del run
        return ()


class _TerminalExecutionAdapter:
    reconciliation_supported = True

    async def result(self, run: ExecutionRun) -> tuple[object, ...]:
        del run
        return ()


class _TerminalAnalysisAdapter:
    reconciliation_supported = True

    async def result(self, run: AnalysisRun) -> tuple[object, ...]:
        del run
        return ()


def _prepare_execution(store: StateStore, *, ambiguous: bool = False) -> ExecutionRun:
    request = _execution_request()
    run, _ = store.prepare_execution_run(request)
    store.prepare_intent(idempotency_key=request.submit_intent_key, canvas_id=request.canvas_id, kind="lab_execution_submit", input_hash=request.input_hash)
    store.mark_intent_submitted(request.submit_intent_key)
    run = store.transition_execution_run("canvas-1", "run-1", status=ExternalRunStatus.SUBMITTED)
    if ambiguous:
        store.mark_intent_ambiguous(request.submit_intent_key, error="timeout", external_id=None)
        run = store.transition_execution_run("canvas-1", "run-1", status=ExternalRunStatus.AMBIGUOUS)
    return run


def _prepare_analysis(store: StateStore, *, ambiguous: bool = False) -> AnalysisRun:
    _prepare_execution(store)
    request = _analysis_request()
    run, _ = store.prepare_analysis_run(request)
    store.prepare_intent(
        idempotency_key=request.submit_intent_key,
        canvas_id=request.canvas_id,
        kind="analysis_submit",
        input_hash=request.input_hash,
    )
    store.mark_intent_submitted(request.submit_intent_key)
    run = store.transition_analysis_run("canvas-1", "analysis-1", status=ExternalRunStatus.SUBMITTED)
    if ambiguous:
        store.mark_intent_ambiguous(request.submit_intent_key, error="timeout", external_id=None)
        run = store.transition_analysis_run("canvas-1", "analysis-1", status=ExternalRunStatus.AMBIGUOUS)
    return run


async def test_concurrent_absent_execution_reconcile_claims_one_submit(store: StateStore) -> None:
    run = _prepare_execution(store, ambiguous=True)
    adapter = _AbsentExecutionAdapter()

    outcomes = await asyncio.gather(
        reconcile_execution(store, adapter, run, authorize=lambda: None),
        reconcile_execution(store, adapter, run, authorize=lambda: None),
    )

    durable = store.get_execution_run("canvas-1", "run-1")
    assert adapter.submit_calls == 1
    assert durable is not None and durable.status is ExternalRunStatus.SUCCEEDED
    assert store.get_intent("execution-intent").status is IntentStatus.RECONCILED
    assert {outcome.status for outcome in outcomes} <= {ExternalRunStatus.RUNNING, ExternalRunStatus.SUCCEEDED}


async def test_concurrent_absent_analysis_reconcile_claims_one_submit(store: StateStore) -> None:
    run = _prepare_analysis(store, ambiguous=True)
    adapter = _AbsentAnalysisAdapter()

    outcomes = await asyncio.gather(
        reconcile_analysis(store, adapter, run),
        reconcile_analysis(store, adapter, run),
    )

    durable = store.get_analysis_run("canvas-1", "analysis-1")
    assert adapter.submit_calls == 1
    assert durable is not None and durable.status is ExternalRunStatus.SUCCEEDED
    assert store.get_intent("analysis-intent").status is IntentStatus.RECONCILED
    assert {outcome.status for outcome in outcomes} <= {
        ExternalRunStatus.RUNNING,
        ExternalRunStatus.SUCCEEDED,
    }


async def test_terminal_recovery_reconciles_execution_and_analysis_intents(store: StateStore) -> None:
    execution = _prepare_execution(store)
    execution = store.transition_execution_run("canvas-1", "run-1", status=ExternalRunStatus.RUNNING)
    execution = store.transition_execution_run("canvas-1", "run-1", status=ExternalRunStatus.SUCCEEDED, provider_execution_id="execution-provider")
    await reconcile_execution(store, _TerminalExecutionAdapter(), execution, authorize=lambda: None)

    request = _analysis_request()
    analysis, _ = store.prepare_analysis_run(request)
    store.prepare_intent(idempotency_key=request.submit_intent_key, canvas_id=request.canvas_id, kind="analysis_submit", input_hash=request.input_hash)
    store.mark_intent_submitted(request.submit_intent_key)
    analysis = store.transition_analysis_run("canvas-1", "analysis-1", status=ExternalRunStatus.SUBMITTED)
    analysis = store.transition_analysis_run("canvas-1", "analysis-1", status=ExternalRunStatus.RUNNING)
    analysis = store.transition_analysis_run("canvas-1", "analysis-1", status=ExternalRunStatus.SUCCEEDED, provider_job_id="analysis-provider")
    await reconcile_analysis(store, _TerminalAnalysisAdapter(), analysis)

    assert store.get_intent("execution-intent").status is IntentStatus.RECONCILED
    assert store.get_intent("execution-intent").external_id == "execution-provider"
    assert store.get_intent("analysis-intent").status is IntentStatus.RECONCILED
    assert store.get_intent("analysis-intent").external_id == "analysis-provider"
