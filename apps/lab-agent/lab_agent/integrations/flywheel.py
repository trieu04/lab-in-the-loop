"""Provider-neutral analysis boundary with a deterministic Flywheel dry run."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import NoReturn, Protocol, runtime_checkable

from lab_agent.models.execution import (
    AnalysisRequest,
    AnalysisRun,
    ArtifactRef,
    ArtifactRole,
    EvidenceKind,
    ExternalFailureCode,
    ExternalRunStatus,
    RunMode,
)

DRY_RUN_EVIDENCE_LABEL = "DRY RUN / MOCK — NOT MEASURED"

def _run_key(tenant_id: str, idempotency_key: str) -> str:
    return f"{tenant_id}:{idempotency_key}"

class FlywheelAdapterError(RuntimeError):
    """A safe, typed failure from an analysis boundary."""

    def __init__(self, code: ExternalFailureCode) -> None:
        self.code = code
        value = code.value if isinstance(code, ExternalFailureCode) else code
        super().__init__(value)

class FlywheelNotReadyError(FlywheelAdapterError):
    """The real analysis adapter has not passed declared readiness gates."""

    def __init__(self) -> None:
        super().__init__(ExternalFailureCode.NOT_READY)


class FlywheelImplementationNotInstalledError(FlywheelAdapterError):
    """No real Flywheel or HPC integration exists in the Phase 8 dry-run scope."""

    def __init__(self) -> None:
        super().__init__(ExternalFailureCode.IMPLEMENTATION_NOT_INSTALLED)


@runtime_checkable
class FlywheelAdapter(Protocol):
    """Async analysis port with cancel and authoritative idempotency reconciliation."""

    @property
    def reconciliation_supported(self) -> bool:
        """Whether an ambiguous job can be authoritatively located."""
        ...

    async def submit(self, request: AnalysisRequest) -> AnalysisRun: ...
    async def status(self, run: AnalysisRun) -> ExternalRunStatus: ...
    async def cancel(self, run: AnalysisRun) -> AnalysisRun: ...
    async def result(self, run: AnalysisRun) -> tuple[ArtifactRef, ...]: ...
    async def find_by_idempotency_key(self, tenant_id: str, key: str | None = None) -> AnalysisRun | None: ...


@dataclass(frozen=True)
class RealFlywheelReadiness:
    """Abstract future gates; no real API, endpoint, or credential schema is assumed."""

    enabled: bool = False
    locality_approved: bool = False
    retention_approved: bool = False

    @property
    def ready(self) -> bool:
        return self.enabled and self.locality_approved and self.retention_approved


@dataclass
class DeterministicFlywheelAdapter:
    """Memory-only analysis dry run that produces derived mock evidence only."""

    adapter_name: str = "deterministic-flywheel-dry-run"
    adapter_version: str = "1"
    visible_label: str = DRY_RUN_EVIDENCE_LABEL
    _runs: dict[str, AnalysisRun] = field(default_factory=dict, init=False, repr=False)

    @property
    def reconciliation_supported(self) -> bool:
        return True

    async def submit(self, request: AnalysisRequest) -> AnalysisRun:
        self._require_dry_run(request.mode, request.evidence_kind)
        existing = self._runs.get(_run_key(request.tenant_id, request.submit_intent_key))
        if existing is not None:
            if existing.input_hash != request.input_hash:
                raise FlywheelAdapterError(ExternalFailureCode.INVALID_SCHEMA)
            return existing
        run = AnalysisRun(
            **request.model_dump(),
            status=ExternalRunStatus.SUCCEEDED,
            provider_job_id=f"dry-run-flywheel-{request.input_hash[:32]}",
            submitted_at=request.requested_at,
            finished_at=request.requested_at,
        )
        self._runs[_run_key(request.tenant_id, request.submit_intent_key)] = run
        return run

    async def status(self, run: AnalysisRun) -> ExternalRunStatus:
        known = self._runs.get(_run_key(run.tenant_id, run.submit_intent_key))
        return known.status if known is not None else run.status

    async def cancel(self, run: AnalysisRun) -> AnalysisRun:
        self._require_dry_run(run.mode, run.evidence_kind)
        cancelled = run.model_copy(
            update={
                "status": ExternalRunStatus.ABORTED,
                "failure_code": ExternalFailureCode.ABORTED,
                "finished_at": run.finished_at or run.requested_at,
            }
        )
        self._runs[_run_key(run.tenant_id, run.submit_intent_key)] = cancelled
        return cancelled

    async def result(self, run: AnalysisRun) -> tuple[ArtifactRef, ...]:
        self._require_dry_run(run.mode, run.evidence_kind)
        if run.status is ExternalRunStatus.ABORTED:
            raise FlywheelAdapterError(ExternalFailureCode.ABORTED)
        source_ids = ":".join(run.source_artifact_ref_ids)
        digest = hashlib.sha256(f"flywheel-dry-run:{run.submit_intent_key}:{source_ids}".encode()).hexdigest()
        return (
            ArtifactRef(
                artifact_ref_id=f"dry-run-flywheel-derived-{digest[:24]}",
                tenant_id=run.tenant_id,
                canvas_id=run.canvas_id,
                analysis_run_id=run.analysis_run_id,
                content_hash=digest,
                logical_uri=f"mock://dry-run/flywheel-analysis/{digest}",
                media_type="application/vnd.lab-agent.dry-run+json",
                classification="mock_or_dry_run",
                role=ArtifactRole.DERIVED,
                evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
                retention_until=run.requested_at + timedelta(days=1),
                recorded_at=run.requested_at,
            ),
        )

    async def find_by_idempotency_key(self, tenant_id: str, key: str | None = None) -> AnalysisRun | None:
        return self._runs.get(_run_key("default" if key is None else tenant_id, tenant_id if key is None else key))

    @staticmethod
    def _require_dry_run(mode: RunMode, evidence_kind: EvidenceKind) -> None:
        if mode is not RunMode.DRY_RUN or evidence_kind is not EvidenceKind.MOCK_OR_DRY_RUN:
            raise FlywheelNotReadyError()


@dataclass(frozen=True)
class DisabledRealFlywheelAdapter:
    """Fail-closed sentinel; it cannot start, inspect, cancel, or query real jobs."""

    readiness: RealFlywheelReadiness

    @property
    def reconciliation_supported(self) -> bool:
        return False

    async def submit(self, request: AnalysisRequest) -> AnalysisRun:
        del request
        self._raise_disabled()

    async def status(self, run: AnalysisRun) -> ExternalRunStatus:
        del run
        self._raise_disabled()

    async def cancel(self, run: AnalysisRun) -> AnalysisRun:
        del run
        self._raise_disabled()

    async def result(self, run: AnalysisRun) -> tuple[ArtifactRef, ...]:
        del run
        self._raise_disabled()

    async def find_by_idempotency_key(self, tenant_id: str, key: str | None = None) -> AnalysisRun | None:
        del tenant_id, key
        self._raise_disabled()

    def _raise_disabled(self) -> NoReturn:
        if self.readiness.ready:
            raise FlywheelImplementationNotInstalledError()
        raise FlywheelNotReadyError()


__all__ = [
    "DRY_RUN_EVIDENCE_LABEL",
    "DeterministicFlywheelAdapter",
    "DisabledRealFlywheelAdapter",
    "FlywheelAdapter",
    "FlywheelAdapterError",
    "FlywheelImplementationNotInstalledError",
    "FlywheelNotReadyError",
    "RealFlywheelReadiness",
]
