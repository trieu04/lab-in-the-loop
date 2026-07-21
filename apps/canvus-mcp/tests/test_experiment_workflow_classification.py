"""Registered experiment-tool regressions for item-level data classification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from canvus_mcp import experiments as core
from canvus_mcp.config import Settings
from canvus_mcp.ragcluster import ConnectorIndex
from canvus_mcp.tools import experiments as tools

CANVAS_ID = "canvas-regression"


def _widget(wid: str, title: str = "", text: str = "", widget_type: str = "Note") -> dict[str, Any]:
    return {"id": wid, "widget_type": widget_type, "title": title, "text": text}


def _connector(cid: str, src: str, dst: str) -> dict[str, Any]:
    return {"id": cid, "widget_type": "Connector", "src": {"id": src}, "dst": {"id": dst}}


def _workflow_widgets() -> list[dict[str, Any]]:
    return [
        _widget("rag1", "RAGCluster_fixture", widget_type="Image"),
        _widget("idea-pending", text="{idea: pending experiment}"),
        _widget("setup-pending", "[EXP:Setup v001] pending"),
        _widget("robot-pending", "Robot_pending"),
        _widget("setup-loop", "[EXP:Setup v001] loop"),
        _widget("robot-loop", "Robot_loop"),
        _widget("result-loop", "[EXP:Result v001] loop"),
        _connector("idea-source", "rag1", "idea-pending"),
        _connector("setup-run", "setup-pending", "robot-pending"),
        _connector("loop-setup-robot", "setup-loop", "robot-loop"),
        _connector("loop-robot-result", "robot-loop", "result-loop"),
        _connector("loop-back", "result-loop", "setup-loop"),
    ]


@dataclass
class _FakeWidgets:
    fixture: list[dict[str, Any]]
    list_calls: list[str] = field(default_factory=list)

    async def list(self, canvas_id: str) -> list[dict[str, Any]]:
        self.list_calls.append(canvas_id)
        return self.fixture


@dataclass
class _FakeClient:
    widgets: _FakeWidgets


class _FakeMCP:
    """Minimal repository-style FastMCP decorator that stores registered tools."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any):
        def register(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return register


@pytest.mark.parametrize("classification", ["internal", "unknown"])
async def test_registered_scan_stamps_all_actionable_items(
    monkeypatch: pytest.MonkeyPatch, classification: str
) -> None:
    widgets = _FakeWidgets(_workflow_widgets())
    monkeypatch.setattr(tools, "get_client", lambda: _FakeClient(widgets))
    monkeypatch.setattr(
        tools,
        "get_settings",
        lambda: Settings(api_url="https://canvus.example/api/v1", api_key="test-key"),
    )
    resolver_calls: list[str] = []

    def resolve(canvas_id: str) -> str:
        resolver_calls.append(canvas_id)
        return classification

    mcp = _FakeMCP()
    tools.register(mcp, classification_for_canvas=resolve)
    result = await mcp.tools["scan_experiment_workflow"](CANVAS_ID)

    assert widgets.list_calls == [CANVAS_ID]
    assert resolver_calls == [CANVAS_ID]
    assert result["canvas_id"] == CANVAS_ID
    assert result["ideas_needing_setup"]
    assert result["setups_needing_run"]
    assert result["loops"]

    expected_original = {
        "ideas_needing_setup": {
            "widget_id": "idea-pending",
            "widget_type": "Note",
            "title": "",
            "ragcluster_id": "rag1",
        },
        "setups_needing_run": {
            "widget_id": "setup-pending",
            "widget_type": "Note",
            "title": "[EXP:Setup v001] pending",
            "robot_id": "robot-pending",
        },
        "loops": {
            "loop_connector_id": "loop-back",
            "setup_id": "setup-loop",
            "result_id": "result-loop",
            "robot_id": "robot-loop",
            "idea_id": "",
            "ragcluster_id": "",
            "round": 1,
        },
    }
    for bucket, original in expected_original.items():
        assert len(result[bucket]) == 1
        item = result[bucket][0]
        assert item["data_classification"] == classification
        assert {key: item[key] for key in original} == original

    raw = core.scan_workflow(ConnectorIndex.build(_workflow_widgets()), core.ExpMarkers())
    for bucket in (
        "ragclusters",
        "ideas",
        "setups",
        "results",
        "robots",
        "closeds",
        "needs_inputs",
    ):
        assert result[bucket] == raw[bucket]


def test_core_scanner_remains_policy_independent() -> None:
    snapshot = core.scan_workflow(ConnectorIndex.build(_workflow_widgets()), core.ExpMarkers())
    for bucket in ("ideas_needing_setup", "setups_needing_run", "loops"):
        assert snapshot[bucket]
        assert all("data_classification" not in item for item in snapshot[bucket])
