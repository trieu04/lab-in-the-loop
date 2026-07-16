"""Shared Canvus SDK client lifecycle for the MCP server.

A single long-lived :class:`canvus_sdk.Client` is built lazily from
:class:`~canvus_mcp.config.Settings` and reused across all tool calls. The
streamable-HTTP transport is long-running, so one pooled client is the right
shape; it is closed on shutdown via :func:`close_client`.
"""

from __future__ import annotations

from canvus_sdk import Client

from canvus_mcp.config import Settings

_client: Client | None = None
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return process-wide settings, constructed once from the environment."""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]  # validated via env
    return _settings


def get_client() -> Client:
    """Return the shared async Canvus client, building it on first use."""
    global _client
    if _client is None:
        cfg = get_settings()
        _client = Client(
            base_url=cfg.api_url,
            api_key=cfg.api_key,
            verify_ssl=cfg.verify_ssl,
        )
    return _client


async def close_client() -> None:
    """Close the shared client if one was created. Idempotent."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


__all__ = ["close_client", "get_client", "get_settings"]
