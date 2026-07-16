"""Live-canvas reconciliation probes for the durable outbox.

canvus-mcp write tools (``create_note``/``create_connector``) are NOT
idempotent, and there is no ``update_note``/search-by-text tool. A crash
between "intent persisted" and "mark reconciled" (``recovery.py``) can only
be detected by re-deriving canvas state from the existing MCP *read* tools.
These probes are the ``live_probe`` callables passed to
``recovery.reconcile_or_execute`` -- they fail closed: any MCP/parse error
propagates (never silently treated as "not found", which would risk a
duplicate write).

Setup/result/closed notes all carry a short discriminator TAG (first 12 hex
chars of the sha256 idempotency key) appended to the title, e.g.
``"[EXP:Setup v001] idea text #a1b2c3d4e5f6"``. Hex digits never contain
``v``, so the tag can never collide with canvus-mcp's round marker
(``_ROUND_RE = re.compile(r"v(\\d+)")`` in ``canvus_mcp.experiments``).
Probing scans ``scan_experiment_workflow``'s ``setups``/``results``/``closeds``
buckets for the tag -- connector-independent, so it closes the crash window
"note created, connector not yet drawn, process killed" for all three note
kinds, including the terminal closed note (whose only outgoing edge, the
``result -> closed`` connector, used to be the *sole* recovery signal before
this tagging was extended to it).

Connector creation is probed via ``check_widget_connections`` on the known
source widget, exact-matching the destination id -- reliable, since by the
time a connector-creation intent exists both endpoints already exist.
"""

from __future__ import annotations

import json
from typing import Any

from lab_agent.mcp_client import MCPClient

_TAG_LEN = 12


def tag_suffix(idempotency_key: str) -> str:
    """First 12 hex chars of a sha256 idempotency key, safe as a title suffix."""
    return idempotency_key[:_TAG_LEN]


def tagged_title(title: str, idempotency_key: str) -> str:
    """Append the discriminator tag to ``title``, fitting within the 120-char note-title cap."""
    tag = f" #{tag_suffix(idempotency_key)}"
    return f"{title[: 120 - len(tag)]}{tag}"


def _parse(raw: str, *, tool: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(raw)
    if "error" in payload:
        raise RuntimeError(f"{tool} failed: {payload['error']}")
    return payload


async def _scan_bucket_for_tag(
    mcp: MCPClient, *, canvas_id: str, bucket: str, idempotency_key: str, widget_type: str | None
) -> str | None:
    """Scan one ``scan_experiment_workflow`` bucket for a tagged widget.

    ``widget_type`` narrows the match to that widget kind (e.g. ``"Browser"``);
    ``None`` matches any widget in the bucket. Raises on any MCP/parse error --
    fail closed (never silently treated as not-found).
    """
    raw = await mcp.call_tool("scan_experiment_workflow", {"canvas_id": canvas_id})
    snapshot = _parse(raw, tool="scan_experiment_workflow")
    needle = f"#{tag_suffix(idempotency_key)}"
    for entry in snapshot.get(bucket, []):
        if widget_type is not None and entry.get("widget_type") != widget_type:
            continue
        if needle in entry.get("title", ""):
            return str(entry["widget_id"])
    return None


async def probe_note_by_tag(
    mcp: MCPClient, *, canvas_id: str, bucket: str, idempotency_key: str
) -> str | None:
    """Find a setup/result/closed note already carrying this intent's tag.

    ``bucket`` is ``"setups"``, ``"results"``, or ``"closeds"`` (the
    ``scan_experiment_workflow`` keys). Returns the widget id if found, else
    ``None``. Raises on any MCP/parse error -- fail closed.
    """
    return await _scan_bucket_for_tag(
        mcp, canvas_id=canvas_id, bucket=bucket, idempotency_key=idempotency_key, widget_type=None
    )


async def probe_browser_by_tag(
    mcp: MCPClient, *, canvas_id: str, bucket: str, idempotency_key: str
) -> str | None:
    """Browser-widget sibling of :func:`probe_note_by_tag` for generated
    artifacts rendered as capability-protected Browser widgets.

    Matches only ``widget_type == "Browser"`` entries so a legacy Note and a
    generated Browser sharing a bucket are never confused. Returns the widget
    id if found, else ``None``; raises on any MCP/parse error -- fail closed.
    """
    return await _scan_bucket_for_tag(
        mcp, canvas_id=canvas_id, bucket=bucket, idempotency_key=idempotency_key, widget_type="Browser"
    )


async def probe_connector(mcp: MCPClient, *, canvas_id: str, src_id: str, dst_id: str) -> str | None:
    """Find an existing ``src_id -> dst_id`` connector by exact endpoint match.

    Returns the connector id if found, else ``None``. Raises on any
    MCP/parse error -- fail closed.
    """
    raw = await mcp.call_tool("check_widget_connections", {"canvas_id": canvas_id, "widget_id": src_id})
    report = _parse(raw, tool="check_widget_connections")
    if not report.get("found", False):
        return None
    for entry in report.get("outgoing", []):
        if entry.get("other", {}).get("widget_id") == dst_id:
            return str(entry["connector_id"])
    return None


__all__ = [
    "probe_browser_by_tag",
    "probe_connector",
    "probe_note_by_tag",
    "tag_suffix",
    "tagged_title",
]
