"""Bridge between MCP tools and the model's tool-use interface.

Two responsibilities:
1. Expose only the **read-only** canvus-mcp tools to the model, so it can
   ground itself but cannot mutate the canvas — all writes are orchestrator
   owned (plan: "agentic reads, orchestrated writes").
2. Execute the tool calls a model requested against the live MCP session and
   package the results as normalized ``tool`` messages for the transcript.
"""

from __future__ import annotations

import json
from typing import Any

from lab_agent.adapters.base import Message, ToolCall, ToolSpec
from lab_agent.evidence import EvidenceLedger
from lab_agent.mcp_client import MCPClient

# Read-only tools the model is allowed to drive during grounding.
READ_TOOLS: frozenset[str] = frozenset(
    {
        "scan_server",
        "list_canvases",
        "check_ragcluster_connections",
        "check_widget_connections",
        "get_note",
        "get_widget",
        "download_pdf",
    }
)


def _bare_name(name: str) -> str:
    """Strip any client namespacing (e.g. ``mcp__canvus__get_note`` -> ``get_note``)."""
    return name.rsplit("__", 1)[-1]


def select_read_tools(tools: list[ToolSpec]) -> list[ToolSpec]:
    """Filter the server's tools to the read-only grounding subset."""
    return [t for t in tools if _bare_name(t.name) in READ_TOOLS]


def _is_error_content(content: str) -> bool:
    """True iff ``content`` is the ``{"error": ...}`` shape a failed tool call
    returns (see :meth:`~lab_agent.mcp_client.MCPClient.call_tool`) -- these
    are never captured as evidence, only genuinely successful reads are."""
    try:
        parsed: Any = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(parsed, dict) and "error" in parsed


def _envelope(tool: str, source_id: str, content: str) -> str:
    """Wrap a successful read result as bounded, provenance-labelled untrusted
    data: the model can see where this came from, but it is DATA, never
    policy/schema/permission instructions (plan item 4)."""
    return json.dumps({"untrusted_data": True, "tool": tool, "source_id": source_id, "content": content})


async def execute_tool_calls(
    mcp: MCPClient, calls: list[ToolCall], ledger: EvidenceLedger | None = None
) -> list[Message]:
    """Run each tool call and return the resulting ``tool`` transcript messages.

    Successful allowlisted reads are recorded in ``ledger`` (if given) and
    reach the model wrapped as untrusted-data envelopes; blocked/disallowed
    calls and tool-level errors are never captured as evidence.
    """
    messages: list[Message] = []
    for call in calls:
        if _bare_name(call.name) not in READ_TOOLS:
            content = f'{{"error": "tool {call.name!r} is not permitted"}}'
        else:
            raw = await mcp.call_tool(call.name, call.arguments)
            if _is_error_content(raw):
                content = raw
            else:
                source_id = ledger.add(call.name, call.arguments, raw) if ledger is not None else ""
                content = _envelope(call.name, source_id, raw)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": content,
            }
        )
    return messages


__all__ = ["READ_TOOLS", "execute_tool_calls", "select_read_tools"]
