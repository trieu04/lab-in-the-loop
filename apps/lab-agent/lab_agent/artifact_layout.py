"""Source-relative placement for generated artifact Browser widgets."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

from lab_agent.mcp_client import MCPClient
from lab_agent.models.artifact import ArtifactType

_GAP = 40.0


@dataclass(frozen=True)
class WidgetGeometry:
    """Canvas geometry parsed from a Canvus widget payload."""

    x: float
    y: float
    width: float
    height: float


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _positive_number(value: object) -> float | None:
    number = _number(value)
    return number if number is not None and number > 0 else None


def _point(payload: dict[str, Any]) -> tuple[float, float] | None:
    location = payload.get("location")
    source = location if isinstance(location, dict) else payload
    x = _number(source.get("x"))
    y = _number(source.get("y"))
    return None if x is None or y is None else (x, y)


def _size(payload: dict[str, Any]) -> tuple[float, float] | None:
    size = payload.get("size")
    source = size if isinstance(size, dict) else payload
    width = _positive_number(source.get("width"))
    height = _positive_number(source.get("height"))
    return None if width is None or height is None else (width, height)


def parse_widget_geometry(payload: dict[str, Any]) -> WidgetGeometry | None:
    """Return widget geometry from nested or flat Canvus shapes, if complete."""
    point = _point(payload)
    size = _size(payload)
    if point is None or size is None:
        return None
    return WidgetGeometry(point[0], point[1], size[0], size[1])


def _offset_from(anchor: WidgetGeometry, artifact_type: ArtifactType) -> tuple[float, float]:
    if artifact_type is ArtifactType.NEEDS_INPUT:
        return anchor.x, anchor.y + anchor.height + _GAP
    return anchor.x + anchor.width + _GAP, anchor.y


def _finite_point(x: float, y: float) -> tuple[float, float] | None:
    return (x, y) if math.isfinite(x) and math.isfinite(y) else None


async def resolve_artifact_position(
    mcp: MCPClient,
    *,
    canvas_id: str,
    anchor_widget_id: str,
    artifact_type: ArtifactType,
    fallback: tuple[float, float],
) -> tuple[float, float]:
    """Place a generated Browser near its anchor, falling back to the grid."""
    if not anchor_widget_id:
        return fallback
    try:
        raw = await mcp.call_tool("get_widget", {"canvas_id": canvas_id, "widget_id": anchor_widget_id})
        payload = json.loads(raw)
    except Exception:
        return fallback
    if not isinstance(payload, dict) or "error" in payload:
        return fallback
    anchor = parse_widget_geometry(payload)
    if anchor is None:
        return fallback
    offset = _offset_from(anchor, artifact_type)
    return _finite_point(*offset) or fallback


__all__ = ["WidgetGeometry", "parse_widget_geometry", "resolve_artifact_position"]
