"""Typed durable records shared by ingestion storage and execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UnitStatus(StrEnum):
    PLANNED = "planned"
    RETRY = "retry"
    LEASED = "leased"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    POISON = "poison"


class ExtractionStatus(StrEnum):
    OK = "ok"
    UNSUPPORTED = "unsupported"
    MALFORMED = "malformed"
    ENCRYPTED = "encrypted"
    OVERSIZED = "oversized"
    FAILED = "failed"


@dataclass(frozen=True)
class UnitSpec:
    """A deterministic extraction slice; it contains no user supplied path/text."""

    kind: str
    ordinal: int
    start: int = 0
    end: int = 0


@dataclass(frozen=True)
class Job:
    id: int
    asset_sha256: str
    extractor_version: str
    mime_type: str
    status: JobStatus
    unit_count: int
    completed_units: int
    failed_units: int
    cancel_requested: bool


@dataclass(frozen=True)
class ClaimedUnit:
    job_id: int
    unit_id: int
    asset_sha256: str
    mime_type: str
    spec: UnitSpec
    lease_token: str
    generation: int
    attempt_number: int


@dataclass(frozen=True)
class StoredChunk:
    ordinal: int
    text: str
    metadata: Mapping[str, str | int | float | bool | None]


@dataclass(frozen=True)
class ExtractedChunk:
    ordinal: int
    text: str
    metadata: Mapping[str, str | int | float | bool | None]


@dataclass(frozen=True)
class ExtractionResult:
    status: ExtractionStatus
    chunks: tuple[ExtractedChunk, ...] = ()
    failure_code: str | None = None


def failed_extraction_result(status: ExtractionStatus) -> ExtractionResult:
    """Build a typed failed result whose code matches its extraction status."""
    return ExtractionResult(status=status, failure_code=status.value)


__all__ = [
    "ClaimedUnit",
    "ExtractedChunk",
    "ExtractionResult",
    "ExtractionStatus",
    "failed_extraction_result",
    "Job",
    "JobStatus",
    "StoredChunk",
    "UnitSpec",
    "UnitStatus",
]
