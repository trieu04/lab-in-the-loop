"""Canvas node helpers — the orchestrator's write surface.

Each stage of the experiment loop is a Note with a title-prefix marker, wired to
the previous stage with a directed connector. These call the canvus-mcp **write**
tools directly (they are not exposed to the model; the model only reads).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from lab_agent.mcp_client import MCPClient
from lab_agent.models.states import DecisionState

# Title-prefix markers for the experiment-loop workflow. Setup/result markers are
# completed as "[EXP:Setup vNNN]" / "[EXP:Result vNNN]".
EXP_SETUP = "[EXP:Setup"
EXP_RESULT = "[EXP:Result"
CLOSED = "[EXP:Closed]"

_COLUMN_X = 0.0
_ROW_STEP = 420.0


@dataclass
class Layout:
    """Stacks nodes in a vertical column so connectors read top-to-bottom."""

    x: float = _COLUMN_X
    y: float = 0.0
    step: float = _ROW_STEP

    def next(self) -> tuple[float, float]:
        pos = (self.x, self.y)
        self.y += self.step
        return pos


async def _call_json(mcp: MCPClient, name: str, args: dict[str, Any]) -> dict[str, Any]:
    raw = await mcp.call_tool(name, args)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


async def create_node(
    mcp: MCPClient,
    canvas_id: str,
    title_marker: str,
    body: str,
    x: float,
    y: float,
    state: DecisionState | None = None,
) -> str:
    """Create a Note whose title is ``title_marker``; return its widget id.

    ``title_marker`` is the full title (e.g. ``"[EXP:Setup v001] A+B"``); the
    caller composes the version into it.
    """
    text = body if state is None else f"{body}\n\nStatus: {state.value}"
    res = await _call_json(
        mcp,
        "create_note",
        {"canvas_id": canvas_id, "text": text, "x": x, "y": y, "title": title_marker[:120]},
    )
    return res.get("id", "") or ""


async def connect(mcp: MCPClient, canvas_id: str, src_id: str, dst_id: str) -> str:
    """Draw a directed connector src -> dst; return its widget id."""
    if not src_id or not dst_id:
        return ""
    res = await _call_json(
        mcp,
        "create_connector",
        {
            "canvas_id": canvas_id,
            "src_widget_id": src_id,
            "dst_widget_id": dst_id,
            "src_tip": "none",
            "dst_tip": "solid-equilateral-triangle",
        },
    )
    return res.get("id", "") or ""


async def read_note_text(mcp: MCPClient, canvas_id: str, note_id: str) -> str:
    """Return a Note's current text (empty string if unavailable)."""
    data = await _call_json(mcp, "get_note", {"canvas_id": canvas_id, "note_id": note_id})
    return str(data.get("text", "") or "")


__all__ = ["CLOSED", "EXP_RESULT", "EXP_SETUP", "Layout", "connect", "create_node", "read_note_text"]
