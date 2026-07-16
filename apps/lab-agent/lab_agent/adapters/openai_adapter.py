"""OpenAI (and OpenAI-compatible) model adapter.

Works against api.openai.com or any OpenAI-compatible endpoint via
``base_url`` (Ollama, vLLM, Azure). Structured output is obtained by forcing a
call to a synthetic ``emit_result`` tool whose parameters are the requested
JSON schema — this behaves uniformly with the Claude adapter and avoids
relying on ``response_format`` support.
"""

from __future__ import annotations

import json
from typing import Any

from openai import AsyncOpenAI

from lab_agent.adapters.base import AdapterResponse, Message, ToolCall, ToolSpec

EMIT_TOOL = "emit_result"


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


class OpenAIAdapter:
    """Adapter over :class:`openai.AsyncOpenAI`."""

    def __init__(self, api_key: str, model: str, base_url: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        self._model = model

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "result",
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
                tools=[_tool_param(emit)],
                tool_choice={"type": "function", "function": {"name": EMIT_TOOL}},
            )
            call = completion.choices[0].message.tool_calls[0]
            return AdapterResponse(parsed=json.loads(call.function.arguments or "{}"))

        kwargs: dict[str, Any] = {"model": self._model, "messages": oai_messages}
        if tools:
            kwargs["tools"] = [_tool_param(t) for t in tools]
        completion = await self._client.chat.completions.create(**kwargs)  # type: ignore[call-overload]
        choice = completion.choices[0].message
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=json.loads(c.function.arguments or "{}"))
            for c in (choice.tool_calls or [])
        ]
        return AdapterResponse(text=choice.content, tool_calls=calls)


__all__ = ["OpenAIAdapter"]
