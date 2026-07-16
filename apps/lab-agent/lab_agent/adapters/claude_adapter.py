"""Anthropic / Claude model adapter.

Maps the normalized transcript onto the Messages API: system messages become
the top-level ``system`` string, tool calls become ``tool_use`` content
blocks, and tool results become ``tool_result`` blocks folded into a user
turn (consecutive tool messages merge into one user turn, as the API
requires). Structured output forces a call to a synthetic ``emit_result``
tool, matching the OpenAI adapter's behaviour.
"""

from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic

from lab_agent.adapters.base import AdapterResponse, Message, ToolCall, ToolSpec

EMIT_TOOL = "emit_result"
_MAX_TOKENS = 4096


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


class ClaudeAdapter:
    """Adapter over :class:`anthropic.AsyncAnthropic`."""

    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "result",
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
                max_tokens=_MAX_TOKENS,
                system=system or None,
                messages=claude_messages,
                tools=[_tool_param(emit)],
                tool_choice={"type": "tool", "name": EMIT_TOOL},
            )
            for block in resp.content:
                if block.type == "tool_use":
                    return AdapterResponse(parsed=dict(block.input))
            return AdapterResponse(parsed={})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": _MAX_TOKENS,
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
        return AdapterResponse(text="\n".join(text_parts) or None, tool_calls=calls)


__all__ = ["ClaudeAdapter"]
