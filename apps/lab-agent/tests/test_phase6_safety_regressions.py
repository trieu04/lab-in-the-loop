"""Regression coverage for Phase 6 model-boundary safety findings."""

from __future__ import annotations

import json

import pytest

from lab_agent import nodes
from lab_agent.adapters.base import AdapterResponse, Message, ToolCall
from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.loop import ToolLoopLimits, run_tool_loop
from lab_agent.model_gateway import GovernedAdapter, LocalityDeniedError, build_context
from lab_agent.orchestrator_emit import ground_and_emit_setup
from lab_agent.result_safety import ResultLimits, is_unsafe_text
from lab_agent.tool_bridge import execute_tool_calls
from tests.fakes import FakeMCP


class _MCP:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def call_tool(self, name: str, _arguments: dict[str, object]) -> str:
        self.calls.append(name)
        return self.payload


class _ManyCallsAdapter:
    async def generate(self, *_args: object, **_kwargs: object) -> AdapterResponse:
        return AdapterResponse(
            tool_calls=[
                ToolCall(id=str(index), name="get_note", arguments={}) for index in range(3)
            ]
        )


class _OneChunkAdapter:
    async def generate(self, *_args: object, **_kwargs: object) -> AdapterResponse:
        return AdapterResponse(tool_calls=[ToolCall(id="chunk", name="read_ingestion_chunks", arguments={})])


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *_args: object, **_kwargs: object) -> AdapterResponse:
        self.calls += 1
        return AdapterResponse(text="unexpected")


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Basic dXNlcjpwYXNz",
        "Proxy-Authorization: Digest user=alice",
        "Cookie=sessionid=abc",
        "result=/srv/private/file",
        "Traceback (most recent call last): details",
    ],
)
def test_sensitive_text_forms_are_rejected(text: str) -> None:
    assert is_unsafe_text(text)


def test_benign_token_prose_is_not_rejected() -> None:
    assert not is_unsafe_text("Token economics: a primer")


async def test_ledger_full_chunk_is_not_presented_as_citeable() -> None:
    payload = json.dumps(
        {
            "data_classification": "internal",
            "chunks": [{"ordinal": 0, "text": "safe chunk"}],
        }
    )
    ledger = EvidenceLedger(max_records=1)
    mcp = _MCP(payload)
    calls = [
        ToolCall(id="first", name="read_ingestion_chunks", arguments={"ordinal": 0}),
        ToolCall(id="second", name="read_ingestion_chunks", arguments={"ordinal": 1}),
    ]

    messages = await execute_tool_calls(mcp, calls, ledger)

    assert json.loads(messages[0]["content"])["source_id"]
    assert messages[1]["content"] == '{"error":"tool_result_unavailable","data_classification":"unknown"}'


async def test_tool_loop_caps_calls_before_mcp_dispatch() -> None:
    mcp = _MCP(json.dumps({"text": "safe"}))
    messages: list[Message] = [{"role": "user", "content": "ground this"}]

    await run_tool_loop(
        _ManyCallsAdapter(), mcp, messages, [], 1,
        limits=ResultLimits(max_bytes=1024, max_string_chars=1024),
        run_limits=ToolLoopLimits(max_calls_per_turn=2, max_calls_per_run=2, max_transcript_bytes=4096),
    )

    assert len(mcp.calls) == 2


async def test_unsafe_note_text_never_reaches_provider_or_intent(store) -> None:
    provider = _Provider()
    settings = Settings(
        openai_api_key="test-key", pricing_version="v1",
        model_pricing={"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}},
        provider_endpoints={"openai": "https://openai.example"},
        provider_data_classifications={"openai": ["internal"]},
    )
    governed = GovernedAdapter(
        build_context(store, settings, "canvas", {"openai": provider}, [{"data_classification": "internal"}])
    )

    with pytest.raises(RuntimeError, match="model_input_unavailable"):
        await governed.generate([{"role": "user", "content": "Authorization: Basic dXNlcjpwYXNz"}])

    assert provider.calls == 0
    assert store.list_incomplete_intents("canvas") == []


@pytest.mark.parametrize("unsafe", [
    "Authorization: Basic dXNlcjpwYXNz", "Proxy-Authorization: Digest user=alice",
    "Cookie=sessionid=abc", "result=/srv/private/file", "Traceback (most recent call last): details",
])
async def test_unsafe_ingestion_chunks_never_reach_provider(unsafe: str, store) -> None:
    payload = json.dumps({"data_classification": "internal", "chunks": [{"ordinal": 0, "text": unsafe}]})
    messages: list[Message] = [{"role": "user", "content": "ground this"}]
    await run_tool_loop(_OneChunkAdapter(), _MCP(payload), messages, [], 1, EvidenceLedger())
    provider = _Provider()
    settings = Settings(
        openai_api_key="test-key", pricing_version="v1",
        model_pricing={"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}},
        provider_endpoints={"openai": "https://openai.example"},
        provider_data_classifications={"openai": ["internal"]},
    )
    governed = GovernedAdapter(
        build_context(store, settings, "canvas", {"openai": provider}, [{"data_classification": "internal"}])
    )

    with pytest.raises(LocalityDeniedError):
        await governed.generate(messages)

    assert provider.calls == 0
    assert unsafe not in json.dumps(messages)


async def test_unsafe_note_read_never_reaches_setup_provider(store) -> None:
    unsafe = "Authorization: Basic dXNlcjpwYXNz"
    mcp = FakeMCP(note_text={"idea": unsafe})
    provider = _Provider()
    settings = Settings(
        openai_api_key="test-key", pricing_version="v1",
        model_pricing={"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}},
        provider_endpoints={"openai": "https://openai.example"},
        provider_data_classifications={"openai": ["internal"]},
    )
    adapter = GovernedAdapter(
        build_context(store, settings, "canvas", {"openai": provider}, [{"data_classification": "internal"}])
    )
    note = await nodes.read_note_text(mcp, "canvas", "idea")

    result = await ground_and_emit_setup(
        mcp, adapter, settings, canvas_id="canvas", idea_text=note, ragcluster_id="", ledger=EvidenceLedger(),
    )

    assert result is None and provider.calls == 0
    assert store.list_incomplete_intents("canvas") == []


async def test_oversized_arguments_are_not_dispatched_or_recorded() -> None:
    class OversizedArgumentAdapter:
        async def generate(self, *_args: object, **_kwargs: object) -> AdapterResponse:
            return AdapterResponse(tool_calls=[ToolCall(
                id="large", name="get_note", arguments={f"field_{index}": index for index in range(100)},
            )])

    mcp = _MCP(json.dumps({"text": "safe"}))
    messages: list[Message] = [{"role": "user", "content": "ground this"}]
    limit = ResultLimits(max_bytes=1024, max_string_chars=1024, max_items=32)

    await run_tool_loop(
        OversizedArgumentAdapter(), mcp, messages, [], 1, limits=limit, argument_limits=limit,
    )

    assert mcp.calls == []
    assert "field_0" not in json.dumps(messages)
