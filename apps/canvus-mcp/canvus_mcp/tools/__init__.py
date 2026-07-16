"""Registration of the Canvus MCP tool surface on one shared runtime."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from canvus_mcp.tools import connections, content, experiments, ingestion, scan, widgets

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from canvus_mcp.access_control import AccessPolicy
    from canvus_mcp.ingestion_pipeline import IngestionPipeline


def register_all(
    mcp: FastMCP,
    *,
    policy: AccessPolicy,
    pipeline: IngestionPipeline,
    max_chunk_chars: int,
    max_source_bytes: int,
    classification_for_canvas: Callable[[str], str],
) -> None:
    """Register every module, passing the same policy and ingestion lifecycle."""
    scan.register(mcp)
    content.register(mcp, classification_for_canvas=classification_for_canvas)
    widgets.register(mcp, policy=policy)
    connections.register(mcp, classification_for_canvas=classification_for_canvas)
    experiments.register(mcp, classification_for_canvas=classification_for_canvas)
    ingestion.register(
        mcp,
        policy=policy,
        pipeline=pipeline,
        max_chunk_chars=max_chunk_chars,
        max_source_bytes=max_source_bytes,
        classification_for_canvas=classification_for_canvas,
    )


__all__ = ["register_all"]
