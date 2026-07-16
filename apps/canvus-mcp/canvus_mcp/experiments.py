"""Experiment-workflow graph logic for the Lab-in-the-Loop use case.

Classifies the workflow widgets on a canvas by their markers and traverses the
connector graph to find the **experiment loop** — a back-edge connector from an
``[EXP:Result]`` note to an ``[EXP:Setup]`` note, which is how a user asks the
system to iterate an experiment. Built on the vendored
:class:`~canvus_mcp.ragcluster.ConnectorIndex`; dependency-free and unit-testable
(no MCP, no network).

Workflow markers (title prefixes, except the idea marker which is in-text):
- knowledge scope : Image title starts ``RAGCluster_``
- idea note       : Note text contains ``{idea: ...}``
- experiment setup: Note title starts ``[EXP:Setup``
- robot           : any widget whose title starts ``Robot_``
- experiment result: Note title starts ``[EXP:Result``
- **loop**        : connector ``result -> setup``
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from canvus_mcp.ragcluster import (
    ConnectorIndex,
    _attr,
    _endpoint_id,
    _widget_title,
    is_ragcluster_widget,
)

_ROUND_RE = re.compile(r"v(\d+)")


@dataclass(frozen=True)
class ExpMarkers:
    """Marker prefixes that identify the experiment-workflow widgets."""

    ragcluster: str = "RAGCluster_"
    robot: str = "Robot_"
    setup: str = "[EXP:Setup"
    result: str = "[EXP:Result"
    idea: str = "{idea:"


def _has_idea(w: Any, m: ExpMarkers) -> bool:
    return _attr(w, "widget_type") == "Note" and m.idea in _attr(w, "text")


def _is_setup(w: Any, m: ExpMarkers) -> bool:
    return _attr(w, "widget_type") == "Note" and _widget_title(w).startswith(m.setup)


def _is_result(w: Any, m: ExpMarkers) -> bool:
    return _attr(w, "widget_type") == "Note" and _widget_title(w).startswith(m.result)


def _is_robot(w: Any, m: ExpMarkers) -> bool:
    return _widget_title(w).startswith(m.robot)


def _brief(w: Any) -> dict[str, str]:
    return {
        "widget_id": _attr(w, "id"),
        "widget_type": _attr(w, "widget_type"),
        "title": _widget_title(w),
    }


def _round_of(w: Any) -> int:
    match = _ROUND_RE.search(_widget_title(w))
    return int(match.group(1)) if match else 0


def _out_ids(index: ConnectorIndex, wid: str) -> list[str]:
    """Widget ids that ``wid`` points to (src -> dst)."""
    return [
        _endpoint_id(index.connectors[c], "dst")
        for c in index.src_to_connectors.get(wid, [])
    ]


def _in_ids(index: ConnectorIndex, wid: str) -> list[str]:
    """Widget ids that point to ``wid`` (src -> dst)."""
    return [
        _endpoint_id(index.connectors[c], "src")
        for c in index.dst_to_connectors.get(wid, [])
    ]


def _first_neighbor(index: ConnectorIndex, ids: list[str], pred) -> str:
    for nid in ids:
        w = index.widgets_by_id.get(nid)
        if w is not None and pred(w):
            return nid
    return ""


def detect_experiment_loops(index: ConnectorIndex, m: ExpMarkers) -> list[dict[str, Any]]:
    """Find every ``result -> setup`` back-edge (an experiment loop)."""
    loops: list[dict[str, Any]] = []
    for conn_id, conn in index.connectors.items():
        src_id = _endpoint_id(conn, "src")
        dst_id = _endpoint_id(conn, "dst")
        src_w = index.widgets_by_id.get(src_id)
        dst_w = index.widgets_by_id.get(dst_id)
        if src_w is None or dst_w is None:
            continue
        if not (_is_result(src_w, m) and _is_setup(dst_w, m)):
            continue
        robot_id = _first_neighbor(index, _out_ids(index, dst_id), lambda w: _is_robot(w, m))
        idea_id = _first_neighbor(index, _in_ids(index, dst_id), lambda w: _has_idea(w, m))
        rag_id = (
            _first_neighbor(index, _in_ids(index, idea_id), lambda w: is_ragcluster_widget(w, m.ragcluster))
            if idea_id
            else ""
        )
        loops.append(
            {
                "loop_connector_id": conn_id,
                "setup_id": dst_id,
                "result_id": src_id,
                "robot_id": robot_id,
                "idea_id": idea_id,
                "ragcluster_id": rag_id,
                "round": _round_of(dst_w),
            }
        )
    return loops


def scan_workflow(index: ConnectorIndex, m: ExpMarkers) -> dict[str, Any]:
    """One snapshot of the experiment workflow: nodes, pending triggers, loops."""
    ideas, setups, results, robots = [], [], [], []
    for wid, w in index.widgets_by_id.items():
        if _has_idea(w, m):
            ideas.append(wid)
        elif _is_setup(w, m):
            setups.append(wid)
        elif _is_result(w, m):
            results.append(wid)
        elif _is_robot(w, m):
            robots.append(wid)

    # Pending: idea connected from a RagCluster but with no setup downstream yet.
    ideas_needing_setup = []
    for i in ideas:
        rag = _first_neighbor(index, _in_ids(index, i), lambda w: is_ragcluster_widget(w, m.ragcluster))
        if not rag:
            continue
        if _first_neighbor(index, _out_ids(index, i), lambda w: _is_setup(w, m)):
            continue
        b = _brief(index.widgets_by_id[i])
        b["ragcluster_id"] = rag
        ideas_needing_setup.append(b)
    # Pending: setup wired to a robot but with no result observed yet.
    setups_needing_run = []
    for s in setups:
        robot_id = _first_neighbor(index, _out_ids(index, s), lambda w: _is_robot(w, m))
        if not robot_id:
            continue
        has_result = _first_neighbor(index, _out_ids(index, robot_id), lambda w: _is_result(w, m))
        if not has_result:
            b = _brief(index.widgets_by_id[s])
            b["robot_id"] = robot_id
            setups_needing_run.append(b)

    return {
        "ragclusters": [_brief(index.widgets_by_id[r]) for r in index.ragcluster_ids],
        "ideas": [_brief(index.widgets_by_id[i]) for i in ideas],
        "setups": [_brief(index.widgets_by_id[s]) for s in setups],
        "results": [_brief(index.widgets_by_id[r]) for r in results],
        "robots": [_brief(index.widgets_by_id[r]) for r in robots],
        "ideas_needing_setup": ideas_needing_setup,
        "setups_needing_run": setups_needing_run,
        "loops": detect_experiment_loops(index, m),
    }


__all__ = ["ExpMarkers", "detect_experiment_loops", "scan_workflow"]
