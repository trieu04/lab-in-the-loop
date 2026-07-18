"""Experiment-workflow graph logic for the Lab-in-the-Loop use case.

Traverses the connector graph to find the **experiment loop** — a back-edge
connector from an ``[EXP:Result]`` widget to an ``[EXP:Setup]`` widget, which
is how a user asks the system to iterate an experiment. Widget classification
(what marks a Setup/Result/Robot/Closed/idea widget) lives in
:mod:`canvus_mcp.experiment_widgets`, which this module re-exports
``ExpMarkers`` from; here we focus on graph traversal and loop/snapshot
assembly. Built on the vendored :class:`~canvus_mcp.ragcluster.ConnectorIndex`;
dependency-free and unit-testable (no MCP, no network).

**loop**: connector ``result -> setup``.
"""

from __future__ import annotations

from typing import Any

from canvus_mcp.experiment_widgets import (
    ExpMarkers,
    _brief,
    _has_idea,
    _is_closed,
    _is_needs_input,
    _is_result,
    _is_robot,
    _is_setup,
    _round_of,
)
from canvus_mcp.ragcluster import ConnectorIndex, _endpoint_id, is_ragcluster_widget


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
        if _round_of(dst_w) > _round_of(src_w):
            # Forward edge: the orchestrator's own round-advance
            # (result_N -> setup_{N+1}) is graph-isomorphic to a user loop
            # trigger (result_N -> setup_N) but must not be re-detected as
            # one. Only same-round (==) or backward (<) edges are loops.
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
    ideas, setups, results, robots, closeds, needs_inputs = [], [], [], [], [], []
    for wid, w in index.widgets_by_id.items():
        if _has_idea(w, m):
            ideas.append(wid)
        elif _is_setup(w, m):
            setups.append(wid)
        elif _is_result(w, m):
            results.append(wid)
        elif _is_closed(w, m):
            closeds.append(wid)
        elif _is_needs_input(w, m):
            needs_inputs.append(wid)
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
        "closeds": [_brief(index.widgets_by_id[c]) for c in closeds],
        "needs_inputs": [_brief(index.widgets_by_id[n]) for n in needs_inputs],
        "ideas_needing_setup": ideas_needing_setup,
        "setups_needing_run": setups_needing_run,
        "loops": detect_experiment_loops(index, m),
    }


__all__ = ["ExpMarkers", "detect_experiment_loops", "scan_workflow"]
