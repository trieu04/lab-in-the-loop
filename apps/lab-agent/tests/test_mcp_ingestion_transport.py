"""Regressions for authenticated, secret-safe MCP transport."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from pydantic import SecretStr

from lab_agent.config import Settings
from lab_agent.mcp_client import MCPClient


def test_mcp_bearer_token_env_is_blank_safe_and_masked(monkeypatch) -> None:
    token = "reader-secret-value"
    monkeypatch.setenv("LAB_AGENT_MCP_BEARER_TOKEN", token)
    settings = Settings()

    assert settings.mcp_bearer_token is not None
    assert settings.mcp_bearer_token.get_secret_value() == token
    rendered = " ".join(
        (repr(settings), str(settings), settings.model_dump_json(), repr(settings.model_dump()))
    )
    assert token not in rendered

    monkeypatch.setenv("LAB_AGENT_MCP_BEARER_TOKEN", "   ")
    assert Settings().mcp_bearer_token is None


async def test_mcp_transport_only_constructs_bearer_header_when_configured(monkeypatch) -> None:
    import lab_agent.mcp_client as subject

    received: list[dict[str, str] | None] = []

    @asynccontextmanager
    async def transport(_url: str, *, headers: dict[str, str] | None = None):
        received.append(headers)
        yield object(), object(), lambda: None

    class Session:
        def __init__(self, _read: object, _write: object) -> None:
            pass

        async def __aenter__(self) -> Session:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            pass

        async def initialize(self) -> None:
            pass

    monkeypatch.setattr(subject, "streamablehttp_client", transport)
    monkeypatch.setattr(subject, "ClientSession", Session)

    async with MCPClient("https://mcp.test", SecretStr("reader-secret")):
        pass
    async with MCPClient("https://mcp.test"):
        pass

    assert received == [{"Authorization": "Bearer reader-secret"}, None]


async def test_mcp_server_error_is_fixed_and_never_reflects_raw_details() -> None:
    raw_error = "token=reader-secret path=/cache/private"
    client = MCPClient("https://mcp.test", SecretStr("reader-secret"))
    client._session = SimpleNamespace(  # type: ignore[assignment]
        call_tool=lambda _name, _arguments: _error_result(raw_error)
    )

    result = await client.call_tool("get_ingestion_status", {"job_id": 1})

    assert result == '{"error":"tool_result_unavailable","data_classification":"unknown"}'
    assert raw_error not in result
    assert "reader-secret" not in repr(client)


async def _error_result(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=True)
