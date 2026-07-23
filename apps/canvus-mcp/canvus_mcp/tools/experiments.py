"""Experiment-workflow tools: detect the loop and snapshot pending triggers."""

from __future__ import annotations

from collections.abc import Callable
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
        closed=cfg.mcp_exp_closed_marker,
        needs_input=cfg.mcp_exp_needs_input_marker,
        validation=cfg.mcp_exp_validation_marker,
        scientist_review=cfg.mcp_exp_scientist_review_marker,
        lab_lead_approval=cfg.mcp_exp_lab_lead_approval_marker,
    )


async def _build_index(canvas_id: str) -> ConnectorIndex:
    client = get_client()
    widgets = await client.widgets.list(canvas_id)
    return ConnectorIndex.build(widgets, get_settings().mcp_ragcluster_marker)


def register(
    mcp: FastMCP,
    *,
    classification_for_canvas: Callable[[str], str],
) -> None:
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
        ``results``, ``robots``, ``closeds``, ``needs_inputs``), the pending
        forward triggers (``ideas_needing_setup`` — an idea connected from a
        RagCluster with no setup yet; ``setups_needing_run`` — a setup wired
        to a robot with no result yet), pending validation/review requests,
        and detected ``loops``. Gate markers are non-authorizing metadata:
        their presence never establishes an actor, role, decision, or approval.
        ``closeds``/``needs_inputs`` make terminal
        ``[EXP:Closed]`` and generated ``[EXP:Needs Input]`` widgets enumerable
        the same way ``setups``/``results`` are, so a caller can recover a
        prior run's widget by its idempotency tag without depending on any
        connector having been drawn yet.
        One call gives an agent everything it needs to drive the next step.
        """
        index = await _build_index(canvas_id)
        result = exp.scan_workflow(index, _markers())
        data_classification = classification_for_canvas(canvas_id)
        for bucket in (
            "ideas_needing_setup",
            "mode_errors",
            "setups_needing_run",
            "setups_needing_validation",
            "validations_needing_scientist_review",
            "scientist_reviews_needing_lab_lead_approval",
            "loops",
        ):
            result[bucket] = [
                {**item, "data_classification": data_classification} for item in result[bucket]
            ]
        result["canvas_id"] = canvas_id
        return result


__all__ = ["register"]
