"""Experiment-workflow tools: detect the loop and snapshot pending triggers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from canvus_mcp import experiments as exp
from canvus_mcp.client import get_client, get_settings
from canvus_mcp.ragcluster import ConnectorIndex

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _markers() -> exp.ExpMarkers:
    cfg = get_settings()
    return exp.ExpMarkers(
        ragcluster=cfg.mcp_ragcluster_marker,
        robot=cfg.mcp_robot_marker,
        setup=cfg.mcp_exp_setup_marker,
        result=cfg.mcp_exp_result_marker,
        idea=cfg.mcp_idea_marker,
    )


async def _build_index(canvas_id: str) -> ConnectorIndex:
    client = get_client()
    widgets = await client.widgets.list(canvas_id)
    return ConnectorIndex.build(widgets, get_settings().mcp_ragcluster_marker)


def register(mcp: FastMCP) -> None:
    """Attach experiment-workflow tools to ``mcp``."""

    @mcp.tool()
    async def detect_experiment_loops(canvas_id: str) -> dict[str, Any]:
        """Detect experiment loops on a canvas.

        A loop is a connector from an ``[EXP:Result]`` Note back to an
        ``[EXP:Setup]`` Note — the signal that the user wants the experiment
        iterated. Each detected loop resolves the participating widget ids
        (``setup_id``, ``result_id``, ``robot_id``, ``idea_id``,
        ``ragcluster_id``, ``loop_connector_id``) and the current ``round``.
        """
        index = await _build_index(canvas_id)
        loops = exp.detect_experiment_loops(index, _markers())
        return {"canvas_id": canvas_id, "loop_count": len(loops), "loops": loops}

    @mcp.tool()
    async def scan_experiment_workflow(canvas_id: str) -> dict[str, Any]:
        """Snapshot the whole experiment workflow on a canvas.

        Returns classified nodes (``ragclusters``, ``ideas``, ``setups``,
        ``results``, ``robots``), the pending forward triggers
        (``ideas_needing_setup`` — an idea connected from a RagCluster with no
        setup yet; ``setups_needing_run`` — a setup wired to a robot with no
        result yet, each carrying its ``robot_id``), and detected ``loops``.
        One call gives an agent everything it needs to drive the next step.
        """
        index = await _build_index(canvas_id)
        result = exp.scan_workflow(index, _markers())
        result["canvas_id"] = canvas_id
        return result


__all__ = ["register"]
