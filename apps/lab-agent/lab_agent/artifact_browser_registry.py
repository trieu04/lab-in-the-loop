"""Browser bucket and fallback-grid registry for generated artifacts."""

from __future__ import annotations

from lab_agent.durable_writes import _CLOSED_X, _RESULT_X, _ROW, _SETUP_X
from lab_agent.models.artifact import ArtifactType

_BUCKETS = {
    ArtifactType.SETUP: "setups",
    ArtifactType.RESULT: "results",
    ArtifactType.CLOSED: "closeds",
    ArtifactType.NEEDS_INPUT: "needs_inputs",
    ArtifactType.IN_SILICO: "validations",
    ArtifactType.APPROVAL_STATUS: "validations",
    ArtifactType.EXECUTION: "executions",
    ArtifactType.ANALYSIS: "analyses",
    ArtifactType.KNOWLEDGE: "knowledge",
    ArtifactType.CONFLICT: "conflicts",
}
_NEEDS_INPUT_X = 780.0
_IN_SILICO_X = 1040.0
_APPROVAL_STATUS_X = 1560.0
_EXECUTION_X = 1820.0
_ANALYSIS_X = 2080.0
_KNOWLEDGE_X = 2340.0
_CONFLICT_X = 2600.0

_FALLBACK_COLUMNS = {
    ArtifactType.SETUP: _SETUP_X,
    ArtifactType.RESULT: _RESULT_X,
    ArtifactType.NEEDS_INPUT: _NEEDS_INPUT_X,
    ArtifactType.IN_SILICO: _IN_SILICO_X,
    ArtifactType.APPROVAL_STATUS: _APPROVAL_STATUS_X,
    ArtifactType.EXECUTION: _EXECUTION_X,
    ArtifactType.ANALYSIS: _ANALYSIS_X,
    ArtifactType.KNOWLEDGE: _KNOWLEDGE_X,
    ArtifactType.CONFLICT: _CONFLICT_X,
}


def browser_bucket(artifact_type: ArtifactType) -> str:
    """Return the stable, display-only probe bucket for one artifact type."""

    return _BUCKETS[artifact_type]


def fallback_layout(artifact_type: ArtifactType, round_index: int) -> tuple[float, float]:
    """Return deterministic placement when source geometry is unavailable."""

    if artifact_type in _FALLBACK_COLUMNS:
        return _FALLBACK_COLUMNS[artifact_type], round_index * _ROW
    return _CLOSED_X, (round_index + 1) * _ROW


__all__ = ["browser_bucket", "fallback_layout"]
