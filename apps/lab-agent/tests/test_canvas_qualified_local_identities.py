"""Regression coverage for tenant/canvas-qualified durable local identities."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lab_agent.models.execution import (
    ArtifactRef,
    ArtifactRole,
    EvidenceKind,
    ExecutionRequest,
    ExternalRunStatus,
    RunMode,
)
from lab_agent.state_store import StateStore
from lab_agent.tenant import TenantContext


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _store(path: Path) -> StateStore:
    return StateStore(path, tenant_context=TenantContext("tenant", {"canvas-a", "canvas-b"}, "example.test"))


def _request(canvas_id: str) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id="tenant",
        request_id="request",
        execution_run_id="run",
        canvas_id=canvas_id,
        setup_id="setup",
        round=0,
        proposal_hash=_hash("proposal"),
        validation_result_hash=_hash("validation"),
        adapter_name="deterministic",
        adapter_version="1",
        mode=RunMode.DRY_RUN,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        submit_intent_key="submit",
        input_hash=_hash("input"),
        requested_at=datetime.now(UTC),
    )


def _ref(canvas_id: str) -> ArtifactRef:
    return ArtifactRef(
        tenant_id="tenant",
        artifact_ref_id="ref",
        canvas_id=canvas_id,
        execution_run_id="run",
        role=ArtifactRole.RAW,
        evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
        content_hash=_hash("content"),
        logical_uri="mock://dry-run/ref",
        media_type="application/json",
        classification="mock",
        retention_until=datetime.now(UTC),
        recorded_at=datetime.now(UTC),
    )


def test_same_tenant_can_reuse_local_run_ref_and_intent_ids_across_canvases(tmp_path: Path) -> None:
    store = _store(tmp_path / "state.db")
    try:
        first, created_first = store.prepare_execution_run(_request("canvas-a"))
        second, created_second = store.prepare_execution_run(_request("canvas-b"))
        assert (first.canvas_id, second.canvas_id, created_first, created_second) == (
            "canvas-a", "canvas-b", True, True
        )

        store.append_artifact_ref(_ref("canvas-a"))
        store.append_artifact_ref(_ref("canvas-b"))
        assert store.get_artifact_ref("canvas-a", "ref") is not None
        assert store.get_artifact_ref("canvas-b", "ref") is not None

        store.prepare_intent(
            idempotency_key="intent", canvas_id="canvas-a", kind="browser", input_hash=_hash("input")
        )
        store.prepare_intent(
            idempotency_key="intent", canvas_id="canvas-b", kind="browser", input_hash=_hash("input")
        )
        assert store.get_intent("intent", canvas_id="canvas-a") is not None
        assert store.get_intent("intent", canvas_id="canvas-b") is not None

        with pytest.raises(RuntimeError):
            store.transition_execution_run("canvas-b", "missing", status=ExternalRunStatus.SUBMITTED)
    finally:
        store.close()
