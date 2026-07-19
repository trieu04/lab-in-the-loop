"""Authorized, bounded MCP tools for durable Canvus content ingestion."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcp.server.fastmcp import Context

from canvus_mcp.access_control import AccessDenied, AccessPolicy
from canvus_mcp.client import get_client, get_settings
from canvus_mcp.content_download import CanvusContentDownloader
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.ingestion_tool_data import job_data
from canvus_mcp.ingestion_validation import is_extractor_version

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

_REASON = re.compile(r"[^A-Za-z0-9 .,_-]+")


def register(
    mcp: FastMCP,
    *,
    policy: AccessPolicy,
    pipeline: IngestionPipeline,
    max_chunk_chars: int,
    max_source_bytes: int = 10 * 1024 * 1024,
    downloader: CanvusContentDownloader | None = None,
    classification_for_canvas: Callable[[str], str] = lambda _canvas_id: "unknown",
) -> None:
    """Attach authenticated tools; download tools remain separately anonymous."""
    if max_chunk_chars < 1 or max_chunk_chars > 8000 or max_source_bytes < 1:
        raise ValueError("invalid ingestion bounds")
    store = pipeline.store
    download_service = downloader

    @mcp.tool()
    @policy.guarded("enqueue_ingestion")
    async def enqueue_ingestion(
        canvas_id: str,
        source_kind: str,
        source_id: str,
        extractor_version: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Download a Canvus source and idempotently create its durable ingestion job."""
        del ctx
        version = "local-v1" if extractor_version is None else extractor_version
        if not is_extractor_version(version):
            raise ValueError("invalid_extractor_version")
        acquisition = download_service or CanvusContentDownloader(
            get_client(), output_dir=get_settings().mcp_output_dir
        )
        meta = await acquisition.acquire(
            canvas_id, source_kind, source_id, max_bytes=max_source_bytes
        )
        if not isinstance(meta.get("size_bytes"), int) or meta["size_bytes"] > max_source_bytes:
            raise ValueError("source_too_large")
        job = pipeline.enqueue_file(
            canvas_id=canvas_id,
            source_ref=f"{source_kind}:{source_id}",
            path=_download_path(meta),
            expected_sha256=_sha(meta),
            mime_type=_mime(meta),
            extractor_version=version,
            classification=classification_for_canvas(canvas_id),
        )
        return job_data(store, job, canvas_id, source_ref=f"{source_kind}:{source_id}")

    @mcp.tool()
    @policy.guarded("get_ingestion_status")
    async def get_ingestion_status(canvas_id: str, job_id: int, ctx: Context | None = None) -> dict[str, Any]:
        """Read canvas-scoped durable progress without scheduling extraction work."""
        return _visible_job(policy, store, "get_ingestion_status", canvas_id, job_id, ctx)

    @mcp.tool()
    @policy.guarded("read_ingestion_chunks")
    async def read_ingestion_chunks(
        canvas_id: str,
        job_id: int,
        after_ordinal: int = -1,
        limit: int = 20,
        max_chars: int = max_chunk_chars,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Read completed chunks in deterministic pages without mutating job state."""
        if (
            after_ordinal < -1
            or not 1 <= limit <= 20
            or not 1 <= max_chars <= 8000
        ):
            raise ValueError("invalid_pagination")
        status = _visible_job(policy, store, "read_ingestion_chunks", canvas_id, job_id, ctx)
        rows = store.read_completed_chunks_for_canvas(
            job_id,
            canvas_id=canvas_id,
            after_ordinal=after_ordinal,
            limit=limit + 1,
        )
        chunks: list[dict[str, Any]] = []
        used = 0
        for chunk in rows[:limit]:
            required = len(chunk.text)
            if used + required > max_chars:
                if not chunks:
                    return {
                        "job_id": job_id,
                        "data_classification": status["data_classification"],
                        "error": "max_chars_too_small",
                        "next_ordinal": after_ordinal,
                        "required_chars": required,
                    }
                break
            chunks.append({"ordinal": chunk.ordinal, "text": chunk.text, "metadata": dict(chunk.metadata)})
            used += required
        next_ordinal = chunks[-1]["ordinal"] if chunks else after_ordinal
        has_more = len(rows) > len(chunks)
        return {
            "job_id": job_id,
            "data_classification": status["data_classification"],
            "chunks": chunks,
            "next_ordinal": next_ordinal,
            "has_more": has_more,
        }

    @mcp.tool()
    @policy.guarded("retry_ingestion")
    async def retry_ingestion(canvas_id: str, job_id: int, ctx: Context | None = None) -> dict[str, Any]:
        """Requeue unfinished terminal units; completed chunks remain immutable."""
        _visible_job(policy, store, "retry_ingestion", canvas_id, job_id, ctx)
        return job_data(store, store.retry_job(job_id), canvas_id)

    @mcp.tool()
    @policy.guarded("cancel_ingestion")
    async def cancel_ingestion(
        canvas_id: str, job_id: int, reason: str = "", ctx: Context | None = None
    ) -> dict[str, Any]:
        """Cancel unfinished extraction units using a bounded operator reason."""
        _visible_job(policy, store, "cancel_ingestion", canvas_id, job_id, ctx)
        store.cancel_job(job_id, reason=_reason(reason))
        return job_data(store, store.get_job(job_id), canvas_id)


def _visible_job(policy: AccessPolicy, store: IngestionStore, action: str, canvas_id: str, job_id: int, ctx: Context | None) -> dict[str, Any]:
    principal = policy.principal_from_context(ctx)
    if principal is None:
        raise AccessDenied()
    job = store.get_job_for_canvas(job_id, canvas_id=canvas_id)
    if job is None:
        policy.deny_resource(principal, action=action, canvas_id=canvas_id, job_id=job_id)
    return job_data(store, job, canvas_id)


def _download_path(meta: dict[str, Any]) -> Path:
    path = meta.get("path")
    if not isinstance(path, str):
        raise ValueError("invalid_source")
    return Path(path)


def _sha(meta: dict[str, Any]) -> str:
    value = meta.get("sha256")
    if not isinstance(value, str):
        raise ValueError("invalid_source")
    return value


def _mime(meta: dict[str, Any]) -> str:
    value = meta.get("mime_type")
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_source")
    return value


def _reason(value: str) -> str:
    return _REASON.sub("", value)[:160]


__all__ = ["register"]
