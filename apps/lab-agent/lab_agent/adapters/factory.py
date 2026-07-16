"""Select a :class:`ModelAdapter` from settings (UC §10 model swap)."""

from __future__ import annotations

from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings, get_settings


def get_adapter(settings: Settings | None = None) -> ModelAdapter:
    """Build the adapter named by ``settings.model_provider``."""
    settings = settings or get_settings()
    provider = settings.model_provider

    if provider == "openai":
        from lab_agent.adapters.openai_adapter import OpenAIAdapter

        if not settings.openai_api_key and not settings.openai_base_url:
            raise ValueError("LAB_AGENT_OPENAI_API_KEY (or _BASE_URL) required for provider 'openai'")
        return OpenAIAdapter(
            api_key=settings.openai_api_key or "not-needed",
            model=settings.openai_model,
            base_url=settings.openai_base_url,
        )

    if provider == "claude":
        from lab_agent.adapters.claude_adapter import ClaudeAdapter

        if not settings.anthropic_api_key:
            raise ValueError("LAB_AGENT_ANTHROPIC_API_KEY required for provider 'claude'")
        return ClaudeAdapter(api_key=settings.anthropic_api_key, model=settings.anthropic_model)

    raise ValueError(f"unknown model_provider: {provider!r}")


__all__ = ["get_adapter"]
