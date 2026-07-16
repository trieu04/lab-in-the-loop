"""MCP transport aggregation limits before the model-result boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient


def test_settings_exposes_validated_mcp_aggregate_limits() -> None:
    settings = Settings(mcp_result_max_bytes=4096, mcp_result_max_string_chars=4096, mcp_result_max_items=12)

    assert settings.mcp_result_max_bytes == 4096
    assert settings.mcp_result_max_items == 12


def test_settings_rejects_string_bounds_larger_than_aggregate_bound() -> None:
    with pytest.raises(ValueError, match="mcp_result_max_string_chars"):
        Settings(mcp_result_max_bytes=4096, mcp_result_max_string_chars=8192)


async def test_mcp_call_aborts_before_joining_over_byte_limit() -> None:
    client = MCPClient("https://mcp.test", result_max_bytes=8)
    client._session = SimpleNamespace(  # type: ignore[assignment]
        call_tool=lambda _name, _arguments: _result(["four", "more"])
    )

    result = await client.call_tool("read_ingestion_chunks", {})

    assert result == '{"error":"tool_result_unavailable","data_classification":"unknown"}'


async def test_mcp_call_bounds_content_item_count() -> None:
    client = MCPClient("https://mcp.test", max_content_items=1)
    client._session = SimpleNamespace(  # type: ignore[assignment]
        call_tool=lambda _name, _arguments: _result(["one", "two"])
    )

    result = await client.call_tool("read_ingestion_chunks", {})

    assert result == '{"error":"tool_result_unavailable","data_classification":"unknown"}'


async def _result(texts: list[str]) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(text=text) for text in texts], isError=False)
