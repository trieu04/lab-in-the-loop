"""End-to-end model-boundary regressions for ingestion status and chunks."""

from __future__ import annotations

import json

import pytest
from pydantic import SecretStr

from lab_agent.adapters.base import AdapterResponse, Message, ToolCall
from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.loop import run_tool_loop
from lab_agent.model_gateway import GovernedAdapter, LocalityDeniedError, build_context


class _MCP:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    async def call_tool(self, name: str, _arguments: dict[str, object]) -> str:
        self.calls.append(name)
        return self.responses[name]


class _StatusThenChunks:
    def __init__(self) -> None:
        self.turn = 0

    async def generate(self, *_args: object, **_kwargs: object) -> AdapterResponse:
        self.turn += 1
        calls = {
            1: [ToolCall(id="status", name="get_ingestion_status", arguments={"canvas_id": "canvas-a"})],
            2: [ToolCall(id="chunks", name="read_ingestion_chunks", arguments={"canvas_id": "canvas-a"})],
        }.get(self.turn, [])
        return AdapterResponse(tool_calls=calls)


class _Provider:
    def __init__(self) -> None:
        self.calls = 0
        self.messages: list[Message] = []

    async def generate(self, messages: list[Message], **_kwargs: object) -> AdapterResponse:
        self.calls += 1
        self.messages = messages
        return AdapterResponse(text="grounded")


def _settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        pricing_version="test-v1",
        model_pricing={"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}},
        provider_endpoints={"openai": "https://openai.example"},
        provider_data_classifications={"openai": ["internal"]},
    )


def _status() -> str:
    return json.dumps(
        {
            "job_id": 9,
            "status": "completed",
            "data_classification": "internal",
            "asset_sha256": "a" * 64,
            "extractor_version": "text-v1",
            "mime_type": "text/plain",
        }
    )


def _chunks() -> str:
    return json.dumps(
        {
            "job_id": 9,
            "data_classification": "internal",
            "asset_sha256": "a" * 64,
            "extractor_version": "text-v1",
            "mime_type": "text/plain",
            "chunks": [{"ordinal": 0, "text": "bounded evidence"}],
        }
    )


async def test_status_then_chunks_creates_only_citeable_safe_evidence_for_next_provider(store) -> None:
    ledger = EvidenceLedger()
    messages: list[Message] = [{"role": "user", "content": "ground this"}]
    mcp = _MCP({"get_ingestion_status": _status(), "read_ingestion_chunks": _chunks()})

    await run_tool_loop(_StatusThenChunks(), mcp, messages, [], 3, ledger)

    envelopes = [json.loads(message["content"]) for message in messages if message["role"] == "tool"]
    status, chunks = envelopes
    assert mcp.calls == ["get_ingestion_status", "read_ingestion_chunks"]
    assert status["operational_status"] is True and "source_id" not in status
    assert chunks["untrusted_data"] is True and ledger.validate_citations([chunks["source_id"]])[0]
    transcript = json.dumps(envelopes)
    assert "/private/cache" not in transcript and "capability.example" not in transcript

    provider = _Provider()
    governed = GovernedAdapter(
        build_context(store, _settings(), "canvas-a", {"openai": provider}, [{"data_classification": "internal"}])
    )
    await governed.generate(messages)
    assert provider.calls == 1
    assert any(message["content"] == json.dumps(chunks, separators=(",", ":")) for message in provider.messages)


async def test_rejected_chunks_after_operational_status_denies_next_provider_before_intent(store) -> None:
    ledger = EvidenceLedger()
    messages: list[Message] = [{"role": "user", "content": "ground this"}]
    malformed_chunks = json.dumps({"job_id": 9, "data_classification": "internal", "chunks": "not-a-list"})
    await run_tool_loop(
        _StatusThenChunks(),
        _MCP({"get_ingestion_status": _status(), "read_ingestion_chunks": malformed_chunks}),
        messages,
        [],
        3,
        ledger,
    )

    provider = _Provider()
    governed = GovernedAdapter(
        build_context(store, _settings(), "canvas-a", {"openai": provider}, [{"data_classification": "internal"}])
    )
    with pytest.raises(LocalityDeniedError):
        await governed.generate(messages)

    assert provider.calls == 0
    assert ledger.audit_summary() == []
    assert store.list_incomplete_intents("canvas-a") == []


async def test_hostile_chunk_metadata_cannot_reach_provider_intent_or_audit(store) -> None:
    secret = "reader-secret-value"
    payload = json.dumps(
        {
            "job_id": 9,
            "data_classification": "internal",
            "headers": {"Authorization": f"Bearer {secret}"},
            "chunks": [{
                "ordinal": 0,
                "text": "safe chunk text",
                "metadata": {"bearer": secret, "privateKey": secret, "cookie": secret},
                "rawError": f"provider error: {secret}",
            }],
        }
    )
    ledger = EvidenceLedger()
    messages: list[Message] = [{"role": "user", "content": "ground this"}]
    await run_tool_loop(
        _StatusThenChunks(), _MCP({"get_ingestion_status": _status(), "read_ingestion_chunks": payload}),
        messages, [], 3, ledger,
    )

    tool_messages = [message for message in messages if message["role"] == "tool"]
    assert tool_messages[-1]["content"] == '{"error":"tool_result_unavailable","data_classification":"unknown"}'
    assert ledger.audit_summary() == []
    assert secret not in json.dumps(messages)

    provider = _Provider()
    governed = GovernedAdapter(
        build_context(store, _settings(), "canvas-a", {"openai": provider}, [{"data_classification": "internal"}])
    )
    with pytest.raises(LocalityDeniedError):
        await governed.generate(messages)

    assert provider.calls == 0
    durable_state = "\n".join(store.conn.iterdump())
    assert secret not in durable_state
    assert store.list_incomplete_intents("canvas-a") == []


async def test_configured_bearer_stays_out_of_provider_transcript_audit_and_intent(store) -> None:
    bearer = "reader-secret-value"
    settings = Settings(
        openai_api_key="test-key",
        mcp_bearer_token=SecretStr(bearer),
        pricing_version="test-v1",
        model_pricing={"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}},
        provider_endpoints={"openai": "https://openai.example"},
        provider_data_classifications={"openai": ["internal"]},
    )
    provider = _Provider()
    governed = GovernedAdapter(
        build_context(store, settings, "canvas-a", {"openai": provider}, [{"data_classification": "internal"}])
    )

    await governed.generate([{"role": "user", "content": "ground this"}])

    durable_state = "\n".join(store.conn.iterdump())
    assert bearer not in repr(settings)
    assert bearer not in json.dumps(provider.messages)
    assert bearer not in durable_state
