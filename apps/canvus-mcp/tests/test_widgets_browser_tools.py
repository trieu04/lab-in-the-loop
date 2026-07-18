"""Tests for the Browser widget MCP tools: ``create_browser``/``update_browser``.

Uses a minimal fake FastMCP + fake Canvus SDK client so the registered tool
coroutines can be exercised directly, without a running MCP server or network
access (mirrors the dependency-free style of ``test_experiments.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from canvus_mcp.tools import widgets as widgets_module


@dataclass
class _FakeBrowserModel:
    """Stands in for the SDK's pydantic ``Browser`` model."""

    id: str
    payload: dict[str, Any]

    def model_dump(self) -> dict[str, Any]:
        return {"id": self.id, "widget_type": "Browser", **self.payload}


@dataclass
class _FakeBrowsersResource:
    create_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    update_calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    async def create(self, canvas_id: str, payload: dict[str, Any]) -> _FakeBrowserModel:
        self.create_calls.append((canvas_id, payload))
        return _FakeBrowserModel(id="b-new", payload=payload)

    async def update(
        self, canvas_id: str, browser_id: str, payload: dict[str, Any]
    ) -> _FakeBrowserModel:
        self.update_calls.append((canvas_id, browser_id, payload))
        return _FakeBrowserModel(id=browser_id, payload=payload)


@dataclass
class _FakeWidgetsResource:
    browsers: _FakeBrowsersResource = field(default_factory=_FakeBrowsersResource)


@dataclass
class _FakeClient:
    widgets: _FakeWidgetsResource = field(default_factory=_FakeWidgetsResource)


class _FakeMCP:
    """Stand-in for :class:`mcp.server.fastmcp.FastMCP`.

    The real ``FastMCP.tool()`` decorator registers the function with the
    server and returns it unchanged; this fake mirrors only that (returning
    ``fn`` unchanged) so tests can call the registered coroutine directly.
    """

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any):
        def decorator(fn: Any) -> Any:
            self.tools[fn.__name__] = fn
            return fn

        return decorator


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> _FakeClient:
    client = _FakeClient()
    monkeypatch.setattr(widgets_module, "get_client", lambda: client)
    return client


@pytest.fixture
def tools(fake_client: _FakeClient) -> dict[str, Any]:
    mcp = _FakeMCP()
    widgets_module.register(mcp)
    return mcp.tools


# ---- create_browser --------------------------------------------------------


async def test_create_browser_default_payload_shape_is_unchanged(
    tools: dict[str, Any], fake_client: _FakeClient
):
    """Existing callers (canvas_id, url, x, y, width, height only) must keep
    getting the exact same payload shape -- no title/transparent_mode keys."""
    await tools["create_browser"]("canvas1", "https://example.test/a", x=1.0, y=2.0)
    canvas_id, payload = fake_client.widgets.browsers.create_calls[0]
    assert canvas_id == "canvas1"
    assert payload == {"url": "https://example.test/a", "location": {"x": 1.0, "y": 2.0}}
    assert "title" not in payload
    assert "transparent_mode" not in payload


async def test_create_browser_with_title_and_transparent_mode(
    tools: dict[str, Any], fake_client: _FakeClient
):
    await tools["create_browser"](
        "canvas1",
        "https://example.test/artifact/abc",
        x=3.0,
        y=4.0,
        width=640.0,
        height=480.0,
        title="[EXP:Setup v001] A+B",
        transparent_mode=True,
    )
    _canvas_id, payload = fake_client.widgets.browsers.create_calls[0]
    assert payload["title"] == "[EXP:Setup v001] A+B"
    assert payload["transparent_mode"] is True
    assert payload["size"] == {"width": 640.0, "height": 480.0}


async def test_create_browser_returns_dumped_sdk_result(
    tools: dict[str, Any], fake_client: _FakeClient
):
    result = await tools["create_browser"]("canvas1", "https://example.test/x", title="t")
    assert result["id"] == "b-new"
    assert result["title"] == "t"


# ---- update_browser ---------------------------------------------------------


async def test_update_browser_requires_at_least_one_field(
    tools: dict[str, Any], fake_client: _FakeClient
):
    with pytest.raises(ValueError, match="at least one"):
        await tools["update_browser"]("canvas1", "b1")
    assert fake_client.widgets.browsers.update_calls == []


async def test_update_browser_url_title_and_transparent_mode(
    tools: dict[str, Any], fake_client: _FakeClient
):
    await tools["update_browser"](
        "canvas1",
        "b1",
        url="https://example.test/new",
        title="[EXP:Result v002]",
        transparent_mode=False,
    )
    canvas_id, browser_id, payload = fake_client.widgets.browsers.update_calls[0]
    assert canvas_id == "canvas1"
    assert browser_id == "b1"
    assert payload == {
        "url": "https://example.test/new",
        "title": "[EXP:Result v002]",
        "transparent_mode": False,
    }


async def test_update_browser_position_and_size_repair(
    tools: dict[str, Any], fake_client: _FakeClient
):
    await tools["update_browser"]("canvas1", "b1", x=10.0, y=20.0, width=800.0, height=600.0)
    _canvas_id, _browser_id, payload = fake_client.widgets.browsers.update_calls[0]
    assert payload == {
        "location": {"x": 10.0, "y": 20.0},
        "size": {"width": 800.0, "height": 600.0},
    }


async def test_update_browser_ignores_incomplete_position_pair(
    tools: dict[str, Any], fake_client: _FakeClient
):
    """``x`` without ``y`` (or vice versa) does not produce a partial
    ``location`` -- mirrors the width/height pairing idiom used elsewhere in
    this module (e.g. ``create_note``)."""
    await tools["update_browser"]("canvas1", "b1", url="https://example.test/z", x=5.0)
    _canvas_id, _browser_id, payload = fake_client.widgets.browsers.update_calls[0]
    assert "location" not in payload
    assert payload == {"url": "https://example.test/z"}


async def test_update_browser_returns_dumped_sdk_result(
    tools: dict[str, Any], fake_client: _FakeClient
):
    result = await tools["update_browser"]("canvas1", "b1", title="[EXP:Closed] after v001")
    assert result["id"] == "b1"
    assert result["title"] == "[EXP:Closed] after v001"
