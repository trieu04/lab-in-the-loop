"""Approved provider endpoint defaults and validation."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

CANONICAL_PROVIDER_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "claude": "https://api.anthropic.com",
}
_API_KEY_FIELDS = {"openai": "openai_api_key", "claude": "anthropic_api_key"}


def default_provider_endpoints(validated_data: dict[str, Any]) -> dict[str, str]:
    """Resolve legacy OpenAI overrides or canonical API-key provider endpoints."""
    endpoints = {
        provider: endpoint
        for provider, endpoint in CANONICAL_PROVIDER_ENDPOINTS.items()
        if validated_data.get(_API_KEY_FIELDS[provider])
    }
    if base_url := validated_data.get("openai_base_url"):
        endpoints["openai"] = str(base_url)
    return endpoints


def is_valid_provider_endpoint(value: str | None) -> bool:
    """Accept only explicit HTTPS endpoint URLs with a host."""
    parsed = urlparse(value or "")
    return parsed.scheme == "https" and parsed.hostname is not None


__all__ = [
    "CANONICAL_PROVIDER_ENDPOINTS",
    "default_provider_endpoints",
    "is_valid_provider_endpoint",
]
