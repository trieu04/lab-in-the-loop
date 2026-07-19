"""Shared Canvus-only acquisition with bounded streaming for ingestion."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from canvus_mcp.client import BinaryResponse, stream_download
from canvus_mcp.downloads import SourceTooLargeError, normalize_mime, save_bytes, save_stream

_IDENTIFIER = re.compile(r"^(?=.{1,128}$)(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._:-]*$")


class SourceRequestError(ValueError):
    """A source is not a supported Canvus object identifier."""

    def __init__(self) -> None:
        super().__init__("invalid_source")


class CanvusContentDownloader:
    """Downloads Canvus sources while preserving content-tool response metadata."""

    def __init__(self, client: Any, *, output_dir: str | Path) -> None:
        self._client = client
        self._output_dir = output_dir

    async def acquire(
        self, canvas_id: str, source_kind: str, source_id: str, *, max_bytes: int | None = None
    ) -> dict[str, Any]:
        """Persist a source; bounded callers stream directly into a temp file."""
        if not _identifier(canvas_id) or not _identifier(source_id) or (max_bytes is not None and max_bytes < 1):
            raise SourceRequestError()
        if source_kind == "pdf":
            return await self._widget(canvas_id, source_id, "pdf", max_bytes)
        if source_kind == "image":
            return await self._widget(canvas_id, source_id, "image", max_bytes)
        if source_kind == "asset":
            return await self._asset(canvas_id, source_id, max_bytes)
        raise SourceRequestError()

    async def _widget(
        self, canvas_id: str, source_id: str, kind: str, max_bytes: int | None
    ) -> dict[str, Any]:
        resource = getattr(self._client.widgets, f"{kind}s")
        model = await resource.get(canvas_id, source_id)
        path = f"canvases/{canvas_id}/{kind}s/{source_id}/download"
        meta = await self._persist(
            path, {}, f"{kind}_{source_id}", _filename(model), _mime(model), max_bytes,
            legacy=lambda: resource.download(canvas_id, source_id),
        )
        meta.update({"canvas_id": canvas_id, "widget_id": source_id, "widget_type": kind.title()})
        return meta

    async def _asset(self, canvas_id: str, source_id: str, max_bytes: int | None) -> dict[str, Any]:
        meta = await self._persist(
            f"assets/{source_id}", {"canvas-id": canvas_id}, f"asset_{source_id[:16]}", "", "", max_bytes,
            legacy=lambda: self._client.assets.download_by_hash(source_id, canvas_id),
        )
        meta.update({"canvas_id": canvas_id, "asset_hash": source_id})
        return meta

    async def _persist(
        self, path: str, headers: dict[str, str], stem: str, filename: str, declared_mime: str,
        max_bytes: int | None, *, legacy: Any
    ) -> dict[str, Any]:
        if max_bytes is None:
            data = await legacy()
            return save_bytes(data, str(self._output_dir), stem=stem, filename=filename, declared_mime=declared_mime)
        async with stream_download(self._client, path, headers=headers) as response:
            _check_content_length(response, max_bytes)
            response_mime = _response_mime(response)
            return await save_stream(
                response.aiter_bytes(), str(self._output_dir), stem=stem, filename=filename,
                declared_mime=normalize_mime(declared_mime) or response_mime, max_bytes=max_bytes,
            )


def _identifier(value: object) -> bool:
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _response_mime(response: BinaryResponse) -> str:
    """Read a bounded validated Content-Type header without trusting its casing."""
    value = next((value for key, value in response.headers.items() if key.lower() == "content-type"), "")
    return normalize_mime(value)


def _check_content_length(response: BinaryResponse, max_bytes: int) -> None:
    value = next((v for key, v in response.headers.items() if key.lower() == "content-length"), None)
    if value is None:
        return
    try:
        content_length = int(value)
    except ValueError:
        return
    if content_length > max_bytes:
        raise SourceTooLargeError()


def _filename(model: Any) -> str:
    return str(getattr(model, "original_filename", "") or "")


def _mime(model: Any) -> str:
    return str(getattr(model, "mime_type", "") or "")


__all__ = ["CanvusContentDownloader", "SourceRequestError"]
