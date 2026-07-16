"""Shared Canvus SDK client lifecycle and bounded binary streaming adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol, cast

from canvus_sdk import Client

from canvus_mcp.config import Settings


class StreamingUnavailableError(RuntimeError):
    """The configured SDK client cannot yield binary response chunks."""


class BinaryResponse(Protocol):
    """Minimal response contract needed by bounded content acquisition."""

    headers: Mapping[str, str]

    def aiter_bytes(self) -> AsyncIterator[bytes]: ...


class _RawResponse(BinaryResponse, Protocol):
    def raise_for_status(self) -> None: ...


class _RawClient(Protocol):
    def stream(
        self, method: str, path: str, *, headers: Mapping[str, str]
    ) -> AbstractAsyncContextManager[_RawResponse]: ...


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


@asynccontextmanager
async def stream_download(
    client: object, path: str, *, headers: Mapping[str, str] | None = None
) -> AsyncIterator[BinaryResponse]:
    """Yield a binary download response without allocating its whole body.

    A future SDK ``stream_download`` method is preferred.  The current local SDK
    lacks one, so its pooled HTTP client is used only here as an additive bridge.
    """
    request_headers = dict(headers or {})
    custom = getattr(client, "stream_download", None)
    if callable(custom):
        manager = cast(AbstractAsyncContextManager[BinaryResponse], custom(path, headers=request_headers))
        async with manager as response:
            yield response
        return
    transport = getattr(client, "_transport", None)
    raw = getattr(transport, "_client", None)
    if raw is None:
        raise StreamingUnavailableError("SDK binary streaming is unavailable")
    http = cast(_RawClient, raw)
    request_headers.setdefault("Accept", "*/*")
    async with http.stream("GET", path, headers=request_headers) as response:
        response.raise_for_status()
        yield response


async def close_client() -> None:
    """Close the shared client if one was created. Idempotent."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


__all__ = [
    "BinaryResponse",
    "StreamingUnavailableError",
    "close_client",
    "get_client",
    "get_settings",
    "stream_download",
]
