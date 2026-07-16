"""Real localhost streamable-HTTP authorization proof for guarded MCP tools."""

from __future__ import annotations

import asyncio
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import uvicorn
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import SecretStr

from canvus_mcp import server
from canvus_mcp.config import Settings
from canvus_mcp.ingestion_types import UnitSpec
from canvus_mcp.tools import widgets


@dataclass
class _Notes:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def create(self, canvas_id: str, payload: dict[str, Any]) -> dict[str, str]:
        self.calls.append((canvas_id, payload))
        return {"id": "note"}


@dataclass
class _Client:
    widgets: Any = field(default_factory=lambda: type("Widgets", (), {"notes": _Notes()})())


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        api_url="https://canvus.example/api/v1", api_key="sdk-secret",
        mcp_ingestion_db_path=str(tmp_path / "ingestion.db"), mcp_ingestion_cache_dir=str(tmp_path / "cache"),
        mcp_reader_token=SecretStr("reader"), mcp_trusted_service_token=SecretStr("service"),
        mcp_reader_canvases=["canvas-a"], mcp_trusted_service_canvases=["canvas-a"],
    )


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@asynccontextmanager
async def _server(mcp, port: int):
    instance = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"))
    task = asyncio.create_task(instance.serve())
    for _ in range(100):
        if instance.started:
            break
        await asyncio.sleep(0.01)
    try:
        assert instance.started
        yield f"http://127.0.0.1:{port}/mcp"
    finally:
        instance.should_exit = True
        await asyncio.wait_for(task, timeout=3)


async def _calls(url: str, headers: dict[str, str], calls: list[tuple[str, dict[str, Any]]]):
    async with streamablehttp_client(url, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return [await session.call_tool(name, arguments) for name, arguments in calls]


async def test_http_missing_bearer_denies_before_side_effect(monkeypatch, tmp_path: Path) -> None:
    client = _Client()
    monkeypatch.setattr(server, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(widgets, "get_client", lambda: client)
    async with _server(server.build_server(), _port()) as url:
        [missing] = await _calls(url, {}, [("create_note", {"canvas_id": "canvas-a", "text": "x"})])
    assert missing.isError and client.widgets.notes.calls == []


async def test_http_invalid_bearer_denies_before_side_effect(monkeypatch, tmp_path: Path) -> None:
    client = _Client()
    monkeypatch.setattr(server, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(widgets, "get_client", lambda: client)
    async with _server(server.build_server(), _port()) as url:
        [invalid] = await _calls(url, {"Authorization": "Bearer wrong"}, [("create_note", {"canvas_id": "canvas-a", "text": "x"})])
    assert invalid.isError and client.widgets.notes.calls == []


async def test_http_valid_bearer_allows_scoped_read_and_mutation(monkeypatch, tmp_path: Path) -> None:
    client = _Client()
    monkeypatch.setattr(server, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(widgets, "get_client", lambda: client)
    mcp = server.build_server()
    store = mcp.ingestion_runtime.store
    sha = "a" * 64
    store.upsert_asset(sha, size_bytes=1, mime_type="text/plain")
    store.record_source(canvas_id="canvas-a", source_ref="note:source", asset_sha256=sha)
    job, _ = store.create_job(sha, "v1", "text/plain", [UnitSpec("whole", 0)])
    async with _server(mcp, _port()) as url:
        valid, scoped_read = await _calls(url, {"Authorization": "Bearer service"}, [
            ("create_note", {"canvas_id": "canvas-a", "text": "x"}),
            ("get_ingestion_status", {"canvas_id": "canvas-a", "job_id": job.id}),
        ])
    assert not valid.isError and not scoped_read.isError
    assert client.widgets.notes.calls == [("canvas-a", {"text": "x", "location": {"x": 0.0, "y": 0.0}})]
