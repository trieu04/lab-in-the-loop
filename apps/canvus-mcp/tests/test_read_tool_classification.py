"""Regressions: canvas-scoped read tools stamp operator data classification.

The model boundary in lab-agent fail-closes an unclassified read to ``unknown``,
which denies every later provider turn. These tools are the operator-authority
producer: each canvas-scoped read must stamp ``data_classification`` for its
canvas, and a missing widget must return a classified ``found: false`` rather
than raise (so a stray 404 cannot poison a run with ``unknown``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from canvus_sdk import NotFoundError

from canvus_mcp.config import Settings
from canvus_mcp.tools import connections as connections_tools
from canvus_mcp.tools import content as content_tools

CANVAS_ID = "canvas-classify"


class _FakeMCP:
    """Minimal FastMCP decorator that stores registered tool functions."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any):
        def register(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return register


@dataclass
class _Note:
    id: str
    text: str

    def model_dump(self) -> dict[str, Any]:
        return {"id": self.id, "text": self.text, "widget_type": "Note"}


@dataclass
class _FakeNotes:
    note: _Note | None

    async def get(self, _canvas_id: str, _note_id: str) -> _Note:
        if self.note is None:
            raise NotFoundError("missing note", status_code=404)
        return self.note


@dataclass
class _FakeWidgets:
    fixture: list[dict[str, Any]] = field(default_factory=list)
    note: _Note | None = None
    widget: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.notes = _FakeNotes(self.note)

    async def list(self, _canvas_id: str) -> list[dict[str, Any]]:
        return self.fixture

    async def get(self, _canvas_id: str, _widget_id: str) -> dict[str, Any]:
        if self.widget is None:
            raise NotFoundError("missing widget", status_code=404)
        return self.widget


@dataclass
class _FakeClient:
    widgets: _FakeWidgets


@dataclass
class _FakeDownloader:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def acquire(self, _canvas_id: str, _kind: str, _ref: str) -> dict[str, Any]:
        return {"path": "/out/file.pdf", "mime_type": "application/pdf", "size_bytes": 3}


def _settings() -> Settings:
    return Settings(api_url="https://canvus.example/api/v1", api_key="test-key")


def _patch_content(monkeypatch: pytest.MonkeyPatch, widgets: _FakeWidgets) -> None:
    monkeypatch.setattr(content_tools, "get_client", lambda: _FakeClient(widgets))
    monkeypatch.setattr(content_tools, "get_settings", _settings)
    monkeypatch.setattr(content_tools, "CanvusContentDownloader", _FakeDownloader)


def _patch_connections(monkeypatch: pytest.MonkeyPatch, widgets: _FakeWidgets) -> None:
    monkeypatch.setattr(connections_tools, "get_client", lambda: _FakeClient(widgets))
    monkeypatch.setattr(connections_tools, "get_settings", _settings)


def _register(module: Any, monkeypatch: pytest.MonkeyPatch, widgets: _FakeWidgets):
    calls: list[str] = []

    def resolve(canvas_id: str) -> str:
        calls.append(canvas_id)
        return "internal"

    (_patch_content if module is content_tools else _patch_connections)(monkeypatch, widgets)
    mcp = _FakeMCP()
    module.register(mcp, classification_for_canvas=resolve)
    return mcp, calls


async def test_get_note_success_stamps_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, calls = _register(content_tools, monkeypatch, _FakeWidgets(note=_Note("n1", "hi")))
    result = await mcp.tools["get_note"](CANVAS_ID, "n1")
    assert result["data_classification"] == "internal"
    assert result["id"] == "n1" and result["text"] == "hi"
    assert calls == [CANVAS_ID]


async def test_get_note_not_found_returns_classified_found_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mcp, _ = _register(content_tools, monkeypatch, _FakeWidgets(note=None))
    result = await mcp.tools["get_note"](CANVAS_ID, "missing")
    assert result == {
        "canvas_id": CANVAS_ID,
        "note_id": "missing",
        "found": False,
        "data_classification": "internal",
    }


async def test_get_widget_success_and_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, _ = _register(
        content_tools, monkeypatch, _FakeWidgets(widget={"id": "w1", "widget_type": "Image"})
    )
    ok = await mcp.tools["get_widget"](CANVAS_ID, "w1")
    assert ok["data_classification"] == "internal" and ok["id"] == "w1"

    mcp, _ = _register(content_tools, monkeypatch, _FakeWidgets(widget=None))
    missing = await mcp.tools["get_widget"](CANVAS_ID, "gone")
    assert missing["found"] is False and missing["data_classification"] == "internal"


async def test_download_pdf_stamps_classification(monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, _ = _register(content_tools, monkeypatch, _FakeWidgets())
    result = await mcp.tools["download_pdf"](CANVAS_ID, "pdf1")
    assert result["data_classification"] == "internal"
    assert result["mime_type"] == "application/pdf"


async def test_check_widget_connections_stamps(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = [
        {"id": "w1", "widget_type": "Note", "title": "", "text": ""},
        {"id": "w2", "widget_type": "Note", "title": "", "text": ""},
        {"id": "c1", "widget_type": "Connector", "src": {"id": "w1"}, "dst": {"id": "w2"}},
    ]
    mcp, _ = _register(connections_tools, monkeypatch, _FakeWidgets(fixture=fixture))
    found = await mcp.tools["check_widget_connections"](CANVAS_ID, "w1")
    assert found["found"] is True and found["data_classification"] == "internal"

    missing = await mcp.tools["check_widget_connections"](CANVAS_ID, "nope")
    assert missing["found"] is False and missing["data_classification"] == "internal"


async def test_check_ragcluster_connections_stamps(monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = [{"id": "rag1", "widget_type": "Image", "title": "RAGCluster_x", "text": ""}]
    mcp, _ = _register(connections_tools, monkeypatch, _FakeWidgets(fixture=fixture))
    result = await mcp.tools["check_ragcluster_connections"](CANVAS_ID)
    assert result["data_classification"] == "internal"
    assert result["ragcluster_count"] == 1


async def test_unmapped_canvas_propagates_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_content(monkeypatch, _FakeWidgets(note=_Note("n1", "hi")))
    mcp = _FakeMCP()
    content_tools.register(mcp, classification_for_canvas=lambda _c: "unknown")
    result = await mcp.tools["get_note"](CANVAS_ID, "n1")
    assert result["data_classification"] == "unknown"
