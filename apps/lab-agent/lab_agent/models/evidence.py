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
    """A term or choice that may materially affect setup generation.

    ``material_impact`` and ``alternatives`` make a non-acronym ambiguity
    actionable instead of allowing glossary-like flags to block a setup. The
    legacy resolution fields remain for backward compatibility and are trusted
    only when the approved dictionary independently agrees.
    """

    term: str = Field(..., description="Unresolved term or choice under review.")
    resolved: bool = Field(default=False, description="Legacy hint; verified against the approved dictionary.")
    expansion: str = Field(default="", description="Legacy approved expansion, empty when unresolved.")
    source: str = Field(default="", description="Legacy dictionary source/version for the expansion.")
    material_impact: str = Field(
        default="",
        description="Concrete safety, feasibility, resource, design, or interpretation impact.",
    )
    alternatives: list[str] = Field(
        default_factory=list,
        description="At least two concrete unresolved choices for a material ambiguity.",
    )


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
