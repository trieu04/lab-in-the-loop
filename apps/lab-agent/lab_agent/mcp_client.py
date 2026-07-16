"""Thin, authenticated async client for the canvus-mcp streamable-HTTP server."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import SecretStr

from lab_agent.adapters.base import ToolSpec
from lab_agent.result_safety import fixed_error_json

_DEFAULT_RESULT_MAX_BYTES = 32 * 1024
_DEFAULT_CONTENT_ITEMS = 256


class MCPClient:
    """Live MCP session that never retains raw credentials or oversized results."""

    def __init__(
        self,
        url: str,
        bearer_token: SecretStr | None = None,
        *,
        result_max_bytes: int = _DEFAULT_RESULT_MAX_BYTES,
        max_content_items: int = _DEFAULT_CONTENT_ITEMS,
    ) -> None:
        if result_max_bytes < 1 or max_content_items < 1:
            raise ValueError("MCP result limits must be positive")
        self._url = url
        self._bearer_token = bearer_token
        self._result_max_bytes = result_max_bytes
        self._max_content_items = max_content_items
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> MCPClient:
        self._stack = AsyncExitStack()
        try:
            headers = self._authorization_headers()
            transport = streamablehttp_client(self._url, headers=headers)
            read, write, _ = await self._stack.enter_async_context(transport)
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
            return self
        except BaseException:
            await self._stack.aclose()
            self._stack = None
            self._session = None
            raise

    async def __aexit__(self, *exc: object) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    def _authorization_headers(self) -> dict[str, str] | None:
        if self._bearer_token is None:
            return None
        return {"Authorization": f"Bearer {self._bearer_token.get_secret_value()}"}

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
                name=tool.name,
                description=tool.description or "",
                parameters=tool.inputSchema or {"type": "object", "properties": {}},
            )
            for tool in result.tools
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Call a tool, aborting before aggregation exceeds configured bounds."""
        try:
            result = await self.session.call_tool(name, arguments)
            if getattr(result, "isError", False):
                return fixed_error_json()
            return self._bounded_text_content(result.content)
        except Exception:  # Third-party transport errors have no safe common type.
            return fixed_error_json()

    def _bounded_text_content(self, content: object) -> str:
        if not isinstance(content, Iterable):
            return fixed_error_json()
        blocks: Iterable[object] = content
        parts: list[str] = []
        total_bytes = 0
        for count, block in enumerate(blocks, start=1):
            if count > self._max_content_items:
                return fixed_error_json()
            text = getattr(block, "text", None)
            if text is None:
                continue
            if not isinstance(text, str):
                return fixed_error_json()
            addition = len(text.encode("utf-8")) + (1 if parts else 0)
            if total_bytes + addition > self._result_max_bytes:
                return fixed_error_json()
            parts.append(text)
            total_bytes += addition
        return "\n".join(parts)


__all__ = ["MCPClient"]
