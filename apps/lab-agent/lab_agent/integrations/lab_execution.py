"""Provider-neutral laboratory execution boundary with a deterministic dry run."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from typing import NoReturn, Protocol, runtime_checkable

from lab_agent.models.execution import (
    ArtifactRef,
    ArtifactRole,
    EvidenceKind,
    ExecutionRequest,
    ExecutionRun,
    ExternalFailureCode,
    ExternalRunStatus,
    RunMode,
)

DRY_RUN_EVIDENCE_LABEL = "DRY RUN / MOCK — NOT MEASURED"

def _run_key(tenant_id: str, idempotency_key: str) -> str:
    return f"{tenant_id}:{idempotency_key}"

class LabExecutionAdapterError(RuntimeError):
    """A safe, typed failure from a laboratory execution boundary."""

    def __init__(self, code: ExternalFailureCode) -> None:
        self.code = code
        value = code.value if isinstance(code, ExternalFailureCode) else code
        super().__init__(value)


class LabExecutionNotReadyError(LabExecutionAdapterError):
    """The real laboratory adapter has not passed its declared readiness gates."""

    def __init__(self) -> None:
        super().__init__(ExternalFailureCode.NOT_READY)


class LabExecutionImplementationNotInstalledError(LabExecutionAdapterError):
    """No real laboratory implementation exists in the Phase 8 dry-run scope."""

    def __init__(self) -> None:
        super().__init__(ExternalFailureCode.IMPLEMENTATION_NOT_INSTALLED)


@runtime_checkable
class LabExecutionAdapter(Protocol):
    """Async execution port with reconciliation before any repeat submission."""

    @property
    def reconciliation_supported(self) -> bool:
        """Whether an ambiguous submission can be authoritatively looked up."""
        ...

    async def submit(self, request: ExecutionRequest) -> ExecutionRun: ...
    async def status(self, run: ExecutionRun) -> ExternalRunStatus: ...
    async def abort(self, run: ExecutionRun) -> ExecutionRun: ...
    async def result(self, run: ExecutionRun) -> tuple[ArtifactRef, ...]: ...
    async def find_by_idempotency_key(self, tenant_id: str, key: str | None = None) -> ExecutionRun | None: ...


@dataclass(frozen=True)
class RealLabExecutionReadiness:
    """Abstract future gates; no provider endpoint or credential contract is implied."""

    enabled: bool = False
    safety_approved: bool = False
    locality_approved: bool = False

    @property
    def ready(self) -> bool:
        return self.enabled and self.safety_approved and self.locality_approved


@dataclass
class DeterministicLabExecutionAdapter:
    """Memory-only dry run whose outputs are stable for a submit idempotency key."""

    adapter_name: str = "deterministic-lab-dry-run"
    adapter_version: str = "1"
    visible_label: str = DRY_RUN_EVIDENCE_LABEL
    _runs: dict[str, ExecutionRun] = field(default_factory=dict, init=False, repr=False)

    @property
    def reconciliation_supported(self) -> bool:
        return True

    async def submit(self, request: ExecutionRequest) -> ExecutionRun:
        self._require_dry_run(request.mode, request.evidence_kind)
        existing = self._runs.get(_run_key(request.tenant_id, request.submit_intent_key))
        if existing is not None:
            if existing.input_hash != request.input_hash:
                raise LabExecutionAdapterError(ExternalFailureCode.INVALID_SCHEMA)
            return existing
        run = ExecutionRun(
            **request.model_dump(by_alias=True),
            status=ExternalRunStatus.SUCCEEDED,
            provider_execution_id=f"dry-run-lab-{request.input_hash[:32]}",
            submitted_at=request.requested_at,
            finished_at=request.requested_at,
        )
        self._runs[_run_key(request.tenant_id, request.submit_intent_key)] = run
        return run

    async def status(self, run: ExecutionRun) -> ExternalRunStatus:
        known = self._runs.get(_run_key(run.tenant_id, run.submit_intent_key))
        return known.status if known is not None else run.status

    async def abort(self, run: ExecutionRun) -> ExecutionRun:
        self._require_dry_run(run.mode, run.evidence_kind)
        aborted = run.model_copy(
            update={
                "status": ExternalRunStatus.ABORTED,
                "failure_code": ExternalFailureCode.ABORTED,
                "finished_at": run.finished_at or run.requested_at,
            }
        )
        self._runs[_run_key(run.tenant_id, run.submit_intent_key)] = aborted
        return aborted

    async def result(self, run: ExecutionRun) -> tuple[ArtifactRef, ...]:
        self._require_dry_run(run.mode, run.evidence_kind)
        if run.status is ExternalRunStatus.ABORTED:
            raise LabExecutionAdapterError(ExternalFailureCode.ABORTED)
        digest = hashlib.sha256(f"lab-dry-run:{run.submit_intent_key}".encode()).hexdigest()
        return (
            ArtifactRef(
                artifact_ref_id=f"dry-run-lab-raw-{digest[:24]}",
                tenant_id=run.tenant_id,
                canvas_id=run.canvas_id,
                execution_run_id=run.execution_run_id,
                content_hash=digest,
                logical_uri=f"mock://dry-run/lab-execution/{digest}",
                media_type="application/vnd.lab-agent.dry-run+json",
                classification="mock_or_dry_run",
                role=ArtifactRole.RAW,
                evidence_kind=EvidenceKind.MOCK_OR_DRY_RUN,
                retention_until=run.requested_at + timedelta(days=1),
                recorded_at=run.requested_at,
            ),
        )

    async def find_by_idempotency_key(self, tenant_id: str, key: str | None = None) -> ExecutionRun | None:
        return self._runs.get(_run_key("default" if key is None else tenant_id, tenant_id if key is None else key))

    @staticmethod
    def _require_dry_run(mode: RunMode, evidence_kind: EvidenceKind) -> None:
        if mode is not RunMode.DRY_RUN or evidence_kind is not EvidenceKind.MOCK_OR_DRY_RUN:
            raise LabExecutionNotReadyError()


@dataclass(frozen=True)
class DisabledRealLabExecutionAdapter:
    """Fail-closed sentinel; it never performs a provider, filesystem, or hardware call."""

    readiness: RealLabExecutionReadiness

    @property
    def reconciliation_supported(self) -> bool:
        return False

    async def submit(self, request: ExecutionRequest) -> ExecutionRun:
        del request
        self._raise_disabled()

    async def status(self, run: ExecutionRun) -> ExternalRunStatus:
        del run
        self._raise_disabled()

    async def abort(self, run: ExecutionRun) -> ExecutionRun:
        del run
        self._raise_disabled()

    async def result(self, run: ExecutionRun) -> tuple[ArtifactRef, ...]:
        del run
        self._raise_disabled()

    async def find_by_idempotency_key(self, tenant_id: str, key: str | None = None) -> ExecutionRun | None:
        del tenant_id, key
        self._raise_disabled()

    def _raise_disabled(self) -> NoReturn:
        if self.readiness.ready:
            raise LabExecutionImplementationNotInstalledError()
        raise LabExecutionNotReadyError()


__all__ = [
    "DRY_RUN_EVIDENCE_LABEL",
    "DeterministicLabExecutionAdapter",
    "DisabledRealLabExecutionAdapter",
    "LabExecutionAdapter",
    "LabExecutionAdapterError",
    "LabExecutionImplementationNotInstalledError",
    "LabExecutionNotReadyError",
    "RealLabExecutionReadiness",
]
