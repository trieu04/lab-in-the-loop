"""Per-run evidence ledger: deterministic source ids, bounded excerpts, and a
minimal durable audit summary.

A setup's citations must resolve against *this run's* ledger -- an id that is
not present (or not recorded because the ledger is full) is a fabrication and
blocks the write (see :mod:`lab_agent.grounding`). Only successful,
allowlisted reads are ever recorded (see :mod:`lab_agent.tool_bridge`); the
ledger never sees blocked/error results.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from lab_agent.state_store import MAX_PAYLOAD_BYTES

#: Cap on what a single evidence record keeps in memory, so one huge download
#: (e.g. download_pdf) cannot grow the per-run ledger unbounded.
EXCERPT_MAX_CHARS = 500

#: Cap on the serialized audit summary (ids/tool/hash only -- never raw
#: content, credentials, or capability URLs). Mirrors the durable store's own
#: whole-payload cap (:data:`lab_agent.state_store.MAX_PAYLOAD_BYTES`) so the
#: two never drift apart; the *enclosing* payload (decision + reason + these
#: rows) is trimmed further by :func:`lab_agent.grounding.record_grounding_audit`.
AUDIT_BYTE_CAP = MAX_PAYLOAD_BYTES


def _canonical_args(arguments: dict[str, Any]) -> str:
    """Stable, order-independent JSON encoding of tool call arguments."""
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _content_hash(content: str) -> str:
    """Full SHA-256 hex digest of ``content`` (UTF-8)."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_source_id(tool: str, arguments: dict[str, Any], content: str) -> str:
    """Deterministic id: bare tool name + canonical arguments + full content hash.

    Two calls with the same tool/arguments/result always resolve to the same
    id, so a repeated read within a run (or across a restart) is recognized
    as the same evidence, not a new fabricated source.
    """
    raw = f"{tool}|{_canonical_args(arguments)}|{_content_hash(content)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class EvidenceRecord:
    """One captured, successful read -- kept in memory only, never persisted verbatim."""

    source_id: str
    tool: str
    content_hash: str
    excerpt: str


@dataclass
class EvidenceLedger:
    """Bounded, per-run, in-memory record of successful allowlisted reads.

    Not thread-safe and not durable by design: it lives for one grounding
    call (one ``generate_setup``/round-advance), is discarded after the gate
    decision is made, and only its minimal :meth:`audit_summary` is ever
    written to the durable audit log.
    """

    max_records: int = 64
    _records: dict[str, EvidenceRecord] = field(default_factory=dict)

    def add(self, tool: str, arguments: dict[str, Any], content: str) -> str:
        """Record a successful read; return its deterministic source id.

        Idempotent for repeated identical calls. Once :attr:`max_records` is
        reached, further distinct reads are still assigned a (deterministic,
        reproducible) id but are not stored -- so they correctly fail
        :meth:`known`/:meth:`validate_citations` rather than being silently
        trusted.
        """
        source_id = compute_source_id(tool, arguments, content)
        if source_id not in self._records and len(self._records) < self.max_records:
            self._records[source_id] = EvidenceRecord(
                source_id=source_id,
                tool=tool,
                content_hash=_content_hash(content),
                excerpt=content[:EXCERPT_MAX_CHARS],
            )
        return source_id

    def known(self, source_id: str) -> bool:
        return source_id in self._records

    def record_for(self, source_id: str) -> EvidenceRecord | None:
        """Return the captured record for ``source_id``, or ``None`` if unknown/dropped."""
        return self._records.get(source_id)

    def validate_citations(self, source_ids: list[str]) -> tuple[bool, list[str], list[str]]:
        """Check a setup's cited ids against this ledger.

        Returns ``(valid, known_ids, unknown_ids)``; ``valid`` requires at
        least one citation and every one of them present in the ledger --
        zero, fabricated, or mixed valid/invalid citations are all invalid.
        """
        known_ids = [sid for sid in source_ids if self.known(sid)]
        unknown_ids = [sid for sid in source_ids if not self.known(sid)]
        valid = bool(source_ids) and not unknown_ids
        return valid, known_ids, unknown_ids

    def excerpts(self) -> tuple[str, ...]:
        """Bounded excerpt text for every captured read, in-memory only.

        Never written to durable storage (see :meth:`audit_summary`); used
        only for the deterministic acronym-boundary scan in
        :mod:`lab_agent.grounding`, so an unresolved term that appears in
        retrieved evidence -- but that the model never quoted into the
        emitted setup or self-flagged -- still gets caught before any
        setup write.
        """
        return tuple(record.excerpt for record in self._records.values())

    def audit_summary(self) -> list[dict[str, str]]:
        """Minimal durable rows: ids/tool/hash only, capped at :data:`AUDIT_BYTE_CAP` bytes.

        Never includes the excerpt, arguments, or anything else that could
        leak a credential, capability URL, or full retrieved document.
        """
        rows: list[dict[str, str]] = []
        total = 2  # the enclosing "[" "]" of the eventual list encoding
        for record in self._records.values():
            row = {"source_id": record.source_id, "tool": record.tool, "content_hash": record.content_hash}
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
]
