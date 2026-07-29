"""Provider-neutral append-only knowledge boundary with a deterministic dry run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NoReturn, Protocol, runtime_checkable

from lab_agent.models.execution import (
    ConflictRecord,
    EvidenceKind,
    ExternalFailureCode,
    ExternalRunStatus,
    KnowledgeVersion,
)

DRY_RUN_EVIDENCE_LABEL = "DRY RUN / MOCK — NOT MEASURED"
class KnowledgeAdapterError(RuntimeError):
    """A safe, typed failure from the knowledge adapter boundary."""

    def __init__(self, code: ExternalFailureCode) -> None:
        self.code = code
        value = code.value if isinstance(code, ExternalFailureCode) else code
        super().__init__(value)

class KnowledgeNotReadyError(KnowledgeAdapterError):
    """The real knowledge adapter has not passed declared readiness gates."""

    def __init__(self) -> None:
        super().__init__(ExternalFailureCode.NOT_READY)


class KnowledgeImplementationNotInstalledError(KnowledgeAdapterError):
    """No real knowledge-store implementation exists in the Phase 8 dry-run scope."""

    def __init__(self) -> None:
        super().__init__(ExternalFailureCode.IMPLEMENTATION_NOT_INSTALLED)


@runtime_checkable
class KnowledgeAdapter(Protocol):
    """Async append-only knowledge port with idempotency-key reconciliation."""

    @property
    def reconciliation_supported(self) -> bool:
        """Whether a prior append can be authoritatively found by key."""
        ...

    async def append(self, version: KnowledgeVersion) -> KnowledgeVersion: ...
    async def status(self, idempotency_key: str, *, tenant_id: str = "default") -> ExternalRunStatus: ...
    async def cancel(self, idempotency_key: str, *, tenant_id: str = "default") -> ExternalRunStatus: ...
    async def result(self, idempotency_key: str, *, tenant_id: str = "default") -> KnowledgeVersion | None: ...
    async def find_by_idempotency_key(self, key: str, *, tenant_id: str = "default") -> KnowledgeVersion | None: ...
    async def record_conflict(self, conflict: ConflictRecord) -> ConflictRecord: ...
    async def find_conflict_by_idempotency_key(self, key: str, *, tenant_id: str = "default") -> ConflictRecord | None: ...


@dataclass(frozen=True)
class RealKnowledgeReadiness:
    """Abstract future gates; no store API, endpoint, or credential shape is assumed."""

    enabled: bool = False
    locality_approved: bool = False
    retention_approved: bool = False

    @property
    def ready(self) -> bool:
        return self.enabled and self.locality_approved and self.retention_approved


@dataclass
class DeterministicKnowledgeAdapter:
    """Memory-only append-only store for explicitly labelled dry-run knowledge."""

    adapter_name: str = "deterministic-knowledge-dry-run"
    adapter_version: str = "1"
    visible_label: str = DRY_RUN_EVIDENCE_LABEL
    _versions: dict[tuple[str, str], KnowledgeVersion] = field(default_factory=dict, init=False, repr=False)
    _conflicts: dict[tuple[str, str], ConflictRecord] = field(default_factory=dict, init=False, repr=False)

    @property
    def reconciliation_supported(self) -> bool:
        return True

    async def append(self, version: KnowledgeVersion) -> KnowledgeVersion:
        self._require_mock_evidence(version.evidence_kind)
        existing = self._versions.get((version.tenant_id, version.idempotency_key))
        if existing is not None:
            if existing != version:
                raise KnowledgeAdapterError(ExternalFailureCode.INVALID_SCHEMA)
            return existing
        self._versions[(version.tenant_id, version.idempotency_key)] = version
        return version

    async def status(self, idempotency_key: str, *, tenant_id: str = "default") -> ExternalRunStatus:
        return (
            ExternalRunStatus.SUCCEEDED
            if (tenant_id, idempotency_key) in self._versions
            else ExternalRunStatus.PENDING
        )

    async def cancel(self, idempotency_key: str, *, tenant_id: str = "default") -> ExternalRunStatus:
        """Never remove persisted history; an already-appended version remains preserved."""
        return (
            ExternalRunStatus.BLOCKED
            if (tenant_id, idempotency_key) in self._versions
            else ExternalRunStatus.ABORTED
        )

    async def result(self, idempotency_key: str, *, tenant_id: str = "default") -> KnowledgeVersion | None:
        return self._versions.get((tenant_id, idempotency_key))

    async def find_by_idempotency_key(self, key: str, *, tenant_id: str = "default") -> KnowledgeVersion | None:
        return self._versions.get((tenant_id, key))

    async def record_conflict(self, conflict: ConflictRecord) -> ConflictRecord:
        existing = self._conflicts.get((conflict.tenant_id, conflict.idempotency_key))
        if existing is not None:
            if existing != conflict:
                raise KnowledgeAdapterError(ExternalFailureCode.INVALID_SCHEMA)
            return existing
        versions = {
            version.knowledge_version_id: version
            for version in self._versions.values()
            if version.tenant_id == conflict.tenant_id and version.canvas_id == conflict.canvas_id
        }
        prior = versions.get(conflict.prior_knowledge_version_id)
        proposed = versions.get(conflict.proposed_knowledge_version_id)
        if (
            prior is None
            or proposed is None
            or prior.hypothesis_hash != conflict.old_hypothesis_hash
            or proposed.hypothesis_hash != conflict.new_hypothesis_hash
        ):
            raise KnowledgeAdapterError(ExternalFailureCode.INVALID_SCHEMA)
        self._conflicts[(conflict.tenant_id, conflict.idempotency_key)] = conflict
        return conflict

    async def find_conflict_by_idempotency_key(self, key: str, *, tenant_id: str = "default") -> ConflictRecord | None:
        return self._conflicts.get((tenant_id, key))

    @staticmethod
    def _require_mock_evidence(evidence_kind: EvidenceKind) -> None:
        if evidence_kind is not EvidenceKind.MOCK_OR_DRY_RUN:
            raise KnowledgeNotReadyError()


@dataclass(frozen=True)
class DisabledRealKnowledgeAdapter:
    """Fail-closed sentinel; it cannot write, query, cancel, or reconcile real knowledge."""

    readiness: RealKnowledgeReadiness

    @property
    def reconciliation_supported(self) -> bool:
        return False

    async def append(self, version: KnowledgeVersion) -> KnowledgeVersion:
        del version
        self._raise_disabled()

    async def status(self, idempotency_key: str, *, tenant_id: str = "default") -> ExternalRunStatus:
        del idempotency_key, tenant_id
        self._raise_disabled()

    async def cancel(self, idempotency_key: str, *, tenant_id: str = "default") -> ExternalRunStatus:
        del idempotency_key, tenant_id
        self._raise_disabled()

    async def result(self, idempotency_key: str, *, tenant_id: str = "default") -> KnowledgeVersion | None:
        del idempotency_key, tenant_id
        self._raise_disabled()

    async def find_by_idempotency_key(self, key: str, *, tenant_id: str = "default") -> KnowledgeVersion | None:
        del key, tenant_id
        self._raise_disabled()

    async def record_conflict(self, conflict: ConflictRecord) -> ConflictRecord:
        del conflict
        self._raise_disabled()

    async def find_conflict_by_idempotency_key(self, key: str, *, tenant_id: str = "default") -> ConflictRecord | None:
        del key, tenant_id
        self._raise_disabled()

    def _raise_disabled(self) -> NoReturn:
        if self.readiness.ready:
            raise KnowledgeImplementationNotInstalledError()
        raise KnowledgeNotReadyError()


__all__ = [
    "DRY_RUN_EVIDENCE_LABEL",
    "DeterministicKnowledgeAdapter",
    "DisabledRealKnowledgeAdapter",
    "KnowledgeAdapter",
    "KnowledgeAdapterError",
    "KnowledgeImplementationNotInstalledError",
    "KnowledgeNotReadyError",
    "RealKnowledgeReadiness",
]
