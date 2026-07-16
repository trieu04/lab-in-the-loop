"""Test-only bridge to canvus-mcp's real loop-detection logic.

canvus-mcp is a sibling app with its own venv/lockfile; the two apps are NOT
runtime-coupled (see ``scripts/check-workflow-contract-parity.py``, which
checks marker/contract alignment without importing either package). These
helpers are test-only: they let :class:`tests.fakes.FakeMCP` derive its scan
snapshot and connection report with the *same* code canvus-mcp runs in
production, instead of a static fixture that can silently drift from real
detector behaviour -- the exact gap that hid the loop-detection false-positive
regression. ``experiments.py``/``ragcluster.py`` are dependency-free (stdlib
only), so this path-based import needs none of canvus-mcp's own dependencies.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_Notes = dict[str, dict[str, Any]]
_Connectors = list[tuple[str, str]]


def _import_detector() -> tuple[Any, Any, Any, Any, Any]:
    root = Path(__file__).resolve().parents[2] / "canvus-mcp"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from canvus_mcp.experiments import ExpMarkers, scan_workflow
    from canvus_mcp.ragcluster import ConnectorIndex, connections_for_widget, is_ragcluster_widget

    return ExpMarkers, scan_workflow, ConnectorIndex, connections_for_widget, is_ragcluster_widget


def _widgets(notes: _Notes, connectors: _Connectors) -> list[dict[str, Any]]:
    """Flatten recorded notes + connectors into detector-ready widget dicts.

    Every note already carries id/widget_type/title/text (see
    ``FakeMCP.seed_widget``), so a shallow copy is a ready-made detector widget.
    Synthetic connector ids are stable across calls (index into the append-only
    connectors list), so a loop's ``loop_connector_id`` stays identical across
    repeated scans -- required for the durable ``workflow_attempts`` dedup
    (trigger ``loop:<connector_id>``) to hold when re-scanning.
    """
    widgets: list[dict[str, Any]] = [dict(w) for w in notes.values()]
    widgets += [
        {"id": f"conn{i}", "widget_type": "Connector", "src": {"id": src}, "dst": {"id": dst}}
        for i, (src, dst) in enumerate(connectors)
    ]
    return widgets


def live_scan(notes: _Notes, connectors: _Connectors) -> dict[str, Any]:
    """Recompute the ``scan_experiment_workflow`` snapshot from live canvas state."""
    exp_markers_cls, scan_workflow, connector_index_cls, _conn, _rag = _import_detector()
    markers = exp_markers_cls()
    index = connector_index_cls.build(_widgets(notes, connectors), markers.ragcluster)
    snapshot: dict[str, Any] = scan_workflow(index, markers)
    return snapshot


def check_widget_connections(notes: _Notes, connectors: _Connectors, widget_id: str, canvas_id: Any) -> dict[str, Any]:
    """Mirror canvus-mcp's real ``check_widget_connections`` tool (see
    ``canvus_mcp/tools/connections.py``) so ``canvas_probe.py``'s recovery
    probes exercise the exact response shape production returns. Always
    recomputed from current notes/connectors -- crash-recovery probing must
    see live canvas state."""
    exp_markers_cls, _scan, connector_index_cls, connections_for_widget, is_ragcluster_widget = _import_detector()
    markers = exp_markers_cls()
    index = connector_index_cls.build(_widgets(notes, connectors), markers.ragcluster)
    if widget_id not in index.widgets_by_id:
        return {"canvas_id": canvas_id, "widget_id": widget_id, "found": False}
    conn = connections_for_widget(index, widget_id)
    target = index.widgets_by_id[widget_id]
    return {
        "canvas_id": canvas_id,
        "widget_id": widget_id,
        "found": True,
        "is_ragcluster": is_ragcluster_widget(target, markers.ragcluster),
        "incoming_count": len(conn["incoming"]),
        "outgoing_count": len(conn["outgoing"]),
        "incoming": conn["incoming"],
        "outgoing": conn["outgoing"],
    }
