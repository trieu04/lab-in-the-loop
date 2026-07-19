"""Real FastMCP streamable-HTTP authentication integration coverage."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from pydantic import SecretStr
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse

from lab_agent.mcp_client import MCPClient


class _BearerGate(BaseHTTPMiddleware):
    expected = "Bearer integration-token"
    denied_headers: list[str | None] = []

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        header = request.headers.get("authorization")
        if header != self.expected:
            self.denied_headers.append(header)
            return PlainTextResponse("denied", status_code=401)
        return await call_next(request)


@asynccontextmanager
async def _server() -> AsyncIterator[tuple[str, list[str]]]:
    observed_headers: list[str] = []
    mcp = FastMCP("authenticated-test")

    @mcp.tool()
    async def read_authorization(ctx: Context) -> str:
        header = ctx.request_context.request.headers.get("authorization", "")
        observed_headers.append(header)
        return header

    _BearerGate.denied_headers = []
    app = mcp.streamable_http_app()
    app.add_middleware(_BearerGate)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    try:
        yield f"http://127.0.0.1:{port}/mcp", observed_headers
    finally:
        server.should_exit = True
        await task


async def test_mcp_client_forwards_bearer_to_fastmcp_tool_context() -> None:
    async with _server() as (url, observed_headers):
        async with MCPClient(url, SecretStr("integration-token")) as client:
            assert await client.call_tool("read_authorization", {}) == "Bearer integration-token"

    assert observed_headers == ["Bearer integration-token"]


@pytest.mark.parametrize("token", [None, "wrong-token"])
async def test_fastmcp_denies_missing_or_invalid_bearer(token: str | None) -> None:
    async with _server() as (url, observed_headers):
        credential = SecretStr(token) if token is not None else None
        with pytest.raises(Exception):
            async with MCPClient(url, credential):
                pass

    assert observed_headers == []
    expected = None if token is None else f"Bearer {token}"
    assert _BearerGate.denied_headers == [expected]
