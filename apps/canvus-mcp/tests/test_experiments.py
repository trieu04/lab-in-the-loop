"""Tests for experiment-workflow classification and loop detection."""

from __future__ import annotations

from canvus_mcp.experiments import ExpMarkers, detect_experiment_loops, scan_workflow
from canvus_mcp.ragcluster import ConnectorIndex

M = ExpMarkers()


def _w(wid, wtype, title="", text=""):
    return {"id": wid, "widget_type": wtype, "title": title, "text": text}


def _conn(cid, src, dst):
    return {"id": cid, "widget_type": "Connector", "src": {"id": src}, "dst": {"id": dst}}


def _index(widgets):
    return ConnectorIndex.build(widgets, M.ragcluster)


# A canvas with the full chain rag -> idea -> setup -> robot -> result, plus the
# loop back-edge result -> setup.
def _full_loop_widgets():
    return [
        _w("rag1", "Image", title="RAGCluster_lung"),
        _w("idea1", "Note", text="{idea: Combine A with B}"),
        _w("setup1", "Note", title="[EXP:Setup v001] A+B"),
        _w("robot1", "Note", title="Robot_arm"),
        _w("result1", "Note", title="[EXP:Result v001]"),
        _conn("c1", "rag1", "idea1"),
        _conn("c2", "idea1", "setup1"),
        _conn("c3", "setup1", "robot1"),
        _conn("c4", "robot1", "result1"),
        _conn("c5", "result1", "setup1"),  # the loop
    ]


def test_detect_loop_resolves_all_participants():
    loops = detect_experiment_loops(_index(_full_loop_widgets()), M)
    assert len(loops) == 1
    loop = loops[0]
    assert loop["setup_id"] == "setup1"
    assert loop["result_id"] == "result1"
    assert loop["robot_id"] == "robot1"
    assert loop["idea_id"] == "idea1"
    assert loop["ragcluster_id"] == "rag1"
    assert loop["loop_connector_id"] == "c5"
    assert loop["round"] == 1


def test_forward_only_graph_has_no_loop():
    widgets = [w for w in _full_loop_widgets() if w.get("id") != "c5"]
    assert detect_experiment_loops(_index(widgets), M) == []


def test_scan_reports_idea_needing_setup():
    widgets = [
        _w("rag1", "Image", title="RAGCluster_x"),
        _w("idea1", "Note", text="{idea: try X}"),
        _conn("c1", "rag1", "idea1"),
    ]
    snap = scan_workflow(_index(widgets), M)
    pending = snap["ideas_needing_setup"]
    assert [b["widget_id"] for b in pending] == ["idea1"]
    assert pending[0]["ragcluster_id"] == "rag1"
    assert snap["setups_needing_run"] == []
    assert snap["loops"] == []


def test_scan_reports_setup_needing_run():
    widgets = [
        _w("setup1", "Note", title="[EXP:Setup v001]"),
        _w("robot1", "Note", title="Robot_arm"),
        _conn("c1", "setup1", "robot1"),
    ]
    snap = scan_workflow(_index(widgets), M)
    pending = snap["setups_needing_run"]
    assert len(pending) == 1
    assert pending[0]["widget_id"] == "setup1"
    assert pending[0]["robot_id"] == "robot1"


def test_scan_setup_with_result_is_not_pending():
    widgets = [
        _w("setup1", "Note", title="[EXP:Setup v001]"),
        _w("robot1", "Note", title="Robot_arm"),
        _w("result1", "Note", title="[EXP:Result v001]"),
        _conn("c1", "setup1", "robot1"),
        _conn("c2", "robot1", "result1"),
    ]
    snap = scan_workflow(_index(widgets), M)
    assert snap["setups_needing_run"] == []
    assert [b["widget_id"] for b in snap["results"]] == ["result1"]
