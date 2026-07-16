"""RagCluster connection graph — self-contained, vendored for the MCP server.

A "RagCluster" is an :class:`~canvus_sdk.Image` widget whose ``title`` starts
with a marker prefix (default ``"RAGCluster_"``). Connections between widgets
are ``Connector`` widgets whose ``src``/``dst`` endpoints reference widget ids
via the ``id`` field (NOT ``widget_id``).

This module adapts ``canvus-serving``'s ``app/scanner/graph.py`` into a small
dependency-free helper: it duck-types widgets (works with SDK models or plain
dicts) and exposes an index plus a connection-summary builder used by the
``check_ragcluster_connections`` MCP tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

DEFAULT_MARKER = "RAGCluster_"
MAX_CHAIN_DEPTH = 5


def _attr(widget: Any, name: str, default: str = "") -> str:
    """Read a string attribute from an SDK model or dict widget."""
    if isinstance(widget, dict):
        value = widget.get(name, default)
    else:
        value = getattr(widget, name, default)
    return str(value) if value else default


def _widget_title(widget: Any) -> str:
    """Return a widget's title, falling back to a ``name`` extra field.

    SDK models expose ``title``; some servers carry the label under an extra
    ``name`` key (surfaced via pydantic ``model_extra``). Both are checked so a
    freshly-created RagCluster Image is still recognised.
    """
    title = _attr(widget, "title")
    if title:
        return title
    extra = getattr(widget, "model_extra", None)
    if isinstance(extra, dict):
        raw = extra.get("name", "")
        if isinstance(raw, str):
            return raw
    if isinstance(widget, dict):
        raw = widget.get("name", "")
        if isinstance(raw, str):
            return raw
    return ""


def is_ragcluster_widget(widget: Any, marker: str = DEFAULT_MARKER) -> bool:
    """True if ``widget`` is an Image whose title starts with ``marker``."""
    if _attr(widget, "widget_type") != "Image":
        return False
    return _widget_title(widget).startswith(marker)


def _endpoint_id(connector: Any, attr: str) -> str:
    """Extract the connected widget id from a connector's src/dst endpoint."""
    if isinstance(connector, dict):
        ep = connector.get(attr)
    else:
        ep = getattr(connector, attr, None)
    if ep is None:
        return ""
    if isinstance(ep, dict):
        return str(ep.get("id", "") or "")
    return str(getattr(ep, "id", "") or "")


@dataclass
class ConnectorIndex:
    """In-memory index of a canvas's widgets and connectors.

    Built once from a single ``widgets.list()`` result; provides O(1) lookups
    for connectivity and RagCluster identification.
    """

    marker: str = DEFAULT_MARKER
    connectors: dict[str, Any] = field(default_factory=dict)
    src_to_connectors: dict[str, list[str]] = field(default_factory=dict)
    dst_to_connectors: dict[str, list[str]] = field(default_factory=dict)
    widgets_by_id: dict[str, Any] = field(default_factory=dict)
    ragcluster_ids: dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(cls, widgets: list[Any], marker: str = DEFAULT_MARKER) -> ConnectorIndex:
        idx = cls(marker=marker)
        for w in widgets:
            wid = _attr(w, "id")
            if not wid:
                continue
            idx.widgets_by_id[wid] = w
            wtype = _attr(w, "widget_type")
            if wtype == "Connector":
                idx.connectors[wid] = w
                src_id = _endpoint_id(w, "src")
                dst_id = _endpoint_id(w, "dst")
                if src_id:
                    idx.src_to_connectors.setdefault(src_id, []).append(wid)
                if dst_id:
                    idx.dst_to_connectors.setdefault(dst_id, []).append(wid)
            elif is_ragcluster_widget(w, marker):
                idx.ragcluster_ids[wid] = _widget_title(w)
        return idx

    def endpoint_id(self, connector: Any, attr: str) -> str:
        """Public wrapper around :func:`_endpoint_id`."""
        return _endpoint_id(connector, attr)


def _widget_brief(widget: Any) -> dict[str, str]:
    """Compact description of a widget for connection reports."""
    return {
        "widget_id": _attr(widget, "id"),
        "widget_type": _attr(widget, "widget_type"),
        "title": _widget_title(widget),
    }


def connections_for_widget(index: ConnectorIndex, widget_id: str) -> dict[str, Any]:
    """Summarise every connection touching ``widget_id``.

    Returns a dict with ``incoming`` (connectors where the widget is the dst)
    and ``outgoing`` (where it is the src). Each entry names the connector and
    the widget at the other end, resolved via the index.
    """
    incoming: list[dict[str, Any]] = []
    for conn_id in index.dst_to_connectors.get(widget_id, []):
        conn = index.connectors.get(conn_id)
        other_id = _endpoint_id(conn, "src")
        incoming.append(
            {
                "connector_id": conn_id,
                "direction": "in",
                "other": _widget_brief(index.widgets_by_id.get(other_id, {"id": other_id})),
            }
        )
    outgoing: list[dict[str, Any]] = []
    for conn_id in index.src_to_connectors.get(widget_id, []):
        conn = index.connectors.get(conn_id)
        other_id = _endpoint_id(conn, "dst")
        outgoing.append(
            {
                "connector_id": conn_id,
                "direction": "out",
                "other": _widget_brief(index.widgets_by_id.get(other_id, {"id": other_id})),
            }
        )
    return {"incoming": incoming, "outgoing": outgoing}


__all__ = [
    "DEFAULT_MARKER",
    "MAX_CHAIN_DEPTH",
    "ConnectorIndex",
    "connections_for_widget",
    "is_ragcluster_widget",
]
