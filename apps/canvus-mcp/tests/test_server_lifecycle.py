"""FastMCP lifespan deterministically owns runtime resources."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from canvus_mcp import server
from canvus_mcp.config import Settings


def test_build_server_closes_runtime_when_tool_registration_fails(monkeypatch, tmp_path: Path) -> None:
    cfg = Settings(api_url="https://canvus.example/api/v1", api_key="sdk-secret")
    closed: list[bool] = []
    runtime = SimpleNamespace(policy=object(), pipeline=object(), close=lambda: closed.append(True))

    def fail_registration(*_args, **_kwargs) -> None:
        raise RuntimeError("registration failed")

    monkeypatch.setattr(server, "get_settings", lambda: cfg)
    monkeypatch.setattr(server, "build_ingestion_runtime", lambda _cfg: runtime)
    monkeypatch.setattr(server, "register_all", fail_registration)
    with pytest.raises(RuntimeError, match="registration failed"):
        server.build_server()
    assert closed == [True]


async def test_lifespan_closes_store_and_client_for_all_transports(monkeypatch, tmp_path: Path) -> None:
    cfg = Settings(
        api_url="https://canvus.example/api/v1", api_key="sdk-secret",
        mcp_ingestion_db_path=str(tmp_path / "ingestion.db"),
        mcp_ingestion_cache_dir=str(tmp_path / "cache"),
    )
    closed: list[str] = []

    async def close_client() -> None:
        closed.append("client")

    monkeypatch.setattr(server, "get_settings", lambda: cfg)
    monkeypatch.setattr(server, "close_client", close_client)
    mcp = server.build_server()
    monkeypatch.setattr(mcp.ingestion_runtime.pipeline.cache, "close", lambda: closed.append("cache"))
    monkeypatch.setattr(mcp.ingestion_runtime.store, "close", lambda: closed.append("store"))
    async with mcp.settings.lifespan(mcp):
        assert not hasattr(mcp, "ingestion_worker")
    assert closed == ["cache", "store", "client"]
