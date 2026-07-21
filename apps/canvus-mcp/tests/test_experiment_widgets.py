"""Tests for Browser-widget classification in the experiment workflow.

Split out of ``test_experiments.py``: a Phase 3 Browser widget can carry the
same generated-artifact markers (setup/result/closed) as a legacy Note, while
idea/human-input stays Note-only. Exercises the widened classification
(``canvus_mcp.experiment_widgets``) through the public
``detect_experiment_loops``/``scan_workflow`` API on ``canvus_mcp.experiments``,
mirroring ``test_experiments.py``'s fixture idiom.
"""

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


def test_browser_setup_and_result_are_recognized():
    """A Phase 3 Browser widget with a setup/result marker classifies the same
    as a legacy Note -- generated artifacts may be rendered as either."""
    widgets = [
        _w("setup1", "Browser", title="[EXP:Setup v001] A+B"),
        _w("robot1", "Note", title="Robot_arm"),
        _w("result1", "Browser", title="[EXP:Result v001]"),
        _conn("c1", "setup1", "robot1"),
        _conn("c2", "robot1", "result1"),
    ]
    snap = scan_workflow(_index(widgets), M)
    assert [b["widget_id"] for b in snap["setups"]] == ["setup1"]
    assert [b["widget_id"] for b in snap["results"]] == ["result1"]
    assert snap["ideas"] == []


def test_browser_setup_and_result_are_recognized_from_name_fallback():
    """If a Browser widget surfaces its label under ``name`` instead of
    ``title``, the scan path should still classify it."""
    widgets = [
        {"id": "setup1", "widget_type": "Browser", "title": "", "name": "[EXP:Setup v001] A+B"},
        _w("robot1", "Note", title="Robot_arm"),
        {"id": "result1", "widget_type": "Browser", "title": "", "name": "[EXP:Result v001]"},
        _conn("c1", "setup1", "robot1"),
        _conn("c2", "robot1", "result1"),
    ]
    snap = scan_workflow(_index(widgets), M)
    assert [b["widget_id"] for b in snap["setups"]] == ["setup1"]
    assert [b["widget_id"] for b in snap["results"]] == ["result1"]


def test_mixed_note_and_browser_graph_detects_loop():
    """A canvas with a Note setup and Browser result (mixed during migration)
    still forms a loop -- classification does not require both ends to match
    widget type."""
    widgets = [
        _w("setup1", "Note", title="[EXP:Setup v001] A+B"),
        _w("robot1", "Note", title="Robot_arm"),
        _w("result1", "Browser", title="[EXP:Result v001]"),
        _conn("c1", "setup1", "robot1"),
        _conn("c2", "robot1", "result1"),
        _conn("c3", "result1", "setup1"),  # the loop
    ]
    loops = detect_experiment_loops(_index(widgets), M)
    assert len(loops) == 1
    assert loops[0]["setup_id"] == "setup1"
    assert loops[0]["result_id"] == "result1"
    assert loops[0]["robot_id"] == "robot1"


def test_browser_setup_forward_round_advance_edge_still_excluded():
    """The forward round-advance exclusion (see
    ``test_forward_round_advance_edge_is_excluded`` in ``test_experiments.py``)
    must keep working when the widened Note-or-Browser classification applies
    to the setup/result nodes, not just the legacy Note case."""
    widgets = [
        _w("setup1", "Browser", title="[EXP:Setup v001] A+B"),
        _w("robot1", "Note", title="Robot_arm"),
        _w("result1", "Browser", title="[EXP:Result v001]"),
        _w("setup2", "Browser", title="[EXP:Setup v002] A+B"),
        _conn("c1", "setup1", "robot1"),
        _conn("c2", "robot1", "result1"),
        _conn("c3", "result1", "setup1"),  # same-round: actionable loop
        _conn("c4", "result1", "setup2"),  # forward round-advance: not a loop
    ]
    loops = detect_experiment_loops(_index(widgets), M)
    assert [loop["loop_connector_id"] for loop in loops] == ["c3"]


def test_browser_closed_widget_still_recognized():
    """``closed`` classification already had no ``widget_type`` restriction;
    confirm a Browser-typed closed widget keeps working unchanged."""
    widgets = [_w("closed1", "Browser", title="[EXP:Closed] after v001")]
    snap = scan_workflow(_index(widgets), M)
    assert [b["widget_id"] for b in snap["closeds"]] == ["closed1"]


def test_browser_idea_marker_is_rejected():
    """Idea/human input stays Note-only: a Browser widget whose text happens
    to contain the idea marker must NOT be classified as an idea."""
    widgets = [_w("idea1", "Browser", text="{idea: should not count}")]
    snap = scan_workflow(_index(widgets), M)
    assert snap["ideas"] == []


def test_browser_needs_input_is_recognized_and_enumerable_without_a_connector():
    """A generated ``[EXP:Needs Input]`` prompt classifies the same whether it
    is a legacy Note or a Phase 3 Browser widget, and -- like ``closed`` -- is
    enumerable in its own ``needs_inputs`` bucket without requiring a
    connector, so recovery can tag-probe it independent of the graph."""
    widgets = [
        _w("ni1", "Browser", title="[EXP:Needs Input] round 1 #a1b2c3d4e5f6"),
        _w("ni2", "Note", title="[EXP:Needs Input] round 2"),
    ]
    snap = scan_workflow(_index(widgets), M)
    assert {b["widget_id"] for b in snap["needs_inputs"]} == {"ni1", "ni2"}
    assert snap["ideas"] == snap["setups"] == snap["results"] == snap["closeds"] == []


def test_needs_input_marker_does_not_shadow_setup_or_result():
    """``[EXP:Needs Input]`` and ``[EXP:Setup``/``[EXP:Result`` are disjoint
    prefixes -- classification order between them must not matter."""
    widgets = [
        _w("setup1", "Note", title="[EXP:Setup v001] A+B"),
        _w("result1", "Note", title="[EXP:Result v001]"),
        _w("ni1", "Note", title="[EXP:Needs Input] round 1"),
    ]
    snap = scan_workflow(_index(widgets), M)
    assert [b["widget_id"] for b in snap["setups"]] == ["setup1"]
    assert [b["widget_id"] for b in snap["results"]] == ["result1"]
    assert [b["widget_id"] for b in snap["needs_inputs"]] == ["ni1"]


def test_human_response_note_to_needs_input_is_not_itself_classified():
    """The human's *response* to a needs-input prompt is a plain Note this
    detector never gives special treatment -- it is invisible to every
    generated-artifact bucket unless it happens to carry the idea marker."""
    widgets = [
        _w("ni1", "Browser", title="[EXP:Needs Input] round 1"),
        _w("resp1", "Note", text="Go ahead with option B"),
    ]
    snap = scan_workflow(_index(widgets), M)
    assert [b["widget_id"] for b in snap["needs_inputs"]] == ["ni1"]
    assert snap["ideas"] == []  # the response note is not an idea either
