"""Per-run evidence ledger: deterministic ids, bounded excerpts, and safe audit."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from lab_agent.models.governance import DataClassification
from lab_agent.result_safety import is_unsafe_data, is_unsafe_text
from lab_agent.state_store import MAX_PAYLOAD_BYTES

EXCERPT_MAX_CHARS = 500
AUDIT_BYTE_CAP = MAX_PAYLOAD_BYTES
_SAFE_PROVENANCE_FIELDS = frozenset(
    {"canvas_id", "job_id", "asset_sha256", "extractor_version", "modality", "ordinal"}
)


def _canonical_args(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_source_id(tool: str, arguments: dict[str, Any], content: str) -> str:
    """Return the deterministic identifier for one bare tool read."""
    raw = f"{tool}|{_canonical_args(arguments)}|{_content_hash(content)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def safe_provenance(provenance: dict[str, str | int] | None) -> dict[str, str | int]:
    """Return only bounded, non-sensitive allowlisted provenance fields."""
    if provenance is None:
        return {}
    safe: dict[str, str | int] = {}
    for key, value in provenance.items():
        if key not in _SAFE_PROVENANCE_FIELDS or isinstance(value, bool):
            continue
        if isinstance(value, int):
            safe[key] = value
        elif isinstance(value, str) and len(value) <= 256 and not is_unsafe_text(value):
            safe[key] = value
    return safe


@dataclass(frozen=True)
class EvidenceRecord:
    """One captured successful read, retained only for its current run."""

    source_id: str
    tool: str
    content_hash: str
    excerpt: str
    data_classification: DataClassification
    provenance: dict[str, str | int] = field(default_factory=dict)


@dataclass
class EvidenceLedger:
    """Bounded, in-memory evidence usable by the setup citation gate."""

    max_records: int = 64
    max_bytes: int = 128 * 1024
    _records: dict[str, EvidenceRecord] = field(default_factory=dict)
    _content_bytes: int = 0

    def add(
        self,
        tool: str,
        arguments: dict[str, Any],
        content: str,
        data_classification: DataClassification = DataClassification.UNKNOWN,
        provenance: dict[str, str | int] | None = None,
    ) -> str:
        """Capture one already-sanitized read and return its deterministic id."""
        if is_unsafe_data(arguments) or is_unsafe_text(content):
            return ""
        source_id = compute_source_id(tool, arguments, content)
        if source_id in self._records:
            return source_id
        content_bytes = len(content.encode("utf-8"))
        if len(self._records) >= self.max_records or self._content_bytes + content_bytes > self.max_bytes:
            return ""
        self._records[source_id] = EvidenceRecord(
            source_id=source_id,
            tool=tool,
            content_hash=_content_hash(content),
            excerpt=content[:EXCERPT_MAX_CHARS],
            data_classification=data_classification,
            provenance=safe_provenance(provenance),
        )
        self._content_bytes += content_bytes
        return source_id

    def known(self, source_id: str) -> bool:
        return source_id in self._records

    def record_for(self, source_id: str) -> EvidenceRecord | None:
        return self._records.get(source_id)

    def validate_citations(self, source_ids: list[str]) -> tuple[bool, list[str], list[str]]:
        known_ids = [source_id for source_id in source_ids if self.known(source_id)]
        unknown_ids = [source_id for source_id in source_ids if not self.known(source_id)]
        return bool(source_ids) and not unknown_ids, known_ids, unknown_ids

    def excerpts(self) -> tuple[str, ...]:
        return tuple(record.excerpt for record in self._records.values())

    def audit_summary(self) -> list[dict[str, object]]:
        """Return capped durable rows without excerpts, arguments, or raw content."""
        rows: list[dict[str, object]] = []
        total = 2
        for record in self._records.values():
            row: dict[str, object] = {
                "source_id": record.source_id,
                "tool": record.tool,
                "content_hash": record.content_hash,
                "data_classification": record.data_classification.value,
            }
            if record.provenance:
                row["provenance"] = record.provenance
            row_bytes = len(json.dumps(row, separators=(",", ":")).encode("utf-8")) + 1
            if total + row_bytes > AUDIT_BYTE_CAP:
                break
            rows.append(row)
            total += row_bytes
        return rows


__all__ = [
    "AUDIT_BYTE_CAP",
    "EXCERPT_MAX_CHARS",
    "EvidenceLedger",
    "EvidenceRecord",
    "compute_source_id",
    "safe_provenance",
]
