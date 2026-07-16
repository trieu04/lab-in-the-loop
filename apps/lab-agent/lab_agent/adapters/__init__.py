"""Model-provider adapters (the agnosticism seam, UC §10)."""

from lab_agent.adapters.base import AdapterResponse, ModelAdapter, ToolCall, ToolSpec
from lab_agent.adapters.factory import get_adapter

__all__ = ["AdapterResponse", "ModelAdapter", "ToolCall", "ToolSpec", "get_adapter"]
