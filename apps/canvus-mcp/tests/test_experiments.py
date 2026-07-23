"""Tests for experiment-workflow classification and loop detection."""

from __future__ import annotations

import pytest

from canvus_mcp.experiments import ExpMarkers, detect_experiment_loops, scan_workflow
from canvus_mcp.ragcluster import ConnectorIndex

M = ExpMarkers()


def _w(wid, wtype, title="", text=""):
    return {"id": wid, "widget_type": wtype, "title": title, "text": text}


def _conn(cid, src, dst):
    return {"id": cid, "widget_type": "Connector", "src": {"id": src}, "dst": {"id": dst}}


def _index(widgets):
    return ConnectorIndex.build(widgets, M.ragcluster)


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


def test_forward_round_advance_edge_is_excluded():
    """Forward round-advance edges are not actionable loop triggers."""
    widgets = [
        _w("setup1", "Note", title="[EXP:Setup v001] A+B"),
        _w("robot1", "Note", title="Robot_arm"),
        _w("result1", "Note", title="[EXP:Result v001]"),
        _w("setup2", "Note", title="[EXP:Setup v002] A+B"),
        _conn("c1", "setup1", "robot1"),
        _conn("c2", "robot1", "result1"),
        _conn("c3", "result1", "setup1"),  # same-round: actionable loop
        _conn("c4", "result1", "setup2"),  # forward round-advance: not a loop
    ]
    loops = detect_experiment_loops(_index(widgets), M)
    assert [loop["loop_connector_id"] for loop in loops] == ["c3"]
    assert loops[0]["setup_id"] == "setup1"


def test_strictly_backward_edge_is_still_a_loop():
    """round(setup) < round(result) (e.g. a user reconnects a v2 result back to
    the original v1 setup) is a backward edge, not forward — still actionable."""
    widgets = [
        _w("setup1", "Note", title="[EXP:Setup v001] A+B"),
        _w("result2", "Note", title="[EXP:Result v002]"),
        _conn("c1", "result2", "setup1"),
    ]
    loops = detect_experiment_loops(_index(widgets), M)
    assert [loop["loop_connector_id"] for loop in loops] == ["c1"]


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


@pytest.mark.parametrize(
    ("setup_title", "result_title", "is_pending", "stale_result_id"),
    [
        ("[EXP:Setup v001]", None, True, None),
        ("[EXP:Setup v002]", "[EXP:Result v001]", True, "result1"),
        ("[EXP:Setup v001]", "[EXP:Result v001]", False, None),
        ("[EXP:Setup] legacy", "[EXP:Result] legacy", False, None),
    ],
)
def test_scan_schedules_only_missing_or_stale_results(setup_title, result_title, is_pending, stale_result_id):
    setup = _w("setup1", "Note", title=setup_title)
    setup["execution_authorized"] = True
    widgets = [setup, _w("robot1", "Note", title="Robot_arm"), _conn("c1", "setup1", "robot1")]
    if result_title:
        widgets += [_w("result1", "Note", title=result_title), _conn("c2", "robot1", "result1")]
    pending = scan_workflow(_index(widgets), M)["setups_needing_run"]
    assert bool(pending) is is_pending
    if pending:
        assert pending[0] == {
            "widget_id": "setup1", "widget_type": "Note", "title": setup_title,
            "robot_id": "robot1", **({"stale_result_id": stale_result_id} if stale_result_id else {}),
        }


def test_scan_preserves_legacy_idea_precedence_and_additive_mode_errors():
    snap = scan_workflow(_index([
        _w("rag", "Image", "RAGCluster_scope"), _conn("source", "rag", "closed1"),
        _w("closed1", "Note", "[EXP:Closed] after v001", "{idea+batch: stop}"),
        _w("loose", "Note", text="{idea+batch: ignore while disconnected}"),
        _w("legacy-setup", "Note", "[EXP:Setup v001]", "{idea: preserve precedence}"),
    ]), M)
    assert [brief["widget_id"] for brief in snap["closeds"]] == ["closed1"]
    assert snap["mode_errors"] == [{"widget_id": "closed1", "widget_type": "Note", "parse_error": "unsupported_mode"}]
    assert [brief["widget_id"] for brief in snap["ideas"]] == ["legacy-setup"]
    assert snap["setups"] == snap["results"] == snap["robots"] == []


