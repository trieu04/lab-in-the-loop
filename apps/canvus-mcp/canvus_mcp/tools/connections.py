"""Connection-check tools for specially-named widgets (e.g. RagCluster)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from canvus_mcp.client import get_client, get_settings
from canvus_mcp.ragcluster import (
    ConnectorIndex,
    connections_for_widget,
    is_ragcluster_widget,
)

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Attach connection-check tools to ``mcp``."""

    @mcp.tool()
    async def check_widget_connections(canvas_id: str, widget_id: str) -> dict[str, Any]:
        """Report every connector touching ``widget_id`` on a canvas.

        Builds a connector index from the canvas's widgets and returns the
        widget's ``incoming`` and ``outgoing`` connections, each naming the
        connector and the widget at the other end.
        """
        client = get_client()
        marker = get_settings().mcp_ragcluster_marker
        widgets = await client.widgets.list(canvas_id)
        index = ConnectorIndex.build(widgets, marker)
        if widget_id not in index.widgets_by_id:
            return {"canvas_id": canvas_id, "widget_id": widget_id, "found": False}
        conns = connections_for_widget(index, widget_id)
        target = index.widgets_by_id[widget_id]
        return {
            "canvas_id": canvas_id,
            "widget_id": widget_id,
            "found": True,
            "is_ragcluster": is_ragcluster_widget(target, marker),
            "incoming_count": len(conns["incoming"]),
            "outgoing_count": len(conns["outgoing"]),
            **conns,
        }

    @mcp.tool()
    async def check_ragcluster_connections(
        canvas_id: str,
        ragcluster_widget_id: str | None = None,
    ) -> dict[str, Any]:
        """Report what is connected to RagCluster widget(s) on a canvas.

        A RagCluster is an Image widget whose title starts with the configured
        marker (default ``RAGCluster_``). For each RagCluster, returns its
        incoming connections (e.g. PDFs/Notes feeding it) and outgoing
        connections (e.g. Notes it produces), classified by the connected
        widget's type.

        Args:
            canvas_id: Canvas to inspect.
            ragcluster_widget_id: Restrict to one RagCluster widget; when
                omitted, every RagCluster on the canvas is reported.
        """
        client = get_client()
        marker = get_settings().mcp_ragcluster_marker
        widgets = await client.widgets.list(canvas_id)
        index = ConnectorIndex.build(widgets, marker)

        target_ids = (
            [ragcluster_widget_id]
            if ragcluster_widget_id is not None
            else list(index.ragcluster_ids.keys())
        )

        clusters: list[dict[str, Any]] = []
        for wid in target_ids:
            widget = index.widgets_by_id.get(wid)
            if widget is None or not is_ragcluster_widget(widget, marker):
                clusters.append({"widget_id": wid, "found": False, "is_ragcluster": False})
                continue
            conns = connections_for_widget(index, wid)
            clusters.append(
                {
                    "widget_id": wid,
                    "found": True,
                    "is_ragcluster": True,
                    "title": index.ragcluster_ids.get(wid),
                    "incoming_count": len(conns["incoming"]),
                    "outgoing_count": len(conns["outgoing"]),
                    "inputs": conns["incoming"],
                    "outputs": conns["outgoing"],
                }
            )

        return {
            "canvas_id": canvas_id,
            "marker": marker,
            "ragcluster_count": len(index.ragcluster_ids),
            "clusters": clusters,
        }


__all__ = ["register"]
