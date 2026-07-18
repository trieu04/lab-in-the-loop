"""The bounded agentic tool-use loop.

Lets the model call read-only MCP tools to ground itself, appending each
assistant turn and tool result to the transcript, until the model stops
requesting tools or the ``max_steps`` cap is hit (the runaway-loop guard of
UC §13.5). Canvas mutations are never performed here — only reads.
"""

from __future__ import annotations

from typing import Any

from lab_agent.adapters.base import Message, ModelAdapter, ToolSpec
from lab_agent.evidence import EvidenceLedger
from lab_agent.mcp_client import MCPClient
from lab_agent.tool_bridge import execute_tool_calls


async def run_tool_loop(
    adapter: ModelAdapter,
    mcp: MCPClient,
    messages: list[Message],
    tools: list[ToolSpec],
    max_steps: int,
    ledger: EvidenceLedger | None = None,
) -> list[Message]:
    """Drive read-tool grounding. Extends and returns ``messages`` in place.

    ``ledger``, if given, records every successful allowlisted read so later
    citations can be validated against it (see :mod:`lab_agent.grounding`).
    """
    for _ in range(max_steps):
        resp = await adapter.generate(messages, tools=tools)
        messages.append(
            {
                "role": "assistant",
                "content": resp.text,
                "tool_calls": [c.as_message_dict() for c in resp.tool_calls],
            }
        )
        if not resp.tool_calls:
            break
        messages.extend(await execute_tool_calls(mcp, resp.tool_calls, ledger))
    return messages


async def emit_structured(
    adapter: ModelAdapter,
    messages: list[Message],
    response_schema: dict[str, Any],
    schema_name: str,
) -> dict[str, Any]:
    """Force one structured emit from the model given the current transcript."""
    resp = await adapter.generate(
        messages,
        response_schema=response_schema,
        schema_name=schema_name,
    )
    return resp.parsed or {}


__all__ = ["emit_structured", "run_tool_loop"]
