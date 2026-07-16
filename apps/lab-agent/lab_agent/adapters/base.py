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

from lab_agent.models.governance import Usage

Message = dict[str, Any]


def normalize_usage(
    *,
    provider: str,
    model: str,
    request_id: str | None,
    prompt_tokens: Any,
    completion_tokens: Any,
    total_tokens: Any = None,
) -> Usage:
    """Build normalized usage from provider counts, failing closed when counts are absent."""
    if prompt_tokens is None or completion_tokens is None:
        return Usage.unavailable(provider, model, request_id=request_id)

    prompt = int(prompt_tokens)
    completion = int(completion_tokens)
    total = int(total_tokens) if total_tokens else prompt + completion
    return Usage(
        provider=provider,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        request_id=request_id,
    )


class ProviderCallError(RuntimeError):
    """Provider-neutral error that preserves SDK request metadata."""

    def __init__(self, message: str, request_id: str | None = None) -> None:
        super().__init__(message)
        self.request_id = request_id


class DeterministicProviderError(ProviderCallError):
    """Invalid request, authentication, authorization, or configuration failure."""


class TransientProviderError(ProviderCallError):
    """Known-not-dispatched transient failure, such as a rate-limit response."""


class AmbiguousProviderError(ProviderCallError):
    """The provider may have accepted the request; never blindly resubmit it."""


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
    """The normalized result of one model turn.

    ``usage`` carries provider-neutral token counts and request metadata so
    cost governance never has to read a provider-native usage object (UC §10,
    NFR-LITL-009). It is ``None`` only for adapters/fakes that predate usage
    capture; real adapters always populate it (with
    :meth:`Usage.unavailable` when the provider omits counts).
    """

    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    parsed: dict[str, Any] | None = None
    usage: Usage | None = None

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


__all__ = [
    "AdapterResponse", "AmbiguousProviderError", "DeterministicProviderError", "Message",
    "ModelAdapter", "ProviderCallError", "ToolCall", "ToolSpec", "TransientProviderError", "Usage",
    "normalize_usage",
]
