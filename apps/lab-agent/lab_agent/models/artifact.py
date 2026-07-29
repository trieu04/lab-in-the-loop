"""Canonical generated-artifact document schema (Phase 3).

Canvus Browser widgets store only url/title/view properties -- they hold no
metadata of their own (phase-03 plan, "Key Insights"). These types are the
typed, versioned record :class:`~lab_agent.artifact_store.ArtifactStore`
persists and the safe view-model layer (``artifact_render.py``, a later
phase) renders into tabs. The Browser widget is a view over this data, never
the source of truth.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from lab_agent.models.states import DecisionState


class ArtifactType(StrEnum):
    """Which generated-artifact kind a document represents (UC step markers)."""

    SETUP = "setup"
    RESULT = "result"
    CLOSED = "closed"
    NEEDS_INPUT = "needs_input"
    IN_SILICO = "in_silico"
    APPROVAL_STATUS = "approval_status"
    EXECUTION = "execution"
    ANALYSIS = "analysis"
    KNOWLEDGE = "knowledge"
    CONFLICT = "conflict"


class TabKey(StrEnum):
    """Stable tab identifiers the safe view-model layer renders.

    Baseline tabs (see :data:`BASELINE_TABS`) apply to every artifact type;
    the remainder are added by specific artifact types per the phase-03 plan.
    """

    OVERVIEW = "overview"
    DETAILS = "details"
    EVIDENCE = "evidence"
    METADATA = "metadata"
    AUDIT = "audit"
    VALIDATION = "validation"
    EXECUTION = "execution"
    ANALYSIS = "analysis"
    VERSIONS = "versions"


#: Every artifact type renders at least these tabs, in this order.
BASELINE_TABS: tuple[TabKey, ...] = (
    TabKey.OVERVIEW,
    TabKey.DETAILS,
    TabKey.EVIDENCE,
    TabKey.METADATA,
    TabKey.AUDIT,
)


class TabDefinition(BaseModel):
    """One named tab in an artifact's rendered view."""

    key: TabKey
    label: str = Field(..., description="Human-readable tab title shown in the Browser widget.")
    order: int = Field(default=0, description="Sort order among an artifact's tabs.")


class ArtifactProvenance(BaseModel):
    """Lineage of one artifact version -- which model/trigger produced it."""

    provider: str = Field(
        ..., description="Model provider/adapter name, e.g. 'claude' or 'openai'."
    )
    model_name: str = Field(
        default="", description="Model identifier used to produce this version."
    )
    trigger_id: str = Field(
        default="", description="Workflow trigger id that produced this version."
    )
    source_widget_id: str = Field(
        default="", description="Upstream widget id this artifact was derived from, if any."
    )


class ArtifactMetadata(BaseModel):
    """Non-payload bookkeeping surfaced in the Metadata tab; never secrets."""

    tags: list[str] = Field(default_factory=list)
    extra: dict[str, str] = Field(
        default_factory=dict,
        description="Free-form string metadata; never raw documents, credentials, or full evidence bodies.",
    )


class ArtifactVersion(BaseModel):
    """One immutable, append-only version of an artifact's payload."""

    tenant_id: str = "default"
    canvas_id: str = "default"
    opaque_id: str
    version: int = Field(..., ge=1)
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Canonical structured payload."
    )
    metadata: ArtifactMetadata = Field(default_factory=ArtifactMetadata)
    provenance: ArtifactProvenance
    content_hash: str
    created_at: float


class ArtifactDocument(BaseModel):
    """Canonical current-version view of one generated artifact.

    Composed by :class:`~lab_agent.artifact_store.ArtifactStore` from the
    ``artifacts`` pointer row and its latest ``artifact_versions`` row.
    ``state`` mirrors the canvas :class:`DecisionState` this version was
    written under, for traceability only -- the canvas remains workflow
    truth (docs/system-architecture.md, harness boundary); this is never a
    second competing state machine. Never carries a token or token hash --
    see ``ArtifactStore.issue_token``/``verify_token`` for capability access.
    """

    tenant_id: str = "default"
    opaque_id: str
    canvas_id: str
    artifact_type: ArtifactType
    state: DecisionState
    round: int = Field(default=0, ge=0)
    current_version: int = Field(..., ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: ArtifactMetadata = Field(default_factory=ArtifactMetadata)
    provenance: ArtifactProvenance
    content_hash: str
    widget_id: str | None = Field(
        default=None, description="Mapped Browser widget id, if created yet."
    )
    created_at: float
    updated_at: float


__all__ = [
    "BASELINE_TABS",
    "ArtifactDocument",
    "ArtifactMetadata",
    "ArtifactProvenance",
    "ArtifactType",
    "ArtifactVersion",
    "TabDefinition",
    "TabKey",
]
