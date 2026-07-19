"""Anthropic / Claude model adapter.

Maps the normalized transcript onto the Messages API: system messages become
the top-level ``system`` string, tool calls become ``tool_use`` content
blocks, and tool results become ``tool_result`` blocks folded into a user
turn (consecutive tool messages merge into one user turn, as the API
requires). Structured output forces a call to a synthetic ``emit_result``
tool, matching the OpenAI adapter's behaviour.
"""

from __future__ import annotations

from typing import Any, NoReturn

from anthropic import (
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
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


def _usage_from(resp: Any, model: str) -> Usage:
    """Normalize an Anthropic ``Message`` into a provider-neutral :class:`Usage`.

    The Messages API reports input/output counts plus optional prompt-cache
    creation/read counts. All input categories count toward the conservative
    token total; pricing still uses the configured provider-neutral input rate.
    Missing usage stays ``UNAVAILABLE`` rather than inventing exact counts.
    """
    raw = getattr(resp, "usage", None)
    prompt_tokens = getattr(raw, "input_tokens", None)
    if prompt_tokens is not None:
        prompt_tokens += int(getattr(raw, "cache_creation_input_tokens", 0) or 0)
        prompt_tokens += int(getattr(raw, "cache_read_input_tokens", 0) or 0)
    return normalize_usage(
        provider="claude",
        model=model,
        request_id=getattr(resp, "id", None),
        prompt_tokens=prompt_tokens,
        completion_tokens=getattr(raw, "output_tokens", None),
    )


def _split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    system_parts = [m.get("content") or "" for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]
    return "\n\n".join(p for p in system_parts if p), rest


def _to_claude_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert non-system messages to Anthropic message blocks."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "user":
            out.append({"role": "user", "content": [{"type": "text", "text": m.get("content") or ""}]})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for c in m.get("tool_calls") or []:
                blocks.append(
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["arguments"]}
                )
            out.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m.get("content") or "",
            }
            # Merge into the previous user turn if it holds tool_results.
            if out and out[-1]["role"] == "user" and all(
                b.get("type") == "tool_result" for b in out[-1]["content"]
            ):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def _tool_param(spec: ToolSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "description": spec.description,
        "input_schema": spec.parameters,
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


class ClaudeAdapter:
    """Adapter over :class:`anthropic.AsyncAnthropic`."""

    def __init__(
        self, api_key: str, model: str, base_url: str | None = None, max_output_tokens: int = 4096
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key, base_url=base_url or None)
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
        system, rest = _split_system(messages)
        claude_messages = _to_claude_messages(rest)
        if response_schema is not None:
            emit = ToolSpec(
                name=EMIT_TOOL,
                description=f"Emit the final {schema_name} as structured data.",
                parameters=response_schema,
            )
            resp = await self._client.messages.create(  # type: ignore[call-overload]
                model=self._model,
                max_tokens=self._max_output_tokens,
                system=system or None,
                messages=claude_messages,
                tools=[_tool_param(emit)],
                tool_choice={"type": "tool", "name": EMIT_TOOL},
            )
            usage = _usage_from(resp, self._model)
            for block in resp.content:
                if block.type == "tool_use":
                    return AdapterResponse(parsed=dict(block.input), usage=usage)
            return AdapterResponse(parsed={}, usage=usage)

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_output_tokens,
            "system": system or None,
            "messages": claude_messages,
        }
        if tools:
            kwargs["tools"] = [_tool_param(t) for t in tools]
        resp = await self._client.messages.create(**kwargs)  # type: ignore[call-overload]

        text_parts: list[str] = []
        calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
        return AdapterResponse(
            text="\n".join(text_parts) or None, tool_calls=calls, usage=_usage_from(resp, self._model)
        )


__all__ = ["ClaudeAdapter"]
