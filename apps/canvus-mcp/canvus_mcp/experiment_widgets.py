"""Widget classification predicates for the experiment-workflow graph.

Identifies workflow-role widgets on a canvas by their markers -- the
predicate/classification boundary consumed by the graph traversal and
loop-detection logic in :mod:`canvus_mcp.experiments`. Duck-types widgets
(SDK models or dicts), matching :mod:`canvus_mcp.ragcluster`'s style;
dependency-free and unit-testable (no MCP, no network).

Workflow markers (title prefixes, except the idea marker which is in-text):
- idea note        : Note text contains ``{idea: ...}`` (Note-only: the idea
  is the user's authored entry point, never a system-generated artifact)
- experiment setup : Note **or** Browser title starts ``[EXP:Setup``
- robot            : any widget whose title starts ``Robot_``
- experiment result: Note **or** Browser title starts ``[EXP:Result``
- experiment closed: any widget whose title starts ``[EXP:Closed]`` (no
  ``widget_type`` restriction, like ``robot``, for Browser support)
- needs input      : Note **or** Browser title starts ``[EXP:Needs Input]``
- validation       : Note **or** Browser title starts ``[EXP:Validation]``
- scientist review : Note title starts ``[EXP:Scientist Review]``
- lab-lead marker  : Note title starts ``[EXP:Lab Lead Approval]``

The human markers are non-authorizing canvas metadata. Their presence never
proves an actor, role, decision, or approval.

Generated artifacts (setup/result/closed/needs-input) may be rendered as
either a legacy Note or a Phase 3 Browser widget backed by the dynamic
artifact service -- these predicates accept both so canvases can carry a mix
during migration. Only ``{idea: ...}`` and human-authored input stay
Note-only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from canvus_mcp.ragcluster import _attr, _widget_title

_ROUND_RE = re.compile(r"v(\d+)")

# Widget types a generated workflow artifact (setup/result) may be rendered
# as. Idea/human-input Notes are intentionally excluded -- see ``_has_idea``.
_GENERATED_WIDGET_TYPES = frozenset({"Note", "Browser"})


@dataclass(frozen=True)
class ExpMarkers:
    """Marker prefixes that identify the experiment-workflow widgets."""

    ragcluster: str = "RAGCluster_"
    robot: str = "Robot_"
    setup: str = "[EXP:Setup"
    result: str = "[EXP:Result"
    idea: str = "{idea:"
    closed: str = "[EXP:Closed]"
    needs_input: str = "[EXP:Needs Input]"
    validation: str = "[EXP:Validation]"
    scientist_review: str = "[EXP:Scientist Review]"
    lab_lead_approval: str = "[EXP:Lab Lead Approval]"
    execution: str = "[EXP:Execution]"
    analysis: str = "[EXP:Analysis]"
    knowledge: str = "[EXP:Knowledge]"
    conflict: str = "[EXP:Conflict]"


ExecutionMode = Literal["manual", "auto"]
ParseError = Literal["unsupported_mode"]


@dataclass(frozen=True)
class IdeaMarkerParse:
    """Safe classification of an idea marker without retaining idea text."""

    is_idea: bool
    execution_mode: ExecutionMode | None = None
    parse_error: ParseError | None = None


def parse_idea_marker(text: object, marker: str = "{idea:") -> IdeaMarkerParse:
    """Classify legacy and auto markers; unsupported modes fail closed."""
    if not isinstance(text, str):
        return IdeaMarkerParse(False)
    if marker.endswith(":"):
        marker_stem = marker[:-1]
        modes = re.findall(rf"{re.escape(marker_stem)}\+([^:\s{{}}]+)(:)?", text)
        if any(mode != "auto" for mode, _delimiter in modes):
            return IdeaMarkerParse(False, parse_error="unsupported_mode")
        if any(mode == "auto" and delimiter == ":" for mode, delimiter in modes):
            return IdeaMarkerParse(True, execution_mode="auto")
    if marker in text:
        return IdeaMarkerParse(True, execution_mode="manual")
    return IdeaMarkerParse(False)


def _has_idea(w: Any, m: ExpMarkers) -> bool:
    return _attr(w, "widget_type") == "Note" and parse_idea_marker(_attr(w, "text"), m.idea).is_idea


def _is_generated_marker(w: Any, prefix: str) -> bool:
    """True if ``w`` is a generated artifact (Note or Browser) titled ``prefix``."""
    return _attr(w, "widget_type") in _GENERATED_WIDGET_TYPES and _widget_title(w).startswith(
        prefix
    )


def _is_setup(w: Any, m: ExpMarkers) -> bool:
    return _is_generated_marker(w, m.setup)


def _is_result(w: Any, m: ExpMarkers) -> bool:
    return _is_generated_marker(w, m.result)


def _is_robot(w: Any, m: ExpMarkers) -> bool:
    return _widget_title(w).startswith(m.robot)


def _is_closed(w: Any, m: ExpMarkers) -> bool:
    # No ``widget_type`` restriction (mirrors ``_is_robot``): preserves the
    # option of a future Browser-widget terminal marker.
    return _widget_title(w).startswith(m.closed)


def _is_needs_input(w: Any, m: ExpMarkers) -> bool:
    return _is_generated_marker(w, m.needs_input)


def _is_validation(w: Any, m: ExpMarkers) -> bool:
    return _is_generated_marker(w, m.validation)


def _is_scientist_review_marker(w: Any, m: ExpMarkers) -> bool:
    return _attr(w, "widget_type") == "Note" and _widget_title(w).startswith(m.scientist_review)


def _is_lab_lead_approval_marker(w: Any, m: ExpMarkers) -> bool:
    return _attr(w, "widget_type") == "Note" and _widget_title(w).startswith(m.lab_lead_approval)


def _is_execution(w: Any, m: ExpMarkers) -> bool:
    return _is_generated_marker(w, m.execution)


def _is_analysis(w: Any, m: ExpMarkers) -> bool:
    return _is_generated_marker(w, m.analysis)


def _is_knowledge(w: Any, m: ExpMarkers) -> bool:
    return _is_generated_marker(w, m.knowledge)


def _is_conflict(w: Any, m: ExpMarkers) -> bool:
    return _is_generated_marker(w, m.conflict)


def _brief(w: Any, execution_mode: ExecutionMode | None = None) -> dict[str, str]:
    brief = {
        "widget_id": _attr(w, "id"),
        "widget_type": _attr(w, "widget_type"),
        "title": _widget_title(w),
    }
    if execution_mode is not None:
        brief["execution_mode"] = execution_mode
    return brief


def _mode_error_brief(w: Any, parse_error: ParseError) -> dict[str, str]:
    """Describe an invalid marker without exposing untrusted Note text."""
    return {
        "widget_id": _attr(w, "id"),
        "widget_type": _attr(w, "widget_type"),
        "parse_error": parse_error,
    }


def _round_of(w: Any) -> int:
    match = _ROUND_RE.search(_widget_title(w))
    return int(match.group(1)) if match else 0


__all__ = ["ExpMarkers", "IdeaMarkerParse", "parse_idea_marker"]
