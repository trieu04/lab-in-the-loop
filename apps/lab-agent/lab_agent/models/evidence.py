"""Typed evidence, citation, and ambiguity contracts for grounded setups.

These are the additive Phase 4 types the setup generator emits and the
grounding gate validates. They are deliberately small value objects: the model
asserts an :class:`EvidenceStatus`, cites :class:`EvidenceCitation` source ids
that must resolve in the per-run ledger, and surfaces unresolved
:class:`AcronymFlag` terms rather than guessing an expansion.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class EvidenceStatus(StrEnum):
    """The model's explicit judgement of internal-evidence sufficiency.

    Only :attr:`SUFFICIENT` may gate an executable setup; the absence of a
    status (``None`` on a legacy/blank setup) is never treated as sufficient.
    """

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"


class EvidenceCitation(BaseModel):
    """A reference to one retrieved evidence source in the per-run ledger.

    ``source_id`` must equal a ledger-derived id (bare tool + canonical
    arguments + full content hash); a citation that resolves nowhere is a
    fabrication and blocks the write.
    """

    source_id: str = Field(..., description="Ledger source id this claim is grounded on.")
    note: str = Field(default="", description="Optional human-readable claim this source supports.")


class AcronymFlag(BaseModel):
    """An acronym-like term detected in the idea/evidence and its resolution.

    ``resolved`` is only ``True`` when an approved, versioned dictionary supplied
    the ``expansion``; unknown or colliding terms stay unresolved with an empty
    expansion and are surfaced, never guessed.
    """

    term: str = Field(..., description="The detected acronym-like term (verbatim).")
    resolved: bool = Field(default=False, description="True only if an approved dictionary resolved it.")
    expansion: str = Field(default="", description="Approved expansion, empty when unresolved.")
    source: str = Field(default="", description="Dictionary source/version that resolved the term.")


class CitationCheck(BaseModel):
    """Outcome of validating emitted citations against the current ledger."""

    valid: bool = Field(..., description="True iff there is >=1 citation and all resolve in the ledger.")
    known_ids: list[str] = Field(default_factory=list, description="Cited ids present in the ledger.")
    unknown_ids: list[str] = Field(default_factory=list, description="Cited ids absent from the ledger.")


class GroundingDecision(StrEnum):
    """The gate's verdict for a generated setup (see grounding_gate)."""

    EXECUTABLE = "executable"
    NEEDS_INPUT = "needs_input"
    INVALID_CITATION = "invalid_citation"
    SCHEMA_FAILED = "schema_failed"


__all__ = [
    "AcronymFlag",
    "CitationCheck",
    "EvidenceCitation",
    "EvidenceStatus",
    "GroundingDecision",
]
