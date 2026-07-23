"""Phase 8 execution model and Browser payload safety boundaries."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from lab_agent.artifact_lifecycle_payloads import execution_payload
from lab_agent.models.execution import (
    ArtifactRef,
    ArtifactRole,
    EvidenceKind,
    ExecutionRequest,
    ExecutionRun,
    MeasuredEvidenceReceipt,
    RunMode,
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _request(**updates: object) -> ExecutionRequest:
    values: dict[str, object] = {
        "request_id": "req-1", "execution_run_id": "run-1", "canvas_id": "canvas-1",
        "setup_id": "setup-1", "round": 0, "proposal_hash": _hash("proposal"),
        "validation_result_hash": _hash("validation"), "adapter_name": "adapter",
        "adapter_version": "1", "mode": RunMode.DRY_RUN,
        "evidence_kind": EvidenceKind.MOCK_OR_DRY_RUN, "submit_intent_key": "key-1",
        "input_hash": _hash("input"), "requested_at": _now(),
    }
    values.update(updates)
    return ExecutionRequest(**values)


def _ref(**updates: object) -> ArtifactRef:
    values: dict[str, object] = {
        "artifact_ref_id": "ref-1", "canvas_id": "canvas-1", "execution_run_id": "run-1",
        "analysis_run_id": None, "content_hash": _hash("content"),
        "logical_uri": "mock://dry-run/lab-execution/content-1",
        "media_type": "application/json", "classification": "internal", "role": ArtifactRole.RAW,
        "evidence_kind": EvidenceKind.MOCK_OR_DRY_RUN, "retention_until": _now(),
        "recorded_at": _now(),
    }
    values.update(updates)
    return ArtifactRef(**values)


class TestExecutionRequestModelTruthBoundary:
    def test_dry_run_requires_mock_evidence(self) -> None:
        request = _request()
        assert request.evidence_kind is EvidenceKind.MOCK_OR_DRY_RUN
        with pytest.raises(ValueError, match="measured execution requires real mode"):
            _request(evidence_kind=EvidenceKind.MEASURED)

    def test_sandbox_measured_evidence_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="measured execution requires real mode"):
            _request(mode=RunMode.SANDBOX, evidence_kind=EvidenceKind.MEASURED)


class TestArtifactRefSafetyBoundary:
    def test_opaque_mock_reference_is_allowed(self) -> None:
        assert _ref().logical_uri == "mock://dry-run/lab-execution/content-1"

    @pytest.mark.parametrize("logical_uri", [
        "file:///results/artifact", "/var/private/results/artifact", "C:\\results\\artifact",
        "https://api.example.com/artifact?token=secret", "mock://dry-run/result?token=secret",
    ])
    def test_local_or_capability_location_is_rejected(self, logical_uri: str) -> None:
        with pytest.raises(ValueError, match="opaque logical reference"):
            _ref(logical_uri=logical_uri)

    def test_mock_artifact_rejects_measured_receipt(self) -> None:
        with pytest.raises(ValueError, match="mock artifact references cannot carry"):
            _ref(measured_receipt=MeasuredEvidenceReceipt(
                provider_job_id="job-1", capture_receipt_id="receipt-1",
                retained_location="mock://retained/receipt-1", captured_at=_now(),
            ))

    def test_measured_artifact_requires_safe_receipt(self) -> None:
        with pytest.raises(ValueError, match="measured artifact references require a capture receipt"):
            _ref(evidence_kind=EvidenceKind.MEASURED, measured_receipt=None)

    def test_browser_payload_does_not_project_logical_location(self) -> None:
        run = ExecutionRun(**_request().model_dump(by_alias=True))
        payload = execution_payload(run, (_ref(),))
        assert "logical_uri" not in payload["evidence_refs"][0]

    def test_artifact_requires_one_owner(self) -> None:
        with pytest.raises(ValueError, match="requires exactly one run owner"):
            _ref(analysis_run_id="analysis-1")
