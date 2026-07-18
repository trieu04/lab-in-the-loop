"""Read-only canvas probing for the legacy-Note -> Browser artifact migration.

The inventory/inspection half of :mod:`lab_agent.artifact_migration`: it derives
which legacy generated Notes exist and how each is wired, using only MCP *read*
tools (``scan_experiment_workflow``, ``check_widget_connections``). Split from
the orchestration/write half to keep both files inside the 200-line budget; this
module performs no ArtifactStore/canvas mutations and imports nothing that does.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactType

#: ``scan_experiment_workflow`` bucket -> the generated artifact type it holds.
_BUCKET_TYPE: dict[str, ArtifactType] = {
    "setups": ArtifactType.SETUP,
    "results": ArtifactType.RESULT,
    "closeds": ArtifactType.CLOSED,
}
_ROUND_RE = re.compile(r"v(\d+)")


class MigrationShapeError(RuntimeError):
    """A connection response could not be read safely -- fail visible for that
    item rather than guessing (and drawing) an edge."""


@dataclass(frozen=True)
class LegacyNote:
    """One legacy generated Note eligible for a Browser mirror."""

    widget_id: str
    artifact_type: ArtifactType
    title: str
    round: int


def _round_of(title: str) -> int:
    """Parse the ``vNNN`` round marker, or 0 when not safely derivable."""
    match = _ROUND_RE.search(title)
    return int(match.group(1)) if match else 0


async def scan_legacy_notes(mcp: MCPClient, canvas_id: str) -> list[LegacyNote]:
    """Inventory legacy **Note** Setup/Result/Closed widgets on ``canvas_id``.

    Browser widgets (already migrated) are excluded by the ``widget_type ==
    "Note"`` filter; idea/human Notes are excluded because the detector routes
    ``{idea: ...}`` and unmarked Notes out of the setup/result/closed buckets.
    """
    raw = await mcp.call_tool("scan_experiment_workflow", {"canvas_id": canvas_id})
    snap = json.loads(raw)
    notes: list[LegacyNote] = []
    if not isinstance(snap, dict):
        return notes
    for bucket, artifact_type in _BUCKET_TYPE.items():
        for entry in snap.get(bucket, []):
            if not isinstance(entry, dict) or entry.get("widget_type") != "Note":
                continue
            widget_id = entry.get("widget_id")
            if not isinstance(widget_id, str) or not widget_id:
                continue
            title = str(entry.get("title", "") or "")
            notes.append(LegacyNote(widget_id, artifact_type, title, _round_of(title)))
    return notes


def _endpoint_ids(entries: Any, note_id: str, label: str) -> list[str]:
    """Extract deduplicated neighbor ids, or fail visible on an unknown shape.

    Browser neighbors are legitimate on mixed legacy/new canvases, so widget type
    never suppresses an otherwise valid edge.
    """
    if not isinstance(entries, list):
        raise MigrationShapeError(f"{note_id}: {label} connections are not a list")
    ids: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise MigrationShapeError(f"{note_id}: malformed {label} entry")
        other = entry.get("other")
        if not isinstance(other, dict):
            raise MigrationShapeError(f"{note_id}: {label} entry missing 'other'")
        other_id = other.get("widget_id")
        if not isinstance(other_id, str) or not other_id:
            raise MigrationShapeError(f"{note_id}: {label} entry missing 'widget_id'")
        if other_id != note_id:  # drop a legacy self-loop
            ids.append(other_id)
    return list(dict.fromkeys(ids))  # dedup, preserve order


async def inspect_connections(
    mcp: MCPClient, canvas_id: str, note_id: str
) -> tuple[list[str], list[str]]:
    """Return ``(inbound source ids, outbound destination ids)`` for ``note_id``.

    Raises :class:`MigrationShapeError` on any response that cannot be read
    safely, so a malformed shape fails visible instead of a guessed edge.
    """
    raw = await mcp.call_tool(
        "check_widget_connections", {"canvas_id": canvas_id, "widget_id": note_id}
    )
    try:
        report = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MigrationShapeError(f"{note_id}: malformed connection JSON") from exc
    if not isinstance(report, dict):
        raise MigrationShapeError(f"{note_id}: non-object connection response")
    if "error" in report:
        raise MigrationShapeError(f"{note_id}: connection query failed")
    if not report.get("found", False):
        return [], []
    inbound = _endpoint_ids(report.get("incoming"), note_id, "incoming")
    outbound = _endpoint_ids(report.get("outgoing"), note_id, "outgoing")
    return inbound, outbound


__all__ = [
    "LegacyNote",
    "MigrationShapeError",
    "inspect_connections",
    "scan_legacy_notes",
]
