import hashlib
import re
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

_HASH = r"^[0-9a-f]{64}$"
_ID = Field(min_length=1, max_length=200)
_TENANT_ID = Field(default="default", min_length=1, max_length=128)
_SHORT = Field(min_length=1, max_length=100)
_SAFE_TEXT = Field(min_length=1, max_length=2000)
_LOGICAL_REF = re.compile(r"^(?:mock|opaque)://[a-z0-9][a-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._/-]{0,399}$")

def _is_opaque_logical_ref(value: str) -> bool:
    return bool(_LOGICAL_REF.fullmatch(value))

class RunMode(StrEnum):
    DRY_RUN, SANDBOX, REAL = "dry_run", "sandbox", "real"

class EvidenceKind(StrEnum):
    MOCK_OR_DRY_RUN, MEASURED = "mock_or_dry_run", "measured"

class ExternalRunStatus(StrEnum):
    PENDING, SUBMITTED, RUNNING = "pending", "submitted", "running"
    RECONCILING, AMBIGUOUS, SUCCEEDED = "reconciling", "ambiguous", "succeeded"
    FAILED, ABORT_REQUESTED, ABORTED, BLOCKED = "failed", "abort_requested", "aborted", "blocked"

class ExternalFailureCode(StrEnum):
    TIMEOUT, PROVIDER_FAILURE, INVALID_SCHEMA = "timeout", "provider_failure", "invalid_schema"
    NOT_READY, AUTHORIZATION_STALE = "not_ready", "authorization_stale"
    RECONCILIATION_UNSUPPORTED, RETENTION_LOCALITY_DENIED = "reconciliation_unsupported", "retention_locality_denied"
    ABORTED, IMPLEMENTATION_NOT_INSTALLED = "aborted", "implementation_not_installed"

class ArtifactRole(StrEnum):
    RAW, DERIVED, LOG = "raw", "derived", "log"

class InterpretationDisposition(StrEnum):
    APPEND, CONFLICT = "append", "conflict"

class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


def _validate_evidence_mode(
    mode: RunMode, evidence_kind: EvidenceKind, *, stage: str
) -> None:
    if evidence_kind is EvidenceKind.MEASURED and mode is not RunMode.REAL:
        raise ValueError(f"measured {stage} requires real mode")
    if mode is RunMode.DRY_RUN and evidence_kind is not EvidenceKind.MOCK_OR_DRY_RUN:
        raise ValueError(f"dry-run {stage} must be mock_or_dry_run")


class MeasuredEvidenceReceipt(_Contract):
    provider_job_id: str = _ID
    capture_receipt_id: str = _ID
    retained_location: str = Field(min_length=1, max_length=500)
    captured_at: AwareDatetime

class ArtifactRef(_Contract):
    tenant_id: str = _TENANT_ID
    artifact_ref_id: str = _ID
    canvas_id: str = _ID
    execution_run_id: str | None = Field(default=None, min_length=1, max_length=200)
    analysis_run_id: str | None = Field(default=None, min_length=1, max_length=200)
    content_hash: str = Field(pattern=_HASH)
    logical_uri: str = Field(min_length=1, max_length=500)
    media_type: str = Field(min_length=1, max_length=100)
    classification: str = Field(min_length=1, max_length=100)
    role: ArtifactRole
    evidence_kind: EvidenceKind
    retention_until: AwareDatetime
    recorded_at: AwareDatetime
    measured_receipt: MeasuredEvidenceReceipt | None = None
    @model_validator(mode="after")
    def validate_lineage_and_location(self) -> "ArtifactRef":
        if (self.execution_run_id is None) == (self.analysis_run_id is None):
            raise ValueError("artifact reference requires exactly one run owner")
        if not _is_opaque_logical_ref(self.logical_uri):
            raise ValueError("artifact logical_uri must be an opaque logical reference")
        if self.evidence_kind is EvidenceKind.MEASURED and (
            self.measured_receipt is None
            or not _is_opaque_logical_ref(self.measured_receipt.retained_location)
        ):
            raise ValueError("measured artifact references require a capture receipt")
        if self.evidence_kind is EvidenceKind.MOCK_OR_DRY_RUN and self.measured_receipt is not None:
            raise ValueError("mock artifact references cannot carry a measured receipt")
        return self

class ExecutionRequest(_Contract):
    tenant_id: str = _TENANT_ID
    request_id: str = _ID
    execution_run_id: str = _ID
    canvas_id: str = _ID
    setup_id: str = _ID
    round_index: int = Field(alias="round", ge=0)
    proposal_hash: str = Field(pattern=_HASH)
    validation_result_hash: str = Field(pattern=_HASH)
    adapter_name: str = _SHORT
    adapter_version: str = _SHORT
    mode: RunMode
    evidence_kind: EvidenceKind
    submit_intent_key: str = _ID
    input_hash: str = Field(pattern=_HASH)
    requested_at: AwareDatetime
    rerun_of_execution_id: str | None = Field(default=None, min_length=1, max_length=200)
    @model_validator(mode="after")
    def validate_evidence_mode(self) -> "ExecutionRequest":
        _validate_evidence_mode(self.mode, self.evidence_kind, stage="execution")
        return self

