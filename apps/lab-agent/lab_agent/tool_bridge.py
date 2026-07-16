"""Bridge between MCP tools and the model's tool-use interface.

Two responsibilities:
1. Expose only the **read-only** canvus-mcp tools to the model, so it can
   ground itself but cannot mutate the canvas — all writes are orchestrator
   owned (plan: "agentic reads, orchestrated writes").
2. Execute the tool calls a model requested against the live MCP session and
   package the results as normalized ``tool`` messages for the transcript.
"""

from __future__ import annotations

from lab_agent.adapters.base import Message, ToolCall, ToolSpec
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


async def execute_tool_calls(mcp: MCPClient, calls: list[ToolCall]) -> list[Message]:
    """Run each tool call and return the resulting ``tool`` transcript messages."""
    messages: list[Message] = []
    for call in calls:
        if _bare_name(call.name) not in READ_TOOLS:
            content = f'{{"error": "tool {call.name!r} is not permitted"}}'
        else:
            content = await mcp.call_tool(call.name, call.arguments)
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
