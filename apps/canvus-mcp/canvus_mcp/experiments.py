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

from canvus_mcp.experiment_gates import first_neighbor, in_ids, out_ids, scan_pending_gates
from canvus_mcp.experiment_widgets import (
    ExpMarkers,
    IdeaMarkerParse,
    _brief,
    _has_idea,
    _is_closed,
    _is_lab_lead_approval_marker,
    _is_needs_input,
    _is_result,
    _is_robot,
    _is_scientist_review_marker,
    _is_setup,
    _is_validation,
    _mode_error_brief,
    _round_of,
    parse_idea_marker,
)
from canvus_mcp.ragcluster import ConnectorIndex, _attr, _endpoint_id, is_ragcluster_widget


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
        robot_id = first_neighbor(index, out_ids(index, dst_id), lambda w: _is_robot(w, m))
        idea_id = first_neighbor(index, in_ids(index, dst_id), lambda w: _has_idea(w, m))
        rag_id = (
            first_neighbor(index, in_ids(index, idea_id), lambda w: is_ragcluster_widget(w, m.ragcluster))
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
    validations, scientist_review_markers, lab_lead_approval_markers = [], [], []
    idea_parses: dict[str, IdeaMarkerParse] = {}
    mode_errors: list[dict[str, str]] = []
    for wid, w in index.widgets_by_id.items():
        if _attr(w, "widget_type") == "Note":
            parsed = parse_idea_marker(_attr(w, "text"), m.idea)
            if parsed.parse_error is not None:
                mode_errors.append(_mode_error_brief(w, parsed.parse_error))
            elif parsed.is_idea:
                idea_parses[wid] = parsed
                ideas.append(wid)
                continue
        if _is_setup(w, m):
            setups.append(wid)
        elif _is_result(w, m):
            results.append(wid)
        elif _is_closed(w, m):
            closeds.append(wid)
        elif _is_needs_input(w, m):
            needs_inputs.append(wid)
        elif _is_validation(w, m):
            validations.append(wid)
        elif _is_scientist_review_marker(w, m):
            scientist_review_markers.append(wid)
        elif _is_lab_lead_approval_marker(w, m):
            lab_lead_approval_markers.append(wid)
        elif _is_robot(w, m):
            robots.append(wid)

    # Pending: idea connected from a RagCluster but with no setup downstream yet.
    ideas_needing_setup = []
    for i in ideas:
        rag = first_neighbor(index, in_ids(index, i), lambda w: is_ragcluster_widget(w, m.ragcluster))
        if not rag:
            continue
        if first_neighbor(index, out_ids(index, i), lambda w: _is_setup(w, m)):
            continue
        b = _brief(index.widgets_by_id[i], idea_parses[i].execution_mode)
        b["ragcluster_id"] = rag
        ideas_needing_setup.append(b)
    # Pending: setup wired to a robot but with no result observed yet.
    setups_needing_run = []
    for s in setups:
        robot_id = first_neighbor(index, out_ids(index, s), lambda w: _is_robot(w, m))
        if not robot_id:
            continue
        has_result = first_neighbor(index, out_ids(index, robot_id), lambda w: _is_result(w, m))
        if not has_result:
            b = _brief(index.widgets_by_id[s])
            b["robot_id"] = robot_id
            setups_needing_run.append(b)

    pending_gates = scan_pending_gates(index, m, setups, validations, scientist_review_markers)
    return {
        "ragclusters": [_brief(index.widgets_by_id[r]) for r in index.ragcluster_ids],
        "ideas": [_brief(index.widgets_by_id[i], idea_parses[i].execution_mode) for i in ideas],
        "setups": [_brief(index.widgets_by_id[s]) for s in setups],
        "results": [_brief(index.widgets_by_id[r]) for r in results],
        "robots": [_brief(index.widgets_by_id[r]) for r in robots],
        "closeds": [_brief(index.widgets_by_id[c]) for c in closeds],
        "needs_inputs": [_brief(index.widgets_by_id[n]) for n in needs_inputs],
        "mode_errors": mode_errors,
        "validations": [_brief(index.widgets_by_id[v]) for v in validations],
        "scientist_review_markers": [_brief(index.widgets_by_id[r]) for r in scientist_review_markers],
        "lab_lead_approval_markers": [_brief(index.widgets_by_id[a]) for a in lab_lead_approval_markers],
        "ideas_needing_setup": ideas_needing_setup,
        "setups_needing_run": setups_needing_run,
        **pending_gates,
        "loops": detect_experiment_loops(index, m),
    }


__all__ = ["ExpMarkers", "detect_experiment_loops", "scan_workflow"]
