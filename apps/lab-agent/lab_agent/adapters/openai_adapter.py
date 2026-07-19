"""OpenAI (and OpenAI-compatible) model adapter.

Works against api.openai.com or any OpenAI-compatible endpoint via
``base_url`` (Ollama, vLLM, Azure). Structured output is obtained by forcing a
call to a synthetic ``emit_result`` tool whose parameters are the requested
JSON schema — this behaves uniformly with the Claude adapter and avoids
relying on ``response_format`` support.
"""

from __future__ import annotations

import json
from typing import Any, NoReturn

from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    UnprocessableEntityError,
)

from lab_agent.adapters.base import (
    AdapterResponse,
    AmbiguousProviderError,
    DeterministicProviderError,
    Message,
    ToolCall,
    ToolSpec,
    TransientProviderError,
    normalize_usage,
)
from lab_agent.models.governance import Usage

EMIT_TOOL = "emit_result"


def _usage_from(completion: Any, model: str) -> Usage:
    """Normalize an OpenAI ``ChatCompletion`` into a provider-neutral :class:`Usage`.

    Uses the exact ``usage`` payload when present; a response without it (some
    OpenAI-compatible endpoints omit usage) yields an ``UNAVAILABLE`` record
    rather than a fabricated count. Only ids and counts are read -- never the
    generated content.
    """
    raw = getattr(completion, "usage", None)
    return normalize_usage(
        provider="openai",
        model=model,
        request_id=getattr(completion, "id", None),
        prompt_tokens=getattr(raw, "prompt_tokens", None),
        completion_tokens=getattr(raw, "completion_tokens", None),
        total_tokens=getattr(raw, "total_tokens", None),
    )


def _to_openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert the normalized transcript to OpenAI chat messages."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role in ("system", "user"):
            out.append({"role": role, "content": m.get("content") or ""})
        elif role == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": m.get("content")}
            calls = m.get("tool_calls") or []
            if calls:
                msg["tool_calls"] = [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c["arguments"]),
                        },
                    }
                    for c in calls
                ]
            out.append(msg)
        elif role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m["tool_call_id"],
                    "content": m.get("content") or "",
                }
            )
    return out


def _tool_param(spec: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        },
    }


def _raise_provider_error(exc: Exception) -> NoReturn:
    request_id = getattr(exc, "request_id", None)
    if isinstance(
        exc,
        (
            BadRequestError,
            AuthenticationError,
            PermissionDeniedError,
            NotFoundError,
            UnprocessableEntityError,
        ),
    ):
        raise DeterministicProviderError(str(exc), request_id) from exc
    if isinstance(exc, RateLimitError):
        raise TransientProviderError(str(exc), request_id) from exc
    if isinstance(exc, (APIConnectionError, APIStatusError)):
        raise AmbiguousProviderError(str(exc), request_id) from exc
    raise exc


class OpenAIAdapter:
    """Adapter over :class:`openai.AsyncOpenAI`."""

    def __init__(
        self, api_key: str, model: str, base_url: str | None = None, max_output_tokens: int = 4096
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        self._model = model
        self._max_output_tokens = max_output_tokens

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "result",
    ) -> AdapterResponse:
        try:
            return await self._generate(messages, tools, response_schema, schema_name)
        except (APIConnectionError, APIStatusError) as exc:
            _raise_provider_error(exc)

    async def _generate(
        self, messages: list[Message], tools: list[ToolSpec] | None,
        response_schema: dict[str, Any] | None, schema_name: str,
    ) -> AdapterResponse:
        oai_messages = _to_openai_messages(messages)

        if response_schema is not None:
            emit = ToolSpec(
                name=EMIT_TOOL,
                description=f"Emit the final {schema_name} as structured data.",
                parameters=response_schema,
            )
            completion = await self._client.chat.completions.create(  # type: ignore[call-overload]
                model=self._model,
                messages=oai_messages,
                max_tokens=self._max_output_tokens,
                tools=[_tool_param(emit)],
                tool_choice={"type": "function", "function": {"name": EMIT_TOOL}},
            )
            call = completion.choices[0].message.tool_calls[0]
            return AdapterResponse(
                parsed=json.loads(call.function.arguments or "{}"),
                usage=_usage_from(completion, self._model),
            )

        kwargs: dict[str, Any] = {
            "model": self._model, "messages": oai_messages, "max_tokens": self._max_output_tokens
        }
        if tools:
            kwargs["tools"] = [_tool_param(t) for t in tools]
        completion = await self._client.chat.completions.create(**kwargs)  # type: ignore[call-overload]
        choice = completion.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=json.loads(c.function.arguments or "{}"))
            for c in (choice.tool_calls or [])
        ]
        return AdapterResponse(
            text=choice.content, tool_calls=calls, usage=_usage_from(completion, self._model)
        )


__all__ = ["OpenAIAdapter"]
