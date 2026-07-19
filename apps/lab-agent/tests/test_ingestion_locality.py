"""Operational and failed ingestion envelopes must constrain the next model call."""

from __future__ import annotations

import json

import pytest

from lab_agent.adapters.base import AdapterResponse, ToolCall
from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.model_gateway import GovernedAdapter, LocalityDeniedError, build_context
from lab_agent.tool_bridge import execute_tool_calls
from tests.test_model_gateway import MESSAGES, SpyAdapter


class _MCP:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def call_tool(self, _name: str, _arguments: dict[str, object]) -> str:
        return self.payload


def _settings() -> Settings:
    return Settings(
        openai_api_key="key",
        model_pricing={"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}},
        pricing_version="test-v1",
        provider_endpoints={"openai": "https://openai.example"},
        provider_data_classifications={"openai": ["internal"]},
    )


@pytest.mark.parametrize(
    ("tool", "payload"),
    [
        ("get_ingestion_status", json.dumps({"job_id": 1, "data_classification": "restricted", "status": "completed"})),
        ("read_ingestion_chunks", "malformed"),
        ("read_ingestion_chunks", json.dumps({"job_id": 1, "chunks": []})),
    ],
)
async def test_ingestion_operational_missing_or_error_classification_denies_next_call(store, tool: str, payload: str) -> None:
    inner = SpyAdapter([AdapterResponse(text="must not dispatch")])
    governed = GovernedAdapter(build_context(store, _settings(), "canvas", {"openai": inner}, [{"data_classification": "internal"}]))
    messages = list(MESSAGES)
    messages.extend(await execute_tool_calls(_MCP(payload), [ToolCall(id="1", name=tool, arguments={})], EvidenceLedger()))

    with pytest.raises(LocalityDeniedError):
        await governed.generate(messages)

    assert inner.calls == 0
