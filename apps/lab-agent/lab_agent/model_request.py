"""Provider-neutral model request identity and conservative usage estimates."""

from __future__ import annotations

import hashlib
import json
from typing import Protocol, runtime_checkable

from lab_agent.adapters.base import AdapterResponse, Message, ToolSpec
from lab_agent.models.governance import Usage, UsageStatus


@runtime_checkable
class SubmittedCallReconciler(Protocol):
    """Optional provider capability for recovering an uncertain submission."""

    async def reconcile_submission(
        self, *, idempotency_key: str, request_id: str | None
    ) -> AdapterResponse | None:
        """Return a completed response, or ``None`` when it remains unresolved."""
        ...


def _request_payload(
    messages: list[Message],
    tools: list[ToolSpec] | None,
    response_schema: dict | None,
    schema_name: str,
) -> dict[str, object]:
    return {
        "messages": messages,
        "response_schema": response_schema,
        "schema_name": schema_name,
        "tools": [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in tools or []
        ],
    }


def _serialize(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def request_digest(
    messages: list[Message],
    tools: list[ToolSpec] | None,
    response_schema: dict | None,
    schema_name: str,
) -> str:
    """Hash every provider-visible request field for durable intent matching."""
    encoded = _serialize(_request_payload(messages, tools, response_schema, schema_name))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _token_estimate(value: object) -> int:
    """Use a conservative deterministic bound for usage-omitting providers."""
    return (len(_serialize(value)) + 2) // 3


def estimate_usage(
    provider: str,
    model: str,
    messages: list[Message],
    response: AdapterResponse | None = None,
    request_id: str | None = None,
    *,
    tools: list[ToolSpec] | None = None,
    response_schema: dict | None = None,
    schema_name: str = "result",
    max_completion_tokens: int = 4096,
) -> Usage:
    """Conservatively estimate all provider-visible input without exact-count claims."""
    prompt_tokens = _token_estimate(_request_payload(messages, tools, response_schema, schema_name))
    completion_tokens = max_completion_tokens
    if response is not None:
        response_content = {
            "parsed": response.parsed or {},
            "text": response.text or "",
            "tool_calls": [(tool.name, tool.arguments) for tool in response.tool_calls],
        }
        completion_tokens = max(max_completion_tokens, _token_estimate(response_content))
    return Usage(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        request_id=request_id,
        status=UsageStatus.ESTIMATED,
    )


__all__ = ["SubmittedCallReconciler", "estimate_usage", "request_digest"]
