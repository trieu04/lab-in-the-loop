"""Approved provider endpoint defaults and validation."""

from __future__ import annotations

import re
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlparse

CANONICAL_PROVIDER_ENDPOINTS = {
    "openai": "https://api.openai.com/v1",
    "claude": "https://api.anthropic.com",
}
_API_KEY_FIELDS = {"openai": "openai_api_key", "claude": "anthropic_api_key"}
_HOST_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


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


def _is_valid_hostname(hostname: str) -> bool:
    try:
        ip_address(hostname)
        return True
    except ValueError:
        labels = hostname.split(".")
        return len(hostname) <= 253 and all(_HOST_LABEL.fullmatch(label) for label in labels)


def is_valid_provider_endpoint(value: str | None, *, provider: str | None = None) -> bool:
    """Accept hosted HTTP(S) OpenAI URLs and HTTPS URLs for other providers."""
    if not value or any(character.isspace() for character in value):
        return False
    if "\\" in value or _INVALID_PERCENT_ESCAPE.search(value):
        return False
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    allowed_schemes = {"http", "https"} if provider == "openai" else {"https"}
    return (
        parsed.scheme in allowed_schemes
        and hostname is not None
        and not parsed.netloc.endswith(":")
        and _is_valid_hostname(hostname)
    )


__all__ = [
    "CANONICAL_PROVIDER_ENDPOINTS",
    "default_provider_endpoints",
    "is_valid_provider_endpoint",
]
