"""Bounded, authorized MCP reads at the model/evidence boundary."""

from __future__ import annotations

import json
from typing import Any

from lab_agent.adapters.base import Message, ToolCall, ToolSpec
from lab_agent.evidence import EvidenceLedger, compute_source_id, safe_provenance
from lab_agent.mcp_client import MCPClient
from lab_agent.models.governance import DataClassification
from lab_agent.result_safety import ResultLimits, fixed_error_json, is_unsafe_data, sanitize_result

READ_TOOLS: frozenset[str] = frozenset(
    {
        "scan_server",
        "list_canvases",
        "check_ragcluster_connections",
        "check_widget_connections",
        "get_note",
        "get_widget",
        "download_pdf",
        "get_ingestion_status",
        "read_ingestion_chunks",
    }
)
_INGESTION_STATUS = "get_ingestion_status"
_INGESTION_CHUNKS = "read_ingestion_chunks"


def tool_identity(name: str, namespace: str = "canvus") -> str | None:
    """Return an allowlisted bare MCP dispatch name, never suffix-match names."""
    if name in READ_TOOLS:
        return name
    prefix = f"mcp__{namespace}__"
    if not name.startswith(prefix):
        return None
    bare = name[len(prefix):]
    return bare if bare in READ_TOOLS else None


def select_read_tools(tools: list[ToolSpec], namespace: str = "canvus") -> list[ToolSpec]:
    """Expose only exact approved bare/configured-namespaced read tools."""
    return [tool for tool in tools if tool_identity(tool.name, namespace) is not None]


def _classification(payload: dict[str, Any]) -> DataClassification:
    try:
        return DataClassification(str(payload.get("data_classification")))
    except (TypeError, ValueError):
        return DataClassification.UNKNOWN


def _provenance(payload: dict[str, Any], arguments: dict[str, Any]) -> dict[str, str | int]:
    values = {
        "canvas_id": arguments.get("canvas_id"),
        "job_id": payload.get("job_id"),
        "asset_sha256": payload.get("asset_sha256"),
        "extractor_version": payload.get("extractor_version"),
        "modality": payload.get("mime_type"),
    }
    return safe_provenance({
        key: value
        for key, value in values.items()
        if isinstance(value, (str, int)) and not isinstance(value, bool)
    })


def _fixed_message(call: ToolCall) -> Message:
    return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": fixed_error_json()}


def _generic_envelope(tool: str, source_id: str, payload: dict[str, Any], classification: DataClassification) -> str:
    return json.dumps(
        {"untrusted_data": True, "tool": tool, "source_id": source_id,
         "data_classification": classification.value,
         "content": json.dumps(payload, separators=(",", ":"))},
        separators=(",", ":"),
    )


def _status_envelope(tool: str, payload: dict[str, Any], arguments: dict[str, Any], classification: DataClassification) -> str:
    status_keys = ("status", "unit_count", "completed_units", "failed_units", "cancel_requested")
    status = {key: payload[key] for key in status_keys if isinstance(payload.get(key), (str, int, bool))}
    return json.dumps(
        {"operational_status": True, "tool": tool, "data_classification": classification.value,
         "provenance": _provenance(payload, arguments), "status": status}, separators=(",", ":")
    )


def _chunk_body(payload: dict[str, Any]) -> list[dict[str, str | int]] | None:
    chunks = payload.get("chunks")
    if not isinstance(chunks, list):
        return None
    safe: list[dict[str, str | int]] = []
    for item in chunks:
        if not isinstance(item, dict) or not isinstance(item.get("ordinal"), int) or isinstance(item["ordinal"], bool):
            return None
        if not isinstance(item.get("text"), str):
            return None
        safe.append({"ordinal": item["ordinal"], "text": item["text"]})
    return safe


def _fits_model_limit(content: str, limits: ResultLimits) -> bool:
    return len(content.encode("utf-8")) <= limits.max_bytes


def _chunks_envelope(
    tool: str, source_id: str, chunks: list[dict[str, str | int]], payload: dict[str, Any],
    arguments: dict[str, Any], classification: DataClassification,
) -> str:
    return json.dumps(
        {"untrusted_data": True, "tool": tool, "source_id": source_id,
         "data_classification": classification.value, "provenance": _provenance(payload, arguments),
         "chunks": chunks}, separators=(",", ":")
    )


async def execute_tool_calls(
    mcp: MCPClient, calls: list[ToolCall], ledger: EvidenceLedger | None = None, *,
    namespace: str = "canvus", limits: ResultLimits | None = None,
    argument_limits: ResultLimits | None = None,
) -> list[Message]:
    """Dispatch exact safe reads and return bounded model-facing envelopes."""
    limits = limits or ResultLimits()
    argument_limits = argument_limits or limits
    messages: list[Message] = []
    for call in calls:
        tool = tool_identity(call.name, namespace)
        if tool is None or is_unsafe_data(call.arguments, argument_limits):
            messages.append(_fixed_message(call))
            continue
        payload = sanitize_result(await mcp.call_tool(tool, call.arguments), limits)
        if payload is None:
            messages.append(_fixed_message(call))
            continue
        classification = _classification(payload)
        evidence: str | None = None
        provenance: dict[str, str | int] | None = None
        captured = False
        if tool == _INGESTION_STATUS:
            content = _status_envelope(tool, payload, call.arguments, classification)
        elif tool == _INGESTION_CHUNKS:
            chunks = _chunk_body(payload)
            if chunks is None or ledger is None:
                messages.append(_fixed_message(call))
                continue
            evidence = json.dumps({"chunks": chunks}, separators=(",", ":"))
            provenance = _provenance(payload, call.arguments)
            source_id = ledger.add(tool, call.arguments, evidence, classification, provenance)
            if not source_id or not ledger.known(source_id):
                messages.append(_fixed_message(call))
                continue
            captured = True
            content = _chunks_envelope(tool, source_id, chunks, payload, call.arguments, classification)
        else:
            evidence = json.dumps(payload)
            source_id = compute_source_id(tool, call.arguments, evidence) if ledger else ""
            content = _generic_envelope(tool, source_id, payload, classification)
        if not _fits_model_limit(content, limits):
            messages.append(_fixed_message(call))
            continue
        if evidence is not None and ledger is not None and not captured:
            source_id = ledger.add(tool, call.arguments, evidence, classification, provenance)
            if not source_id:
                messages.append(_fixed_message(call))
                continue
        messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content})
    return messages


__all__ = ["READ_TOOLS", "execute_tool_calls", "select_read_tools", "tool_identity"]
