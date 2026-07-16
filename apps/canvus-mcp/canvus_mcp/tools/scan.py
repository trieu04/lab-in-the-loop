"""Scan tools for the pre-configured Canvus server."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from canvus_mcp.client import get_client, get_settings
from canvus_mcp.ragcluster import is_ragcluster_widget

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _canvas_brief(canvas: Any) -> dict[str, Any]:
    return {
        "canvas_id": getattr(canvas, "id", None),
        "name": getattr(canvas, "name", None),
        "state": getattr(canvas, "state", None),
        "folder_id": getattr(canvas, "folder_id", None),
    }


def register(mcp: FastMCP) -> None:
    """Attach scan tools to ``mcp``."""

    @mcp.tool()
    async def list_canvases() -> dict[str, Any]:
        """List every canvas on the pre-configured Canvus server.

        Returns canvas id, name, state, and folder for each accessible canvas.
        """
        client = get_client()
        canvases = await client.canvases.list()
        return {
            "server": client.base_url,
            "count": len(canvases),
            "canvases": [_canvas_brief(c) for c in canvases],
        }

    @mcp.tool()
    async def scan_server(include_widget_counts: bool = False) -> dict[str, Any]:
        """Scan the pre-configured server and report RagCluster widgets.

        Walks every canvas, listing its widgets, and reports which canvases
        contain a "RagCluster" widget (an Image whose title starts with the
        configured marker, default ``RAGCluster_``). This mirrors how
        canvus-serving discovers RAG-enabled canvases.

        Args:
            include_widget_counts: When true, include each canvas's total
                widget count in the result.
        """
        client = get_client()
        marker = get_settings().mcp_ragcluster_marker
        canvases = await client.canvases.list()

        results: list[dict[str, Any]] = []
        total_clusters = 0
        for canvas in canvases:
            canvas_id = getattr(canvas, "id", "") or ""
            entry = _canvas_brief(canvas)
            if not canvas_id:
                entry["error"] = "canvas id missing"
                entry["ragclusters"] = []
                results.append(entry)
                continue
            try:
                widgets = await client.widgets.list(canvas_id)
            except Exception as exc:  # noqa: BLE001 — report per-canvas, keep scanning
                entry["error"] = str(exc)
                entry["ragclusters"] = []
                results.append(entry)
                continue

            clusters = [
                {"widget_id": getattr(w, "id", None), "title": getattr(w, "title", None)}
                for w in widgets
                if is_ragcluster_widget(w, marker)
            ]
            total_clusters += len(clusters)
            entry["ragclusters"] = clusters
            if include_widget_counts:
                entry["widget_count"] = len(widgets)
            results.append(entry)

        return {
            "server": client.base_url,
            "marker": marker,
            "canvas_count": len(canvases),
            "ragcluster_count": total_clusters,
            "canvases_with_ragcluster": [
                r for r in results if r.get("ragclusters")
            ],
            "canvases": results,
        }


__all__ = ["register"]
