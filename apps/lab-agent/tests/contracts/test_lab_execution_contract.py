"""Lab execution adapter contract tests: typed requests/results, idempotency, status, abort/cancel, reconciliation, typed failures, no secrets/raw bodies."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from lab_agent.integrations.lab_execution import (
    DeterministicLabExecutionAdapter,
    LabExecutionAdapter,
    LabExecutionAdapterError,
)
from lab_agent.models.execution import (
    ArtifactRole,
    EvidenceKind,
    ExecutionRequest,
    ExternalFailureCode,
    ExternalRunStatus,
    RunMode,
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _execution_request() -> ExecutionRequest:
    now = datetime.now(UTC)
    return ExecutionRequest(
        request_id="req-1",
        execution_run_id="run-1",
        canvas_id="canvas-1",
        setup_id="setup-1",
        round=0,
        proposal_hash=_hash("proposal"),
        validation_result_hash=_hash("validation"),
        adapter_name="dry-run-adapter",
        adapter_version="1",
        mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        submit_intent_key="intent-key-1",
        input_hash=_hash("input"),
        requested_at=now,
    )


@pytest.mark.asyncio
async def test_lab_adapter_is_protocol() -> None:
    """Adapter protocol is runtime-checkable and verifiable."""
    adapter = DeterministicLabExecutionAdapter()
    assert isinstance(adapter, LabExecutionAdapter)


@pytest.mark.asyncio
async def test_submit_returns_typed_run() -> None:
    """Submit returns a typed ExecutionRun with stable idempotency key."""
    adapter = DeterministicLabExecutionAdapter()
    request = _execution_request()

    run = await adapter.submit(request)

    assert run.execution_run_id == request.execution_run_id
    assert run.canvas_id == request.canvas_id
    assert run.submit_intent_key == request.submit_intent_key
    assert run.status is ExternalRunStatus.SUCCEEDED
    assert run.provider_execution_id is not None
    assert run.submitted_at is not None
    assert run.finished_at is not None
    assert len(run.provider_execution_id) > 0


@pytest.mark.asyncio
async def test_idempotent_submit_converges() -> None:
    """Same idempotency key returns same run; different input fails."""
    adapter = DeterministicLabExecutionAdapter()
    request1 = _execution_request()
    request1_with_key = request1.model_copy(update={"submit_intent_key": "key-1"})

    run1 = await adapter.submit(request1_with_key)

    # Same key, same input → same run
    request2 = _execution_request()
    request2_with_key = request2.model_copy(update={"submit_intent_key": "key-1", "input_hash": request1_with_key.input_hash})

    run2 = await adapter.submit(request2_with_key)
    assert run2.provider_execution_id == run1.provider_execution_id

    # Same key, different input → error
    request3 = _execution_request()
    request3_with_key = request3.model_copy(update={"submit_intent_key": "key-1", "input_hash": _hash("different")})

    with pytest.raises(LabExecutionAdapterError) as exc:
        await adapter.submit(request3_with_key)
    assert exc.value.code is ExternalFailureCode.INVALID_SCHEMA


@pytest.mark.asyncio
async def test_status_returns_run_status() -> None:
    """Status returns current ExternalRunStatus."""
    adapter = DeterministicLabExecutionAdapter()
    request = _execution_request()

    run = await adapter.submit(request)
    status = await adapter.status(run)

    assert status is ExternalRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_abort_preserves_history() -> None:
    """Abort returns updated run with abort marker; preserves prior history."""
    adapter = DeterministicLabExecutionAdapter()
    request = _execution_request()

    run = await adapter.submit(request)
    aborted = await adapter.abort(run)

    assert aborted.status is ExternalRunStatus.ABORTED
    assert aborted.provider_execution_id == run.provider_execution_id
    assert aborted.submitted_at == run.submitted_at
    assert aborted.canvas_id == run.canvas_id


@pytest.mark.asyncio
async def test_result_returns_artifact_refs() -> None:
    """Result returns typed ArtifactRef tuples; no raw bytes or secrets."""
    adapter = DeterministicLabExecutionAdapter()
    request = _execution_request()

    run = await adapter.submit(request)
    refs = await adapter.result(run)

    assert len(refs) > 0
    for ref in refs:
        assert ref.artifact_ref_id
        assert ref.canvas_id == request.canvas_id
        assert ref.execution_run_id == request.execution_run_id
        assert ref.analysis_run_id is None
        assert len(ref.content_hash) == 64  # SHA-256
        assert ref.logical_uri
        assert not ref.logical_uri.startswith(("http://", "https://", "?", "#"))
        assert ref.media_type
        assert ref.role in (ArtifactRole.RAW, ArtifactRole.DERIVED, ArtifactRole.LOG)
        assert ref.evidence_kind is EvidenceKind.MOCK_OR_DRY_RUN
        assert ref.measured_receipt is None  # Mock → no receipt


@pytest.mark.asyncio
async def test_reconciliation_supported() -> None:
    """Adapter declares reconciliation capability."""
    adapter = DeterministicLabExecutionAdapter()
    assert adapter.reconciliation_supported is True


@pytest.mark.asyncio
async def test_find_by_idempotency_key() -> None:
    """Find by idempotency key returns same run or None."""
    adapter = DeterministicLabExecutionAdapter()
    request = _execution_request()
    request_with_key = request.model_copy(update={"submit_intent_key": "key-find-1"})

    run = await adapter.submit(request_with_key)
    found = await adapter.find_by_idempotency_key("key-find-1")

    assert found is not None
    assert found.provider_execution_id == run.provider_execution_id

    # Not found
    not_found = await adapter.find_by_idempotency_key("key-does-not-exist")
    assert not_found is None


@pytest.mark.asyncio
async def test_dry_run_only_enforcement() -> None:
    """Dry-run adapter rejects non-dry-run modes and non-mock evidence with NOT_READY."""
    adapter = DeterministicLabExecutionAdapter()

    # Real mode not allowed — deterministic adapter returns NOT_READY
    request_real = _execution_request()
    request_real_mode = request_real.model_copy(update={"mode": RunMode.REAL})
    with pytest.raises(LabExecutionAdapterError) as exc:
        await adapter.submit(request_real_mode)
    assert exc.value.code is ExternalFailureCode.NOT_READY

    # Measured evidence not allowed — deterministic adapter returns NOT_READY
    request_measured = _execution_request()
    request_measured_evidence = request_measured.model_copy(update={"evidence_kind": EvidenceKind.MEASURED})
    with pytest.raises(LabExecutionAdapterError) as exc:
        await adapter.submit(request_measured_evidence)
    assert exc.value.code is ExternalFailureCode.NOT_READY


@pytest.mark.asyncio
async def test_typed_failures_no_raw_provider_text() -> None:
    """Failures are typed, never raw provider error strings."""
    adapter = DeterministicLabExecutionAdapter()
    request = _execution_request()
    request_real = request.model_copy(update={"mode": RunMode.REAL})

    with pytest.raises(LabExecutionAdapterError) as exc:
        await adapter.submit(request_real)
    assert isinstance(exc.value.code, ExternalFailureCode)
    assert exc.value.code is ExternalFailureCode.NOT_READY
    # Error message is the enum value, never a raw string
    assert str(exc.value) == ExternalFailureCode.NOT_READY.value
