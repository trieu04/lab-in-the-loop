"""Provider-agnostic model-adapter interface (UC §10).

The orchestrator and tool-use loop depend only on :class:`ModelAdapter`, so a
new provider (Ollama, vLLM, internal) is a drop-in that never touches the
loop. Conversations are passed as a normalized transcript of plain dicts; each
adapter converts that to its native wire format on every call.

Normalized message shapes (list of dicts):
- ``{"role": "system",    "content": str}``
- ``{"role": "user",      "content": str}``
- ``{"role": "assistant", "content": str | None, "tool_calls": [ToolCall-as-dict]}``
- ``{"role": "tool",      "tool_call_id": str, "name": str, "content": str}``

A ``ToolCall`` dict is ``{"id": str, "name": str, "arguments": dict}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

Message = dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    """A tool offered to the model: name, description, JSON-Schema parameters."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation the model requested."""

    id: str
    name: str
    arguments: dict[str, Any]

    def as_message_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass
class AdapterResponse:
    """The normalized result of one model turn."""

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    parsed: dict[str, Any] | None = None

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


@runtime_checkable
class ModelAdapter(Protocol):
    """One model provider behind a uniform async interface."""

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        response_schema: dict[str, Any] | None = None,
        schema_name: str = "result",
    ) -> AdapterResponse:
        """Run one model turn.

        - With ``response_schema``: force a structured emit and return
          :attr:`AdapterResponse.parsed` (a dict matching the schema).
        - With ``tools`` (and no schema): allow tool calls; return text and/or
          :attr:`AdapterResponse.tool_calls`.
        - With neither: return plain :attr:`AdapterResponse.text`.
        """
        ...


__all__ = ["AdapterResponse", "Message", "ModelAdapter", "ToolCall", "ToolSpec"]
