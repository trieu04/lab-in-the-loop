"""Decision states for a Lab-in-the-Loop experiment (UC §12).

The lifecycle a proposed experiment moves through, from an ungrounded draft to
a closed round whose findings have been folded back into the knowledge base.
"""

from __future__ import annotations

from enum import StrEnum


class DecisionState(StrEnum):
    """Lifecycle state of an experiment node on the canvas."""

    DRAFT = "DRAFT"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    APPROVED_FOR_IN_SILICO = "APPROVED_FOR_IN_SILICO"
    IN_SILICO_RUNNING = "IN_SILICO_RUNNING"
    IN_SILICO_COMPLETE = "IN_SILICO_COMPLETE"
    NEEDS_SCIENTIST_REVIEW = "NEEDS_SCIENTIST_REVIEW"
    NEEDS_LAB_LEAD_APPROVAL = "NEEDS_LAB_LEAD_APPROVAL"
    APPROVED_FOR_WET_LAB = "APPROVED_FOR_WET_LAB"
    RUNNING = "RUNNING"
    ANALYSIS_COMPLETE = "ANALYSIS_COMPLETE"
    KNOWLEDGE_UPDATE_PENDING = "KNOWLEDGE_UPDATE_PENDING"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"


__all__ = ["DecisionState"]
