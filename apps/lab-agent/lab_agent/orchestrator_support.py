"""Helpers for the experiment-loop orchestrator: validation, versioning, summary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError


def coerce(model_cls: type[BaseModel], parsed: dict[str, Any]) -> Any:
    """Validate model output, filling missing required fields defensively."""
    try:
        return model_cls.model_validate(parsed)
    except ValidationError:
        data = dict(parsed)
        for name, fld in model_cls.model_fields.items():
            if fld.is_required() and name not in data:
                data[name] = False if fld.annotation is bool else ""
        return model_cls.model_validate(data)


def version(round_index: int) -> str:
    """Format a round index as a zero-padded version tag, e.g. 1 -> 'v001'."""
    return f"v{round_index:03d}"


@dataclass
class LoopSummary:
    """Outcome of running one experiment loop to termination."""

    rounds: int = 0
    stopped_reason: str = ""
    setup_ids: list[str] = field(default_factory=list)
    result_ids: list[str] = field(default_factory=list)
    closed_id: str = ""


__all__ = ["LoopSummary", "coerce", "version"]
