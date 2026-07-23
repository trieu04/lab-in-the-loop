"""Decision-neutral in-silico validation marker contracts."""

from __future__ import annotations

import pytest

from canvus_mcp.config import Settings
from canvus_mcp.experiments import ExpMarkers, scan_workflow
from canvus_mcp.ragcluster import ConnectorIndex


def _widget(widget_id: str, widget_type: str, title: str) -> dict[str, str]:
    return {"id": widget_id, "widget_type": widget_type, "title": title}


def _connector(widget_id: str, source: str, destination: str) -> dict[str, object]:
    return {
        "id": widget_id,
        "widget_type": "Connector",
        "src": {"id": source},
        "dst": {"id": destination},
    }


@pytest.mark.parametrize("decision", ["proceed", "revise", "reject"])
def test_decision_neutral_validation_marker_blocks_pending_setup(decision: str) -> None:
    widgets = [
        _widget("setup", "Browser", "[EXP:Setup v001] proposal"),
        _widget("validation", "Browser", f"[EXP:Validation] {decision}"),
        _connector("setup-validation", "setup", "validation"),
    ]

    scan = scan_workflow(ConnectorIndex.build(widgets, ExpMarkers().ragcluster), ExpMarkers())

    assert scan["setups_needing_validation"] == []
    assert [item["widget_id"] for item in scan["validations"]] == ["validation"]


def test_validation_marker_default_is_non_authorizing_and_decision_neutral() -> None:
    settings = Settings(api_url="https://canvus.test", api_key="test-key")

    assert settings.mcp_exp_validation_marker == "[EXP:Validation]"
    assert ExpMarkers().validation == "[EXP:Validation]"
