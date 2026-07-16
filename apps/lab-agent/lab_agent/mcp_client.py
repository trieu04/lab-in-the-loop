"""Thin async client for the canvus-mcp streamable-HTTP server.

Opens one :class:`mcp.ClientSession` for the lifetime of a run (via
``async with``) and exposes just what the agent needs: list the available
tools and call one by name. Tool results are flattened to text — the canvus-mcp
tools return JSON-serialisable dicts as text content.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from lab_agent.adapters.base import ToolSpec


class MCPClient:
    """Live MCP session against a canvus-mcp server."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> MCPClient:
        self._stack = AsyncExitStack()
        read, write, _ = await self._stack.enter_async_context(streamablehttp_client(self._url))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    @property
    def session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError("MCPClient used outside its async context")
        return self._session

    async def list_tools(self) -> list[ToolSpec]:
        """Return every tool the server exposes as a :class:`ToolSpec`."""
        result = await self.session.list_tools()
        return [
            ToolSpec(
                name=t.name,
                description=t.description or "",
                parameters=t.inputSchema or {"type": "object", "properties": {}},
            )
            for t in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool and return its result as a text string."""
        result = await self.session.call_tool(name, arguments)
        parts: list[str] = []
        for block in result.content:
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(text)
        payload = "\n".join(parts)
        if getattr(result, "isError", False):
            return json.dumps({"error": payload or "tool call failed"})
        return payload


__all__ = ["MCPClient"]