def test_closed_note_is_not_misclassified_as_robot():
    widgets = [_w("closed1", "Note", title="[EXP:Closed] after v001")]
    snap = scan_workflow(_index(widgets), M)
    assert snap["robots"] == []
    assert [b["widget_id"] for b in snap["closeds"]] == ["closed1"]


def test_scan_reports_ordered_gate_requests():
    setup = [_w("setup", "Note", title="[EXP:Setup v001]")]
    validation = _w("validation", "Browser", title="[EXP:Validation] proceed")
    review = _w("review", "Note", title="[EXP:Scientist Review]")
    first = scan_workflow(_index(setup), M)
    second = scan_workflow(_index(setup + [validation, _conn("sv", "setup", "validation")]), M)
    third = scan_workflow(
        _index(setup + [validation, review, _conn("sv", "setup", "validation"), _conn("vr", "validation", "review")]), M
    )
    assert [item["widget_id"] for item in first["setups_needing_validation"]] == ["setup"]
    assert second["validations_needing_scientist_review"] == [{"widget_id": "validation", "widget_type": "Browser", "title": "[EXP:Validation] proceed", "setup_id": "setup"}]
    assert third["scientist_reviews_needing_lab_lead_approval"] == [{"widget_id": "review", "widget_type": "Note", "title": "[EXP:Scientist Review]", "setup_id": "setup", "validation_id": "validation"}]
    complete = setup + [validation, review, _w("lab", "Note", title="[EXP:Lab Lead Approval]")]
    complete += [_conn("sv", "setup", "validation"), _conn("vr", "validation", "review"), _conn("rl", "review", "lab")]
    assert scan_workflow(_index(complete), M)["scientist_reviews_needing_lab_lead_approval"] == []


def test_gate_requests_require_the_expected_connector():
    widgets = [
        _w("setup", "Note", title="[EXP:Setup v001]"),
        _w("validation", "Browser", title="[EXP:Validation] proceed"),
        _w("review", "Note", title="[EXP:Scientist Review]"),
        _conn("sv", "setup", "validation"),
    ]
    snap = scan_workflow(_index(widgets), M)
    assert [item["widget_id"] for item in snap["validations_needing_scientist_review"]] == ["validation"]
    assert snap["scientist_reviews_needing_lab_lead_approval"] == []


def test_gate_requests_reject_wrong_widget_types():
    widgets = [
        _w("setup", "Note", title="[EXP:Setup v001]"),
        _w("validation", "Browser", title="[EXP:Validation] proceed"),
        _w("review", "Browser", title="[EXP:Scientist Review]"),
        _conn("sv", "setup", "validation"),
        _conn("vr", "validation", "review"),
    ]
    snap = scan_workflow(_index(widgets), M)
    assert snap["scientist_review_markers"] == []
    assert [item["widget_id"] for item in snap["validations_needing_scientist_review"]] == ["validation"]


def test_free_text_and_authorship_never_authorize_gate_requests():
    note = _w("note", "Note", title="Scientist approved this", text="Proceed to wet lab")
    note.update({"author": "scientist", "role": "lab-lead", "decision": "approve"})
    widgets = [
        _w("setup", "Note", title="[EXP:Setup v001]"),
        _w("validation", "Browser", title="[EXP:Validation] proceed"),
        note,
        _conn("sv", "setup", "validation"),
        _conn("vn", "validation", "note"),
    ]
    snap = scan_workflow(_index(widgets), M)
    assert snap["scientist_review_markers"] == []
    assert [item["widget_id"] for item in snap["validations_needing_scientist_review"]] == ["validation"]
