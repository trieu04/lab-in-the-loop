"""Fail-closed in-silico adapter boundary and deterministic dry-run implementation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from lab_agent.models.validation import (
    InSilicoRequest,
    InSilicoResult,
    ValidationDecision,
    ValidationMode,
)


class InSilicoAdapterError(RuntimeError):
    """Base error for validation failures that must not advance workflow state."""


class InSilicoTimeoutError(InSilicoAdapterError):
    """Validation exceeded its configured deadline."""


class InSilicoProviderError(InSilicoAdapterError):
    """Validation provider failed without a usable typed result."""


class InSilicoSchemaError(InSilicoAdapterError):
    """Validation output failed schema validation."""


class InSilicoNotReadyError(InSilicoAdapterError):
    """A real adapter is disabled or missing required approvals/configuration."""


class MockFailureMode(StrEnum):
    """Deterministic failure injected for dry-run contract testing."""

    NONE = "none"
    TIMEOUT = "timeout"
    PROVIDER_FAILURE = "provider_failure"
    INVALID_SCHEMA = "invalid_schema"


@runtime_checkable
class InSilicoAdapter(Protocol):
    """Typed async boundary shared by dry-run and future real validators."""

    async def validate(self, request: InSilicoRequest) -> InSilicoResult:
        """Validate one exact proposal or raise a typed fail-closed error."""
        ...


@dataclass(frozen=True)
class RealAdapterReadiness:
    """Explicit gates required before a future real adapter may be implemented."""

    endpoint: str | None = None
    security_approved: bool = False
    sla_approved: bool = False
    locality_authorized: bool = False
    identity_provider_ready: bool = False

    @property
    def blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if not self.endpoint or not self.endpoint.startswith("https://"):
            blockers.append("https_endpoint")
        if not self.security_approved:
            blockers.append("security_approval")
        if not self.sla_approved:
            blockers.append("sla_approval")
        if not self.locality_authorized:
            blockers.append("locality_authorization")
        if not self.identity_provider_ready:
            blockers.append("identity_provider")
        return tuple(blockers)

    @property
    def ready(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class DeterministicInSilicoAdapter:
    """Local structural dry run; it makes no scientific or provider call claims."""

    failure_mode: MockFailureMode = MockFailureMode.NONE
    adapter_version: str = "1"
    algorithm_version: str = "structural-rules-v1"

    async def validate(self, request: InSilicoRequest) -> InSilicoResult:
        self._raise_injected_failure()
        setup = request.setup
        risks: tuple[str, ...]
        changes: tuple[str, ...]

        if not setup.steps or not setup.expected_readouts:
            decision = ValidationDecision.REJECT
            outcome = "Dry-run structural validation found no executable steps or readouts."
            confidence = 1.0
            risks = ("structurally-incomplete-proposal", "dry-run-only")
            changes = ("Add executable steps and expected readouts before resubmission.",)
        elif not setup.hypothesis or not setup.success_criteria or not setup.conditions:
            decision = ValidationDecision.REVISE
            outcome = "Dry-run structural validation found an incomplete review contract."
            confidence = 0.9
            risks = ("incomplete-review-contract", "dry-run-only")
            changes = ("Add a hypothesis, conditions, and measurable success criteria.",)
        else:
            decision = ValidationDecision.PROCEED
            outcome = "Dry-run structural validation found the required proposal fields."
            confidence = 0.95
            risks = ("dry-run-only",)
            changes = ()

        return InSilicoResult(
            tenant_id=request.tenant_id,
            canvas_id=request.canvas_id,
            validation_id=f"dry-run-{request.proposal_hash[:24]}",
            proposal_hash=request.proposal_hash,
            decision=decision,
            predicted_outcome=outcome,
            confidence=confidence,
            uncertainty="No scientific simulation or external provider was executed.",
            assumptions=("This result checks structure only, not scientific validity.",),
            risk_flags=risks,
            recommended_changes=changes,
            adapter_name="deterministic-mock",
            adapter_version=self.adapter_version,
            algorithm_version=self.algorithm_version,
            mode=ValidationMode.DRY_RUN,
            completed_at=request.requested_at,
        )

    def _raise_injected_failure(self) -> None:
        errors: dict[MockFailureMode, type[InSilicoAdapterError]] = {
            MockFailureMode.TIMEOUT: InSilicoTimeoutError,
            MockFailureMode.PROVIDER_FAILURE: InSilicoProviderError,
            MockFailureMode.INVALID_SCHEMA: InSilicoSchemaError,
        }
        error_type = errors.get(self.failure_mode)
        if error_type is not None:
            raise error_type(self.failure_mode.value)


@dataclass(frozen=True)
class DisabledRealInSilicoAdapter:
    """Non-dispatching placeholder that keeps real validation unreachable."""

    readiness: RealAdapterReadiness

    async def validate(self, request: InSilicoRequest) -> InSilicoResult:
        del request
        blockers = self.readiness.blockers
        if blockers:
            raise InSilicoNotReadyError("real adapter blocked: " + ",".join(blockers))
        raise InSilicoNotReadyError("real adapter implementation is not installed")


__all__ = [
    "DeterministicInSilicoAdapter",
    "DisabledRealInSilicoAdapter",
    "InSilicoAdapter",
    "InSilicoAdapterError",
    "InSilicoNotReadyError",
    "InSilicoProviderError",
    "InSilicoSchemaError",
    "InSilicoTimeoutError",
    "MockFailureMode",
    "RealAdapterReadiness",
]
