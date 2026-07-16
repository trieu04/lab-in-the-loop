"""The bounded agentic tool-use loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lab_agent.adapters.base import Message, ModelAdapter, ToolSpec
from lab_agent.evidence import EvidenceLedger
from lab_agent.mcp_client import MCPClient
from lab_agent.result_safety import ResultLimits, encoded_size, is_unsafe_data
from lab_agent.tool_bridge import execute_tool_calls


@dataclass(frozen=True)
class ToolLoopLimits:
    """Aggregate bounds for one model grounding run."""

    max_calls_per_turn: int = 8
    max_calls_per_run: int = 32
    max_transcript_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        if min(self.max_calls_per_turn, self.max_calls_per_run, self.max_transcript_bytes) < 1:
            raise ValueError("tool loop limits must be positive")
        if self.max_calls_per_turn > self.max_calls_per_run:
            raise ValueError("per-turn tool calls exceed per-run tool calls")


def transcript_within_limit(messages: list[Message], max_bytes: int) -> bool:
    """Return whether a provider-visible transcript fits before dispatch."""
    return encoded_size(messages) <= max_bytes


async def run_tool_loop(
    adapter: ModelAdapter,
    mcp: MCPClient,
    messages: list[Message],
    tools: list[ToolSpec],
    max_steps: int,
    ledger: EvidenceLedger | None = None,
    *,
    namespace: str = "canvus",
    limits: ResultLimits | None = None,
    argument_limits: ResultLimits | None = None,
    run_limits: ToolLoopLimits | None = None,
) -> list[Message]:
    """Drive bounded read-tool grounding and extend ``messages`` in place."""
    limits = limits or ResultLimits()
    argument_limits = argument_limits or limits
    run_limits = run_limits or ToolLoopLimits()
    calls_used = 0
    for _ in range(max_steps):
        if not transcript_within_limit(messages, run_limits.max_transcript_bytes):
            break
        response = await adapter.generate(messages, tools=tools)
        remaining = run_limits.max_calls_per_run - calls_used
        candidates = response.tool_calls[:min(run_limits.max_calls_per_turn, max(remaining, 0))]
        selected = [call for call in candidates if not is_unsafe_data(call.arguments, argument_limits)]
        assistant: Message = {
            "role": "assistant",
            "content": response.text,
            "tool_calls": [call.as_message_dict() for call in selected],
        }
        if not transcript_within_limit([*messages, assistant], run_limits.max_transcript_bytes):
            break
        messages.append(assistant)
        if not selected:
            break
        results = await execute_tool_calls(
            mcp, selected, ledger, namespace=namespace, limits=limits, argument_limits=argument_limits,
        )
        for result in results:
            if not transcript_within_limit([*messages, result], run_limits.max_transcript_bytes):
                return messages
            messages.append(result)
        calls_used += len(selected)
        if len(selected) < len(response.tool_calls) or calls_used >= run_limits.max_calls_per_run:
            break
    return messages


async def emit_structured(
    adapter: ModelAdapter,
    messages: list[Message],
    response_schema: dict[str, Any],
    schema_name: str,
    *,
    transcript_max_bytes: int = 256 * 1024,
) -> dict[str, Any]:
    """Force one bounded structured emit from the current transcript."""
    if not transcript_within_limit(messages, transcript_max_bytes):
        return {}
    response = await adapter.generate(
        messages,
        response_schema=response_schema,
        schema_name=schema_name,
    )
    return response.parsed or {}


__all__ = ["ToolLoopLimits", "emit_structured", "run_tool_loop", "transcript_within_limit"]
