"""P7b validation DTO scope and scientific-hash compatibility coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lab_agent.integrations.in_silico import InSilicoSchemaError
from lab_agent.models.validation import (
    InSilicoResult,
    ValidationDecision,
    ValidationMode,
    hash_validation_result,
)
from lab_agent.orchestrator_validation import _validate_result
from lab_agent.state.gate_evidence import GateEvidenceConflictError
from lab_agent.state_store import StateStore
from lab_agent.tenant import TenantContext

_NOW = datetime(2026, 7, 24, tzinfo=UTC)


def _result(*, tenant_id: str = "tenant", canvas_id: str = "canvas-a") -> InSilicoResult:
    return InSilicoResult(
        tenant_id=tenant_id,
        canvas_id=canvas_id,
        validation_id="validation",
        proposal_hash="a" * 64,
        decision=ValidationDecision.PROCEED,
        predicted_outcome="Dry-run structural validation passed.",
        confidence=0.9,
        uncertainty="No scientific simulation was performed.",
        adapter_name="deterministic",
        adapter_version="1",
        algorithm_version="rules-v1",
        mode=ValidationMode.DRY_RUN,
        completed_at=_NOW,
    )


def test_scoped_result_round_trips_without_changing_scientific_hash(tmp_path: Path) -> None:
    result = _result()
    historical = _result(tenant_id="default", canvas_id="default")
    assert hash_validation_result(result) == hash_validation_result(historical)

    store = StateStore(
        tmp_path / "state.db",
        tenant_context=TenantContext("tenant", {"canvas-a", "canvas-b"}, "example.test"),
    )
    try:
        assert store.append_validation_result("canvas-a", result) == result
        assert store.get_validation_result("canvas-a", result.validation_id) == result
        with pytest.raises(GateEvidenceConflictError):
            store.append_validation_result("canvas-b", result)
    finally:
        store.close()


def test_adapter_result_with_wrong_scope_fails_before_persistence() -> None:
    with pytest.raises(InSilicoSchemaError, match="scope"):
        _validate_result(
            _result(canvas_id="canvas-b"),
            tenant_id="tenant",
            canvas_id="canvas-a",
            proposal_hash="a" * 64,
        )
