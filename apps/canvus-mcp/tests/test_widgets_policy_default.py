"""Widget tools require an explicit policy when embedded directly."""

from __future__ import annotations

from typing import Any

import pytest

from canvus_mcp.access_control import AccessDenied
from canvus_mcp.tools import widgets


class _MCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any):
        def decorator(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return decorator


async def test_mutations_fail_closed_without_explicit_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(widgets, "get_client", lambda: pytest.fail("network side effect"))
    mcp = _MCP()
    widgets.register(mcp)
    with pytest.raises(AccessDenied, match="access_denied"):
        await mcp.tools["create_note"]("canvas-a", "text")
