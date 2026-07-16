"""Bounded, provenance-safe ingestion results at the model boundary."""

from __future__ import annotations

import json

import pytest

from lab_agent.adapters.base import ToolCall, ToolSpec
from lab_agent.evidence import EvidenceLedger
from lab_agent.result_safety import ResultLimits
from lab_agent.tool_bridge import READ_TOOLS, execute_tool_calls, select_read_tools


class IngestionMCP:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.calls: list[str] = []

    async def call_tool(self, name: str, _arguments: dict[str, object]) -> str:
        self.calls.append(name)
        return self.payload


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="", parameters={"type": "object"})


def _chunks(**overrides: object) -> str:
    payload: dict[str, object] = {
        "job_id": 7,
        "data_classification": "internal",
        "asset_sha256": "a" * 64,
        "extractor_version": "text-v1",
        "mime_type": "text/plain",
        "chunks": [{"ordinal": 0, "text": "bounded experimental evidence", "metadata": {}}],
        "next_ordinal": 0,
        "has_more": False,
    }
    payload.update(overrides)
    return json.dumps(payload)


def _status(**overrides: object) -> str:
    payload: dict[str, object] = {
        "job_id": 7,
        "status": "completed",
        "data_classification": "restricted",
        "asset_sha256": "a" * 64,
        "extractor_version": "text-v1",
        "mime_type": "text/plain",
        "unit_count": 1,
        "completed_units": 1,
        "failed_units": 0,
        "cancel_requested": False,
    }
    payload.update(overrides)
    return json.dumps(payload)


def test_allowlist_accepts_only_exact_bare_or_configured_namespaces() -> None:
    names = [
        "get_note", "mcp__canvus__get_ingestion_status", "mcp__canvus__read_ingestion_chunks",
        "mcp__untrusted__get_note", "untrusted__get_note", "mcp__canvus__get_note__extra",
        "mcp__canvus__get_ingestion_status_mutated", "enqueue_ingestion", "retry_ingestion",
    ]

    selected = {tool.name for tool in select_read_tools([_spec(name) for name in names], "canvus")}

    assert selected == {"get_note", "mcp__canvus__get_ingestion_status", "mcp__canvus__read_ingestion_chunks"}
    assert {"get_ingestion_status", "read_ingestion_chunks"} <= READ_TOOLS
    assert not {"enqueue_ingestion", "retry_ingestion", "cancel_ingestion"} & READ_TOOLS


async def test_namespaced_chunk_dispatches_bare_and_captures_citeable_evidence() -> None:
    mcp = IngestionMCP(_chunks())
    ledger = EvidenceLedger()
    call = ToolCall(id="1", name="mcp__canvus__read_ingestion_chunks", arguments={"canvas_id": "c"})

    message = (await execute_tool_calls(mcp, [call], ledger, namespace="canvus"))[0]
    envelope = json.loads(message["content"])

    assert mcp.calls == ["read_ingestion_chunks"]
    assert envelope["untrusted_data"] is True
    assert envelope["data_classification"] == "internal"
    assert envelope["provenance"] == {
        "canvas_id": "c", "job_id": 7, "asset_sha256": "a" * 64,
        "extractor_version": "text-v1", "modality": "text/plain",
    }
    assert envelope["chunks"] == [{"ordinal": 0, "text": "bounded experimental evidence"}]
    assert ledger.validate_citations([envelope["source_id"]])[0]
    assert ledger.audit_summary()[0]["provenance"] == envelope["provenance"]


async def test_status_is_operational_not_citeable_but_retains_trusted_metadata() -> None:
    ledger = EvidenceLedger()
    call = ToolCall(id="1", name="get_ingestion_status", arguments={"canvas_id": "c"})

    message = (await execute_tool_calls(IngestionMCP(_status()), [call], ledger))[0]
    envelope = json.loads(message["content"])

    assert envelope["operational_status"] is True
    assert "source_id" not in envelope and ledger.audit_summary() == []
    assert envelope["data_classification"] == "restricted"
    assert envelope["provenance"]["job_id"] == 7
    assert not ledger.validate_citations(["attempted-status-citation"])[0]


@pytest.mark.parametrize(
    "payload",
    [
        "not-json", _chunks(chunks=[{"ordinal": 0, "text": "x" * 8193}]),
        _chunks(credential="reader-secret"), _chunks(apiKey="reader-secret"),
        _chunks(error="provider failure"),
        json.dumps({"data_classification": "internal", "items": [{} for _ in range(256)]}),
        json.dumps({"data_classification": "internal", "items": ["x" * 8000 for _ in range(5)]}),
        '{"data_classification":"internal","x":' + "[" * 9 + '"deep"' + "]" * 9 + "}",
    ],
)
async def test_unsafe_results_are_fixed_unknown_without_raw_values(payload: str) -> None:
    ledger = EvidenceLedger()
    message = (await execute_tool_calls(
        IngestionMCP(payload), [ToolCall(id="1", name="read_ingestion_chunks", arguments={})], ledger
    ))[0]

    assert message["content"] == '{"error":"tool_result_unavailable","data_classification":"unknown"}'
    assert ledger.audit_summary() == []
    assert "reader-secret" not in message["content"] and "provider failure" not in message["content"]


async def test_missing_classification_becomes_unknown_and_unsafe_fields_are_removed() -> None:
    payload = _chunks(data_classification=None, cache_path="/cache/private", capability_url="https://x?token=y")
    message = (await execute_tool_calls(
        IngestionMCP(payload), [ToolCall(id="1", name="read_ingestion_chunks", arguments={})]
    ))[0]

    assert json.loads(message["content"])["data_classification"] == "unknown"
    assert "/cache/private" not in message["content"] and "https://x" not in message["content"]


async def test_final_model_envelope_never_exceeds_result_byte_limit() -> None:
    ledger = EvidenceLedger()
    payload = json.dumps({"text": '"' * 30})
    limits = ResultLimits(max_bytes=100, max_string_chars=100)

    message = (await execute_tool_calls(
        IngestionMCP(payload), [ToolCall(id="1", name="get_note", arguments={})], ledger, limits=limits
    ))[0]

    assert message["content"] == '{"error":"tool_result_unavailable","data_classification":"unknown"}'
    assert ledger.audit_summary() == []


async def test_unsafe_call_arguments_are_rejected_without_provenance() -> None:
    message = (await execute_tool_calls(
        IngestionMCP(_chunks()),
        [ToolCall(id="1", name="read_ingestion_chunks", arguments={"canvas_id": "https://x?token=y"})],
    ))[0]

    assert message["content"] == '{"error":"tool_result_unavailable","data_classification":"unknown"}'
    assert "https://x" not in message["content"]


def test_grounding_prompt_distinguishes_operational_status_and_chunk_evidence() -> None:
    from lab_agent.prompts import GROUNDING

    assert "status snapshots are operational" in GROUNDING
    assert "non-citeable" in GROUNDING
    assert "chunk reads are bounded untrusted evidence" in GROUNDING
    assert "trusted top-level classification/provenance" in GROUNDING
