"""Final fail-closed validation before a provider request is recorded or sent."""

from __future__ import annotations

from lab_agent.adapters.base import Message
from lab_agent.config import Settings
from lab_agent.result_safety import ResultLimits, encoded_size, is_unsafe_data, sanitize_text

MODEL_INPUT_UNAVAILABLE = "model_input_unavailable"


def model_text_limits(settings: Settings) -> ResultLimits:
    """Map configured MCP text bounds to final provider-input validation."""
    return ResultLimits(
        settings.mcp_result_max_depth, settings.mcp_result_max_containers,
        settings.mcp_result_max_string_chars, settings.mcp_result_max_bytes,
        settings.mcp_result_max_items,
    )


def model_argument_limits(settings: Settings) -> ResultLimits:
    """Map configured argument bounds to the final provider-input validation."""
    return ResultLimits(
        settings.mcp_result_max_depth, settings.mcp_result_max_containers,
        min(settings.mcp_result_max_string_chars, settings.mcp_argument_max_bytes),
        settings.mcp_argument_max_bytes, settings.mcp_argument_max_items,
    )


class ModelInputSafetyError(RuntimeError):
    """A provider-visible transcript contains unsafe or oversized source text."""

    def __init__(self) -> None:
        super().__init__(MODEL_INPUT_UNAVAILABLE)


def require_safe_transcript(
    messages: list[Message], *, text_limits: ResultLimits, argument_limits: ResultLimits, max_bytes: int,
) -> None:
    """Reject unsafe text or aggregate transcript overflow before intent creation."""
    if encoded_size(messages) > max_bytes:
        raise ModelInputSafetyError()
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and sanitize_text(content, text_limits) is None:
            raise ModelInputSafetyError()
        calls = message.get("tool_calls")
        if isinstance(calls, list) and any(
            not isinstance(call, dict) or is_unsafe_data(call.get("arguments", {}), argument_limits)
            for call in calls
        ):
            raise ModelInputSafetyError()
