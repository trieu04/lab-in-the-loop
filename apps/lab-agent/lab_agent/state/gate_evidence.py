"""Shared immutable-evidence types and canonical record serialization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from lab_agent.models.validation import GateApproval, InSilicoResult

_EvidenceModel = TypeVar("_EvidenceModel", bound=BaseModel)


class GateEvidenceConflictError(RuntimeError):
    """A durable evidence identity or approver slot was reused incompatibly."""


class ValidationEvidenceNotFoundError(RuntimeError):
    """An approval does not reference validation evidence on its canvas."""


@dataclass(frozen=True)
class GateEvidenceProjection:
    """Current gate evidence record for one immutable tenant/canvas scope."""

    tenant_id: str
    canvas_id: str
    validation_result: InSilicoResult | None
    approvals: tuple[GateApproval, ...]


def canonical_evidence_json(record: _EvidenceModel) -> str:
    """Canonical JSON makes replay equality independent of Python object identity."""

    return json.dumps(
        record.model_dump(mode="json", exclude_none=False),
        allow_nan=False,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "GateEvidenceConflictError",
    "GateEvidenceProjection",
    "ValidationEvidenceNotFoundError",
    "canonical_evidence_json",
]
