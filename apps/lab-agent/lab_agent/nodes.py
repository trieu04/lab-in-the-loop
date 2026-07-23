"""Canvas node helpers — the orchestrator's write surface.

System-generated stages are Browser widgets; legacy/user input remains Notes.
Both use title markers/connectors; only read tools are exposed to the model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from lab_agent.mcp_client import MCPClient
from lab_agent.models.states import DecisionState

EXP_SETUP = "[EXP:Setup"
EXP_RESULT = "[EXP:Result"
CLOSED = "[EXP:Closed]"
EXP_NEEDS_INPUT = "[EXP:Needs Input]"
EXP_VALIDATION = "[EXP:Validation]"
EXP_SCIENTIST_REVIEW = "[EXP:Scientist Review]"
EXP_LAB_LEAD_APPROVAL = "[EXP:Lab Lead Approval]"

_COLUMN_X = 0.0
_ROW_STEP = 420.0

# Generated-artifact Browser widget dimensions.
BROWSER_WIDTH = 480.0
BROWSER_HEIGHT = 360.0


@dataclass
class Layout:
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


class MCPToolError(Exception):
    """A write-tool call failed, or returned a response no write path may
    trust for a new widget id.

    ``MCPClient.call_tool`` does not raise on a tool-level failure -- a
    failed ``create_note``/``create_connector`` still returns normally, as a
    ``{"error": ...}`` JSON string (see ``mcp_client.py``). Without this
    check, ``res.get("id", "")`` would silently yield ``""``, which
    ``recovery.reconcile_or_execute`` would mark permanently ``RECONCILED``
    -- a phantom success. Raising here instead makes the failure propagate to
    ``reconcile_or_execute``'s ``execute()`` try/except, so the intent is
    marked failed and the caller's ``workflow_attempts`` row becomes
    retryable/backoff/quarantine, never falsely completed.
    """


def _parse_checked(raw: str, *, tool: str) -> dict[str, Any]:
    """Parse a write-tool's response; raise on malformed JSON, a non-object
    response, or an explicit ``error`` key -- fail closed on all three."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MCPToolError(f"{tool}: malformed JSON response") from exc
    if not isinstance(data, dict):
        raise MCPToolError(f"{tool}: non-object response")
    if "error" in data:
        raise MCPToolError(f"{tool} failed: {data['error']}")
    return data


def _require_id(data: dict[str, Any], *, tool: str) -> str:
    """Return ``data['id']``; raise if it is missing or empty."""
    widget_id = data.get("id") or ""
    if not widget_id:
        raise MCPToolError(f"{tool}: response missing a non-empty 'id'")
    return str(widget_id)


async def _call_checked(mcp: MCPClient, name: str, args: dict[str, Any]) -> str:
    """Call a create-type write tool; return its new widget id or raise
    :class:`MCPToolError`. Used by every write path (``create_node``,
    ``connect``) -- never by reads, which stay on the lenient ``_call_json``."""
    raw = await mcp.call_tool(name, args)
    return _require_id(_parse_checked(raw, tool=name), tool=name)


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
    caller composes the version into it. Raises :class:`MCPToolError` on
    failure -- never returns an empty id for a failed create.
    """
    text = body if state is None else f"{body}\n\nStatus: {state.value}"
    return await _call_checked(
        mcp,
        "create_note",
        {"canvas_id": canvas_id, "text": text, "x": x, "y": y, "title": title_marker[:120]},
    )


async def connect(mcp: MCPClient, canvas_id: str, src_id: str, dst_id: str) -> str:
    """Draw a directed connector src -> dst; return its widget id.

    Raises :class:`MCPToolError` on failure -- never returns an empty id for
    a failed connect, which would otherwise be recorded as a phantom edge.
    """
    if not src_id or not dst_id:
        return ""
    return await _call_checked(
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


async def _call_artifact_checked(
    mcp: MCPClient, name: str, args: dict[str, Any]
) -> str:
    try:
        return await _call_checked(mcp, name, args)
    except Exception:
        raise MCPToolError(f"{name} failed") from None


async def create_artifact_widget(
    mcp: MCPClient, canvas_id: str, title_marker: str, url: str, x: float, y: float
) -> str:
    """Create a generated-artifact Browser widget at ``url``; return its id.

    ``title_marker`` is the full workflow marker title (e.g.
    ``"[EXP:Setup v001] A+B #tag"``). Fail-closed via :func:`_call_checked`
    like :func:`create_node` -- an error/malformed response raises
    :class:`MCPToolError`, never a phantom empty id.
    """
    return await _call_artifact_checked(
        mcp, "create_browser",
        {
            "canvas_id": canvas_id, "url": url, "x": x, "y": y,
            "width": BROWSER_WIDTH, "height": BROWSER_HEIGHT, "title": title_marker[:120],
        },
    )


async def update_artifact_widget(
    mcp: MCPClient, canvas_id: str, browser_id: str, *,
    url: str, title: str, x: float | None = None, y: float | None = None,
) -> str:
    """Repair a Browser widget in place (fresh capability URL/marker title)
    without recreating it, so its id and connector position survive. Fail-closed
    like :func:`create_artifact_widget`."""
    args: dict[str, Any] = {
        "canvas_id": canvas_id, "browser_id": browser_id, "url": url, "title": title[:120],
    }
    if x is not None and y is not None:
        args["x"], args["y"] = x, y
    return await _call_artifact_checked(mcp, "update_browser", args)


async def read_note_text(mcp: MCPClient, canvas_id: str, note_id: str) -> str:
    """Return a Note's current text (empty string if unavailable)."""
    data = await _call_json(mcp, "get_note", {"canvas_id": canvas_id, "note_id": note_id})
    return str(data.get("text", "") or "")


__all__ = [
    "BROWSER_HEIGHT", "BROWSER_WIDTH", "CLOSED", "EXP_LAB_LEAD_APPROVAL", "EXP_NEEDS_INPUT",
    "EXP_RESULT", "EXP_SCIENTIST_REVIEW", "EXP_SETUP", "EXP_VALIDATION",
    "Layout", "MCPToolError", "connect", "create_artifact_widget", "create_node",
    "read_note_text", "update_artifact_widget",
]
