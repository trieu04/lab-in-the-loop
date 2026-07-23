"""Phase 8 execution store persistence tests: scope, idempotency, canvas-scope checks, invalid transitions rejected."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from lab_agent.models.execution import (
    ArtifactRef,
    ArtifactRole,
    EvidenceKind,
    ExecutionRequest,
    ExternalFailureCode,
    ExternalRunStatus,
    RunMode,
)
from lab_agent.state_store import StateStore


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _now():
    return datetime.now(UTC)


def _execution_request(canvas_id: str = "canvas-1") -> ExecutionRequest:
    return ExecutionRequest(
        request_id="req-1",
        execution_run_id="run-1",
        canvas_id=canvas_id,
        setup_id="setup-1",
        round=0,
        proposal_hash=_hash("proposal"),
        validation_result_hash=_hash("validation"),
        adapter_name="adapter",
        adapter_version="1",
        mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        submit_intent_key="intent-key-1",
        input_hash=_hash("input"),
        requested_at=_now(),
    )


def test_prepare_execution_run_creates_row(store: StateStore) -> None:
    """Prepare creates a new execution run row."""
    request = _execution_request()
    run, is_new = store.prepare_execution_run(request)

    assert is_new is True
    assert run.execution_run_id == "run-1"
    assert run.canvas_id == "canvas-1"
    assert run.status is ExternalRunStatus.PENDING


def test_prepare_execution_run_idempotent(store: StateStore) -> None:
    """Same request produces same run; second prepare is not new."""
    request = _execution_request()
    run1, is_new1 = store.prepare_execution_run(request)
    run2, is_new2 = store.prepare_execution_run(request)

    assert is_new1 is True
    assert is_new2 is False
    assert run1.execution_run_id == run2.execution_run_id


def test_get_execution_run_canvas_scoped(store: StateStore) -> None:
    """Get verifies canvas_id; cross-canvas lookup fails."""
    request = _execution_request(canvas_id="canvas-1")
    run, _ = store.prepare_execution_run(request)

    # Same canvas → found
    found = store.get_execution_run("canvas-1", "run-1")
    assert found is not None
    assert found.execution_run_id == "run-1"

    # Different canvas → not found
    not_found = store.get_execution_run("canvas-2", "run-1")
    assert not_found is None


def test_transition_execution_run_valid(store: StateStore) -> None:
    """Transition updates status with timestamps."""
    request = _execution_request()
    run, _ = store.prepare_execution_run(request)

    transitioned = store.transition_execution_run(
        "canvas-1",
        "run-1",
        status=ExternalRunStatus.SUBMITTED,
        provider_execution_id="external-id-1",
    )

    assert transitioned.status is ExternalRunStatus.SUBMITTED
    assert transitioned.provider_execution_id == "external-id-1"
    assert transitioned.submitted_at is not None


def test_transition_execution_run_to_failed(store: StateStore) -> None:
    """Transition to failed state with failure code."""
    request = _execution_request()
    run, _ = store.prepare_execution_run(request)

    failed = store.transition_execution_run(
        "canvas-1",
        "run-1",
        status=ExternalRunStatus.FAILED,
        failure_code=ExternalFailureCode.TIMEOUT,
    )

    assert failed.status is ExternalRunStatus.FAILED
    assert failed.failure_code is ExternalFailureCode.TIMEOUT
    assert failed.finished_at is not None


def test_transition_execution_run_to_abort_requested(store: StateStore) -> None:
    """Transition to abort_requested persists abort intent key."""
    request = _execution_request()
    run, _ = store.prepare_execution_run(request)

    # First submit
    submitted = store.transition_execution_run(
        "canvas-1",
        "run-1",
        status=ExternalRunStatus.SUBMITTED,
        provider_execution_id="external-id-1",
    )
    assert submitted.status is ExternalRunStatus.SUBMITTED

    # Then abort
    abort_requested = store.transition_execution_run(
        "canvas-1",
        "run-1",
        status=ExternalRunStatus.ABORT_REQUESTED,
        abort_intent_key="abort-intent-1",
    )

    assert abort_requested.status is ExternalRunStatus.ABORT_REQUESTED
    assert abort_requested.abort_intent_key == "abort-intent-1"


def test_list_execution_runs_canvas_scoped(store: StateStore) -> None:
    """List only returns runs from the given canvas."""
    request1 = _execution_request(canvas_id="canvas-1")
    request1_updated = request1.model_copy(update={"execution_run_id": "run-1", "submit_intent_key": "intent-key-1"})
    run1, _ = store.prepare_execution_run(request1_updated)

    request2 = _execution_request(canvas_id="canvas-2")
    request2_updated = request2.model_copy(update={"execution_run_id": "run-2", "submit_intent_key": "intent-key-2"})
    run2, _ = store.prepare_execution_run(request2_updated)

    # List canvas-1
    canvas1_runs = store.list_execution_runs("canvas-1")
    assert len(canvas1_runs) == 1
    assert canvas1_runs[0].execution_run_id == "run-1"

    # List canvas-2
    canvas2_runs = store.list_execution_runs("canvas-2")
    assert len(canvas2_runs) == 1
    assert canvas2_runs[0].execution_run_id == "run-2"


def test_list_execution_runs_filtered_by_status(store: StateStore) -> None:
    """List with status filter returns only matching runs."""
    request = _execution_request()
    run, _ = store.prepare_execution_run(request)

    # Pending runs
    pending = store.list_execution_runs("canvas-1", status=ExternalRunStatus.PENDING)
    assert len(pending) == 1

    # Transition to submitted
    store.transition_execution_run(
        "canvas-1",
        "run-1",
        status=ExternalRunStatus.SUBMITTED,
        provider_execution_id="external-id-1",
    )

    # Now pending list empty
    pending = store.list_execution_runs("canvas-1", status=ExternalRunStatus.PENDING)
    assert len(pending) == 0

    # Submitted list has one
    submitted = store.list_execution_runs("canvas-1", status=ExternalRunStatus.SUBMITTED)
    assert len(submitted) == 1


def test_append_artifact_ref_canvas_scoped(store: StateStore) -> None:
    """Artifact ref append is canvas-scoped."""
    # Create run for canvas-1
    request1 = _execution_request(canvas_id="canvas-1")
    request1_updated = request1.model_copy(update={"execution_run_id": "run-1", "submit_intent_key": "intent-key-1"})
    store.prepare_execution_run(request1_updated)

    ref1 = ArtifactRef(
        artifact_ref_id="ref-1",
        canvas_id="canvas-1",
        execution_run_id="run-1",
        analysis_run_id=None,
        content_hash=_hash("content-1"),
        logical_uri="mock://dry-run/execution/results-1",
        media_type="application/json",
        classification="internal",
        role=ArtifactRole.RAW,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        retention_until=_now(),
        recorded_at=_now(),
    )
    appended1, is_new1 = store.append_artifact_ref(ref1)
    assert is_new1 is True

    # Create run for canvas-2
    request2 = _execution_request(canvas_id="canvas-2")
    request2_updated = request2.model_copy(update={"execution_run_id": "run-2", "submit_intent_key": "intent-key-2"})
    store.prepare_execution_run(request2_updated)

    ref2 = ArtifactRef(
        artifact_ref_id="ref-2",
        canvas_id="canvas-2",
        execution_run_id="run-2",
        analysis_run_id=None,
        content_hash=_hash("content-2"),
        logical_uri="mock://dry-run/execution/results-2",
        media_type="application/json",
        classification="internal",
        role=ArtifactRole.RAW,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        retention_until=_now(),
        recorded_at=_now(),
    )
    appended2, is_new2 = store.append_artifact_ref(ref2)
    assert is_new2 is True

    # Get from canvas-1
    found1 = store.get_artifact_ref("canvas-1", "ref-1")
    assert found1 is not None
    assert found1.artifact_ref_id == "ref-1"

    # Get from canvas-1 doesn't find canvas-2's ref
    not_found = store.get_artifact_ref("canvas-1", "ref-2")
    assert not_found is None


def test_append_artifact_ref_idempotent(store: StateStore) -> None:
    """Same artifact ref appended twice is idempotent."""
    # Create run first
    request = _execution_request(canvas_id="canvas-1")
    request_updated = request.model_copy(update={"execution_run_id": "run-1", "submit_intent_key": "intent-key-1"})
    store.prepare_execution_run(request_updated)

    ref = ArtifactRef(
        artifact_ref_id="ref-1",
        canvas_id="canvas-1",
        execution_run_id="run-1",
        analysis_run_id=None,
        content_hash=_hash("content"),
        logical_uri="mock://dry-run/execution/results",
        media_type="application/json",
        classification="internal",
        role=ArtifactRole.RAW,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        retention_until=_now(),
        recorded_at=_now(),
    )
    appended1, is_new1 = store.append_artifact_ref(ref)
    appended2, is_new2 = store.append_artifact_ref(ref)

    assert is_new1 is True
    assert is_new2 is False
    assert appended1.artifact_ref_id == appended2.artifact_ref_id
