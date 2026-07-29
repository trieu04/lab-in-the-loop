"""Deterministic in-process Canvus server for cross-app integration tests.

The production lab-agent speaks MCP.  This test server preserves that boundary
by exposing a per-canvas ``FakeMCP`` client whose workflow scan is recomputed by
the real canvus-mcp detector through ``tests.canvus_detector_bridge``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tests import canvus_detector_bridge as detector
from tests.fakes import FakeMCP


@dataclass
class FakeCanvusCanvas:
    """One isolated canvas exposed through the fake MCP contract."""

    canvas_id: str
    mcp: FakeMCP = field(default_factory=lambda: FakeMCP(live=True))

    def seed_experiment_loop(self) -> None:
        """Seed the minimal topology that Canvus classifies as a loop."""
        self.mcp.seed_widget("rag", "Image", title="RAGCluster_test")
        self.mcp.seed_widget("idea", "Note", text="{idea: test experiment}")
        self.mcp.seed_widget("setup", "Browser", title="[EXP:Setup v001]")
        self.mcp.seed_widget("robot", "Note", title="Robot_arm")
        self.mcp.seed_widget("result", "Browser", title="[EXP:Result v001]")
        for src, dst in (
            ("rag", "idea"),
            ("idea", "setup"),
            ("setup", "robot"),
            ("robot", "result"),
            ("result", "setup"),
        ):
            self.mcp.seed_connector(src, dst)

    def scan(self) -> dict[str, object]:
        """Return a live graph snapshot from the real Canvus detector."""
        return detector.live_scan(self.mcp.notes, self.mcp.connectors)

    def browser_ids(self) -> set[str]:
        return {
            widget_id
            for widget_id, widget in self.mcp.notes.items()
            if widget["widget_type"] == "Browser"
        }


class FakeCanvusServer:
    """Own independent fake canvases without a second test harness."""

    def __init__(self) -> None:
        self._canvases: dict[str, FakeCanvusCanvas] = {}

    def canvas(self, canvas_id: str) -> FakeCanvusCanvas:
        if not canvas_id:
            raise ValueError("canvas_id is required")
        return self._canvases.setdefault(canvas_id, FakeCanvusCanvas(canvas_id))


__all__ = ["FakeCanvusCanvas", "FakeCanvusServer"]
