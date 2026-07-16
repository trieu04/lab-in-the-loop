"""Widget mutation authorization occurs before local or network side effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from canvus_mcp.access_control import AccessDenied, AccessPolicy, Role
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.tools import widgets


class _MCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any):
        def decorate(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return decorate


@dataclass
class _Request:
    headers: list[tuple[str, str]]


@dataclass
class _RequestContext:
    request: _Request


@dataclass
class _Context:
    request_context: _RequestContext


def _ctx(token: str) -> _Context:
    return _Context(_RequestContext(_Request([("Authorization", f"Bearer {token}")])))


@dataclass
class _Resource:
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    async def create(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(args)
        return {"id": "created"}

    async def update(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(args)
        return {"id": "updated"}

    async def upload(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(args)
        return {"id": "uploaded"}


@dataclass
class _Widgets:
    notes: _Resource = field(default_factory=_Resource)
    browsers: _Resource = field(default_factory=_Resource)
    images: _Resource = field(default_factory=_Resource)
    connectors: _Resource = field(default_factory=_Resource)


@dataclass
class _Client:
    widgets: _Widgets = field(default_factory=_Widgets)


@pytest.fixture
def authorized_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], _Client, IngestionStore]:
    store = IngestionStore(tmp_path / "ingestion.db")
    policy = AccessPolicy(
        store=store,
        reader_token=SecretStr("reader"),
        trusted_service_token=SecretStr("service"),
        operator_token=SecretStr("operator"),
        reader_canvases=("canvas-a",),
        trusted_service_canvases=("canvas-a",),
        operator_canvases=("canvas-a",),
        stdio_role=Role.READER,
        stdio_canvases=("canvas-a",),
    )
    client = _Client()
    monkeypatch.setattr(widgets, "get_client", lambda: client)
    mcp = _MCP()
    widgets.register(mcp, policy=policy)
    try:
        yield mcp.tools, client, store
    finally:
        store.close()


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("create_note", ("canvas-a", "text")),
        ("create_browser", ("canvas-a", "https://example.test")),
        ("update_browser", ("canvas-a", "browser-1", "https://example.test")),
        ("create_image", ("canvas-a", "/not/read-before-auth")),
        ("create_connector", ("canvas-a", "src", "dst")),
    ],
)
async def test_widget_mutations_deny_before_side_effects(
    authorized_tools: tuple[dict[str, Any], _Client, IngestionStore], name: str, args: tuple[Any, ...]
) -> None:
    tools, client, _ = authorized_tools
    with pytest.raises(AccessDenied, match="access_denied"):
        await tools[name](*args, ctx=_ctx("reader"))
    assert not client.widgets.notes.calls
    assert not client.widgets.browsers.calls
    assert not client.widgets.images.calls
    assert not client.widgets.connectors.calls


async def test_trusted_service_can_mutate_scoped_canvas(
    authorized_tools: tuple[dict[str, Any], _Client, IngestionStore]
) -> None:
    tools, client, _ = authorized_tools
    await tools["create_note"]("canvas-a", "text", ctx=_ctx("service"))
    assert client.widgets.notes.calls == [("canvas-a", {"text": "text", "location": {"x": 0.0, "y": 0.0}})]
