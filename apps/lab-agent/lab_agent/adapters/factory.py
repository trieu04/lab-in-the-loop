"""Construct :class:`ModelAdapter` instances from settings (UC §10 model swap).

``get_adapter`` builds one provider (the legacy single-provider path);
``build_provider_adapter`` builds a named provider so task-stage routing can
hold several at once (:func:`get_adapters`). The factory stays the sole adapter
constructor -- policy selects *which* provider, never how one is built.
"""

from __future__ import annotations

from lab_agent.adapters.base import ModelAdapter
from lab_agent.config import Settings, get_settings
from lab_agent.provider_endpoints import is_valid_provider_endpoint


def build_provider_adapter(provider: str, settings: Settings) -> ModelAdapter:
    """Build the adapter for an explicit ``provider`` (raises if misconfigured)."""
    endpoint = settings.provider_endpoints.get(provider)
    if not is_valid_provider_endpoint(endpoint):
        raise ValueError(f"LAB_AGENT_PROVIDER_ENDPOINTS requires an HTTPS endpoint for {provider!r}")
    if provider == "openai":
        from lab_agent.adapters.openai_adapter import OpenAIAdapter

        if not settings.openai_api_key and not settings.openai_base_url:
            raise ValueError("LAB_AGENT_OPENAI_API_KEY (or _BASE_URL) required for provider 'openai'")
        return OpenAIAdapter(
            api_key=settings.openai_api_key or "not-needed",
            model=settings.openai_model,
            base_url=endpoint,
            max_output_tokens=settings.model_max_output_tokens,
        )

    if provider == "claude":
        from lab_agent.adapters.claude_adapter import ClaudeAdapter

        if not settings.anthropic_api_key:
            raise ValueError("LAB_AGENT_ANTHROPIC_API_KEY required for provider 'claude'")
        return ClaudeAdapter(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            base_url=endpoint,
            max_output_tokens=settings.model_max_output_tokens,
        )

    raise ValueError(f"unknown provider: {provider!r}")


def get_adapter(settings: Settings | None = None) -> ModelAdapter:
    """Build the adapter named by ``settings.model_provider``."""
    settings = settings or get_settings()
    return build_provider_adapter(settings.model_provider, settings)


def _routed_providers(settings: Settings) -> set[str]:
    providers: set[str] = {settings.model_provider}
    for preferences in settings.routing_table.values():
        providers.update(preferences)
    return providers


def get_adapters(settings: Settings | None = None) -> dict[str, ModelAdapter]:
    """Build every provider the routing table (plus the default) may select.

    The default provider is required and raised on if unbuildable; other routed
    providers that are not yet configured are skipped, so routing fails closed on
    them only when a stage actually prefers one (rather than at startup).
    """
    settings = settings or get_settings()
    adapters: dict[str, ModelAdapter] = {
        settings.model_provider: build_provider_adapter(settings.model_provider, settings)
    }
    for provider in _routed_providers(settings):
        if provider in adapters:
            continue
        try:
            adapters[provider] = build_provider_adapter(provider, settings)
        except ValueError:
            continue
    return adapters


__all__ = ["build_provider_adapter", "get_adapter", "get_adapters"]
