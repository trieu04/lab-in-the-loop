"""Flywheel (analysis) adapter contract tests: typed requests/results, idempotency, status, cancel, reconciliation, typed failures, no secrets/raw bodies."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from lab_agent.integrations.flywheel import (
    DeterministicFlywheelAdapter,
    FlywheelAdapter,
    FlywheelAdapterError,
)
from lab_agent.models.execution import (
    AnalysisRequest,
    ArtifactRole,
    EvidenceKind,
    ExternalFailureCode,
    ExternalRunStatus,
    RunMode,
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _analysis_request() -> AnalysisRequest:
    now = datetime.now(UTC)
    return AnalysisRequest(
        request_id="req-1",
        analysis_run_id="analysis-1",
        canvas_id="canvas-1",
        execution_run_id="exec-1",
        source_artifact_ref_ids=("ref-1", "ref-2"),
        adapter_name="deterministic-flywheel",
        adapter_version="1",
        mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        submit_intent_key="intent-key-1",
        input_hash=_hash("input"),
        requested_at=now,
    )


@pytest.mark.asyncio
async def test_flywheel_adapter_is_protocol() -> None:
    """Adapter protocol is runtime-checkable and verifiable."""
    adapter = DeterministicFlywheelAdapter()
    assert isinstance(adapter, FlywheelAdapter)


@pytest.mark.asyncio
async def test_submit_returns_typed_analysis_run() -> None:
    """Submit returns a typed AnalysisRun with stable idempotency key."""
    adapter = DeterministicFlywheelAdapter()
    request = _analysis_request()

    run = await adapter.submit(request)

    assert run.analysis_run_id == request.analysis_run_id
    assert run.canvas_id == request.canvas_id
    assert run.submit_intent_key == request.submit_intent_key
    assert run.status is ExternalRunStatus.SUCCEEDED
    assert run.provider_job_id is not None
    assert run.submitted_at is not None
    assert run.finished_at is not None
    assert len(run.provider_job_id) > 0


@pytest.mark.asyncio
async def test_idempotent_submit_converges() -> None:
    """Same idempotency key returns same run; different input fails."""
    adapter = DeterministicFlywheelAdapter()
    request1 = _analysis_request()
    request1_updated = request1.model_copy(update={"submit_intent_key": "key-1"})

    run1 = await adapter.submit(request1_updated)

    # Same key, same input → same run
    request2 = _analysis_request()
    request2_updated = request2.model_copy(update={"submit_intent_key": "key-1", "input_hash": request1_updated.input_hash})

    run2 = await adapter.submit(request2_updated)
    assert run2.provider_job_id == run1.provider_job_id

    # Same key, different input → error
    request3 = _analysis_request()
    request3_updated = request3.model_copy(update={"submit_intent_key": "key-1", "input_hash": _hash("different")})

    with pytest.raises(FlywheelAdapterError) as exc:
        await adapter.submit(request3_updated)
    assert exc.value.code is ExternalFailureCode.INVALID_SCHEMA


@pytest.mark.asyncio
async def test_status_returns_run_status() -> None:
    """Status returns current ExternalRunStatus."""
    adapter = DeterministicFlywheelAdapter()
    request = _analysis_request()

    run = await adapter.submit(request)
    status = await adapter.status(run)

    assert status is ExternalRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_abort_preserves_history() -> None:
    """Cancel returns updated run with abort marker; preserves prior history."""
    adapter = DeterministicFlywheelAdapter()
    request = _analysis_request()

    run = await adapter.submit(request)
    cancelled = await adapter.cancel(run)

    assert cancelled.status is ExternalRunStatus.ABORTED
    assert cancelled.provider_job_id == run.provider_job_id
    assert cancelled.submitted_at == run.submitted_at
    assert cancelled.canvas_id == run.canvas_id


@pytest.mark.asyncio
async def test_result_returns_artifact_refs() -> None:
    """Result returns typed ArtifactRef tuples; no raw bytes or secrets."""
    adapter = DeterministicFlywheelAdapter()
    request = _analysis_request()

    run = await adapter.submit(request)
    refs = await adapter.result(run)

    assert len(refs) > 0
    for ref in refs:
        assert ref.artifact_ref_id
        assert ref.canvas_id == request.canvas_id
        assert ref.execution_run_id is None
        assert ref.analysis_run_id == request.analysis_run_id
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
    adapter = DeterministicFlywheelAdapter()
    assert adapter.reconciliation_supported is True


@pytest.mark.asyncio
async def test_find_by_idempotency_key() -> None:
    """Find by idempotency key returns same run or None."""
    adapter = DeterministicFlywheelAdapter()
    request = _analysis_request()
    request_updated = request.model_copy(update={"submit_intent_key": "key-find-1"})

    run = await adapter.submit(request_updated)
    found = await adapter.find_by_idempotency_key("key-find-1")

    assert found is not None
    assert found.provider_job_id == run.provider_job_id

    # Not found
    not_found = await adapter.find_by_idempotency_key("key-does-not-exist")
    assert not_found is None


@pytest.mark.asyncio
async def test_dry_run_only_enforcement() -> None:
    """Dry-run adapter rejects non-dry-run modes and non-mock evidence with NOT_READY."""
    adapter = DeterministicFlywheelAdapter()

    # Real mode not allowed — deterministic adapter returns NOT_READY
    request_real = _analysis_request()
    request_real_updated = request_real.model_copy(update={"mode": RunMode.REAL})
    with pytest.raises(FlywheelAdapterError) as exc:
        await adapter.submit(request_real_updated)
    assert exc.value.code is ExternalFailureCode.NOT_READY

    # Measured evidence not allowed — deterministic adapter returns NOT_READY
    request_measured = _analysis_request()
    request_measured_updated = request_measured.model_copy(update={"evidence_kind": EvidenceKind.MEASURED})
    with pytest.raises(FlywheelAdapterError) as exc:
        await adapter.submit(request_measured_updated)
    assert exc.value.code is ExternalFailureCode.NOT_READY


@pytest.mark.asyncio
async def test_typed_failures_no_raw_provider_text() -> None:
    """Failures are typed, never raw provider error strings."""
    adapter = DeterministicFlywheelAdapter()
    request = _analysis_request()
    request_real = request.model_copy(update={"mode": RunMode.REAL})

    with pytest.raises(FlywheelAdapterError) as exc:
        await adapter.submit(request_real)
    assert isinstance(exc.value.code, ExternalFailureCode)
    assert exc.value.code is ExternalFailureCode.NOT_READY
    # Error message is the enum value, never a raw string
    assert str(exc.value) == ExternalFailureCode.NOT_READY.value