class ExecutionRun(ExecutionRequest):
    status: ExternalRunStatus = ExternalRunStatus.PENDING
    abort_intent_key: str | None = Field(default=None, min_length=1, max_length=200)
    provider_execution_id: str | None = Field(default=None, min_length=1, max_length=200)
    submitted_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    failure_code: ExternalFailureCode | None = None

class AnalysisRequest(_Contract):
    tenant_id: str = _TENANT_ID
    request_id: str = _ID
    analysis_run_id: str = _ID
    canvas_id: str = _ID
    execution_run_id: str = _ID
    source_artifact_ref_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    adapter_name: str = _SHORT
    adapter_version: str = _SHORT
    mode: RunMode
    evidence_kind: EvidenceKind
    submit_intent_key: str = _ID
    input_hash: str = Field(pattern=_HASH)
    requested_at: AwareDatetime
    rerun_of_analysis_id: str | None = Field(default=None, min_length=1, max_length=200)
    @model_validator(mode="after")
    def validate_evidence_mode(self) -> "AnalysisRequest":
        _validate_evidence_mode(self.mode, self.evidence_kind, stage="analysis")
        return self

class AnalysisRun(AnalysisRequest):
    status: ExternalRunStatus = ExternalRunStatus.PENDING
    abort_intent_key: str | None = Field(default=None, min_length=1, max_length=200)
    provider_job_id: str | None = Field(default=None, min_length=1, max_length=200)
    submitted_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    failure_code: ExternalFailureCode | None = None

class InterpretationResult(_Contract):
    tenant_id: str = _TENANT_ID
    interpretation_id: str = _ID
    canvas_id: str = _ID
    execution_run_id: str = _ID
    analysis_run_id: str = _ID
    source_artifact_ref_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    original_hypothesis: str = _SAFE_TEXT
    original_hypothesis_hash: str = Field(pattern=_HASH)
    evidence_kind: EvidenceKind
    caveats: tuple[str, ...] = Field(default_factory=tuple, max_length=50)
    interpretation: str = Field(min_length=1, max_length=8000)
    disposition: InterpretationDisposition
    prior_knowledge_version_id: str | None = Field(default=None, min_length=1, max_length=200)
    @model_validator(mode="after")
    def validate_hypothesis_and_disposition(self) -> "InterpretationResult":
        digest = hashlib.sha256(self.original_hypothesis.encode("utf-8")).hexdigest()
        if self.original_hypothesis_hash != digest:
            raise ValueError("original_hypothesis_hash does not match original_hypothesis")
        if (self.disposition is InterpretationDisposition.CONFLICT) != (self.prior_knowledge_version_id is not None):
            raise ValueError("conflict disposition requires exactly one prior knowledge version")
        return self

class KnowledgeVersion(_Contract):
    tenant_id: str = _TENANT_ID
    knowledge_version_id: str = _ID
    canvas_id: str = _ID
    execution_run_id: str = _ID
    analysis_run_id: str = _ID
    proposal_hash: str = Field(pattern=_HASH)
    hypothesis: str = _SAFE_TEXT
    hypothesis_hash: str = Field(pattern=_HASH)
    evidence_kind: EvidenceKind
    provenance_artifact_ref_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    payload: str = Field(min_length=1, max_length=12000)
    content_hash: str = Field(pattern=_HASH)
    idempotency_key: str = _ID
    created_at: AwareDatetime

class ConflictRecord(_Contract):
    tenant_id: str = _TENANT_ID
    conflict_id: str = _ID
    canvas_id: str = _ID
    prior_knowledge_version_id: str = _ID
    proposed_knowledge_version_id: str = _ID
    old_hypothesis_hash: str = Field(pattern=_HASH)
    new_hypothesis_hash: str = Field(pattern=_HASH)
    evidence_artifact_ref_ids: tuple[str, ...] = Field(min_length=1, max_length=100)
    reason_code: str = Field(min_length=1, max_length=100)
    reason_text: str = _SAFE_TEXT
    idempotency_key: str = _ID
    created_at: AwareDatetime

__all__ = ["AnalysisRequest", "AnalysisRun", "ArtifactRef", "ArtifactRole", "ConflictRecord", "EvidenceKind", "ExecutionRequest", "ExecutionRun", "ExternalFailureCode", "ExternalRunStatus", "InterpretationDisposition", "InterpretationResult", "KnowledgeVersion", "MeasuredEvidenceReceipt", "RunMode"]
