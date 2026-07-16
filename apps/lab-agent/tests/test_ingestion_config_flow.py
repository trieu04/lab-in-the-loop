"""Settings must govern namespace matching and inbound-result bounds."""

from __future__ import annotations

import json

from lab_agent.adapters.base import AdapterResponse, ToolCall, ToolSpec
from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.loop import run_tool_loop
from lab_agent.result_safety import ResultLimits


class _MCP:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def call_tool(self, name: str, _arguments: dict[str, object]) -> str:
        self.calls.append(name)
        return json.dumps({"data_classification": "internal", "chunks": [{"ordinal": 0, "text": "five"}]})


class _Adapter:
    def __init__(self) -> None:
        self.turn = 0

    async def generate(self, *_args, **_kwargs) -> AdapterResponse:
        self.turn += 1
        return AdapterResponse(
            tool_calls=[ToolCall(id="1", name="mcp__custom__read_ingestion_chunks", arguments={})]
            if self.turn == 1 else []
        )


async def test_tool_loop_passes_configured_namespace_and_result_limits() -> None:
    settings = Settings(mcp_server_namespace="custom", mcp_result_max_string_chars=4)
    limits = ResultLimits(
        max_depth=settings.mcp_result_max_depth,
        max_containers=settings.mcp_result_max_containers,
        max_string_chars=settings.mcp_result_max_string_chars,
        max_bytes=settings.mcp_result_max_bytes,
    )
    messages = [{"role": "user", "content": "read"}]

    await run_tool_loop(
        _Adapter(), _MCP(), messages, [ToolSpec(name="mcp__custom__read_ingestion_chunks", description="", parameters={})],
        2, EvidenceLedger(), namespace=settings.mcp_server_namespace, limits=limits,
    )

    assert messages[-2]["content"] == '{"error":"tool_result_unavailable","data_classification":"unknown"}'
