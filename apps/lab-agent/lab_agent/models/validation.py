"""Typed contracts for in-silico validation and human approval evidence."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from lab_agent.models.experiment import ExperimentSetup

HASH_ALGORITHM = "sha256"
HASH_SCHEMA_VERSION = "litl-canonical-json-v1"


class ValidationDecision(StrEnum):
    """Fail-closed outcome of an in-silico validation run."""

    PROCEED = "proceed"
    REVISE = "revise"
    REJECT = "reject"


class ValidationMode(StrEnum):
    """Whether validation is a deterministic dry run or an approved real call."""

    DRY_RUN = "dry_run"
    REAL = "real"


class ApprovalRole(StrEnum):
    """Human roles permitted to authorize Phase 7 gates."""

    SCIENTIST = "scientist"
    LAB_LEAD = "lab_lead"


class ApprovalDecision(StrEnum):
    """Decision recorded by a verified human approver."""

    APPROVE = "approve"
    REJECT = "reject"


class IdentityAssertion(BaseModel):
    """Authenticated identity snapshot retained with an approval record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actor_id: str = Field(min_length=1, max_length=200)
    role: ApprovalRole
    credential_domain: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=100)
    verified_at: AwareDatetime
    authenticated: Literal[True] = True
    production_eligible: bool = False


class InSilicoRequest(BaseModel):
    """Exact proposal submitted to an in-silico adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(default="default", min_length=1, max_length=128)
    canvas_id: str = Field(default="default", min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)
    setup_id: str = Field(min_length=1, max_length=200)
    setup: ExperimentSetup
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    requested_at: AwareDatetime

    @model_validator(mode="after")
    def verify_proposal_hash(self) -> InSilicoRequest:
        if self.proposal_hash != hash_proposal(self.setup):
            raise ValueError("proposal_hash does not match setup")
        return self


class InSilicoResult(BaseModel):
    """Complete UC-LITL-03 validation result bound to one proposal hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(default="default", min_length=1, max_length=128)
    canvas_id: str = Field(default="default", min_length=1, max_length=200)
    validation_id: str = Field(min_length=1, max_length=200)
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: ValidationDecision
    predicted_outcome: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: str = Field(min_length=1, max_length=2000)
    assumptions: tuple[str, ...] = Field(default_factory=tuple)
    risk_flags: tuple[str, ...] = Field(default_factory=tuple)
    recommended_changes: tuple[str, ...] = Field(default_factory=tuple)
    adapter_name: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=100)
    algorithm_version: str = Field(min_length=1, max_length=100)
    mode: ValidationMode
    completed_at: AwareDatetime


class GateApproval(BaseModel):
    """Immutable human decision bound to exact proposal and validation hashes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: str = Field(default="default", min_length=1, max_length=128)
    canvas_id: str = Field(default="default", min_length=1, max_length=200)
    approval_id: str = Field(min_length=1, max_length=200)
    proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity: IdentityAssertion
    decision: ApprovalDecision
    rationale: str = Field(min_length=1, max_length=2000)
    decided_at: AwareDatetime
    validation_adapter: str = Field(min_length=1, max_length=100)
    validation_adapter_version: str = Field(min_length=1, max_length=100)
    validation_algorithm_version: str = Field(min_length=1, max_length=100)


def _canonical_hash(kind: str, value: BaseModel) -> str:
    payload = {
        "kind": kind,
        "schema_version": HASH_SCHEMA_VERSION,
        # Scope is ledger routing metadata, not scientific evidence. Excluding it
        # preserves the historical litl-canonical-json-v1 result hash contract.
        "value": value.model_dump(
            mode="json", exclude_none=False, exclude={"tenant_id", "canvas_id"}
        ),
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def hash_proposal(setup: ExperimentSetup) -> str:
    """Return the versioned canonical hash for an experiment proposal."""

    return _canonical_hash("experiment_setup", setup)


def hash_validation_result(result: InSilicoResult) -> str:
    """Return the versioned canonical hash for an in-silico result."""

    return _canonical_hash("in_silico_result", result)


__all__ = [
    "HASH_ALGORITHM",
    "HASH_SCHEMA_VERSION",
    "ApprovalDecision",
    "ApprovalRole",
    "GateApproval",
    "IdentityAssertion",
    "InSilicoRequest",
    "InSilicoResult",
    "ValidationDecision",
    "ValidationMode",
    "hash_proposal",
    "hash_validation_result",
]
