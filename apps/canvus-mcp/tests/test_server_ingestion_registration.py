"""Server wiring creates one authorized ingestion runtime without a worker."""

from __future__ import annotations

from pathlib import Path

from canvus_mcp import server
from canvus_mcp.config import Settings


def test_server_registers_ingestion_tools_hides_context_and_does_not_start_worker(
    monkeypatch, tmp_path: Path
) -> None:
    cfg = Settings(
        api_url="https://canvus.example/api/v1",
        api_key="sdk-secret",
        mcp_ingestion_db_path=str(tmp_path / "ingestion.db"),
        mcp_ingestion_cache_dir=str(tmp_path / "cache"),
        mcp_reader_token="reader",
        mcp_reader_canvases=["canvas-a"],
    )
    monkeypatch.setattr(server, "get_settings", lambda: cfg)
    mcp = server.build_server()
    try:
        names = set(mcp._tool_manager._tools)
        assert {"enqueue_ingestion", "get_ingestion_status", "read_ingestion_chunks", "retry_ingestion", "cancel_ingestion"} <= names
        assert "ctx" not in mcp._tool_manager._tools["get_ingestion_status"].parameters["properties"]
        assert cfg.mcp_host == "127.0.0.1"
        assert not hasattr(mcp, "ingestion_worker")
    finally:
        mcp.ingestion_runtime.store.close()
