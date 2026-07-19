"""Lossless pagination regressions for completed ingestion chunks."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from canvus_mcp.access_control import AccessPolicy, Role
from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.tools import ingestion


class _MCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any):
        def register(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return register


@dataclass
class _Context:
    request_context: Any


def _context() -> _Context:
    request = type("Request", (), {"headers": [("authorization", "Bearer reader-token")]})()
    return _Context(type("RequestContext", (), {"request": request})())


def _policy(store: IngestionStore) -> AccessPolicy:
    return AccessPolicy(
        store=store, reader_token=SecretStr("reader-token"), trusted_service_token=None,
        operator_token=None, reader_canvases=("canvas-a",), trusted_service_canvases=(),
        operator_canvases=(), stdio_role=Role.READER, stdio_canvases=("canvas-a",),
    )


def _tools(tmp_path: Path) -> tuple[dict[str, Any], IngestionStore, AssetCache, int]:
    store = IngestionStore(tmp_path / "ingestion.db")
    cache = AssetCache(tmp_path / "cache")
    pipeline = IngestionPipeline(store=store, cache=cache)
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    job = pipeline.enqueue_file(
        canvas_id="canvas-a",
        source_ref="pdf:source-1",
        path=source,
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        mime_type="text/plain",
        extractor_version="text-v1",
        classification="restricted",
    )
    claim = store.claim_next("test", lease_seconds=30)
    assert claim is not None
    store.complete_unit(claim, [(0, "a complete chunk", {}), (1, "next chunk", {})])
    mcp = _MCP()
    policy = AccessPolicy(
        store=store,
        reader_token=SecretStr("reader-token"),
        trusted_service_token=None,
        operator_token=None,
        reader_canvases=("canvas-a",),
        trusted_service_canvases=(),
        operator_canvases=(),
        stdio_role=Role.READER,
        stdio_canvases=("canvas-a",),
    )
    ingestion.register(mcp, policy=policy, pipeline=pipeline, max_chunk_chars=80)
    return mcp.tools, store, cache, job.id


async def test_reduced_config_cap_allows_legacy_whole_chunk_read_up_to_hard_maximum(tmp_path: Path) -> None:
    tools, store, cache, job_id = _tools(tmp_path)
    try:
        store.conn.execute("UPDATE chunks SET text=? WHERE job_id=? AND ordinal=0", ("x" * 80, job_id))
        mcp = _MCP()
        pipeline = IngestionPipeline(store=store, cache=cache)
        ingestion.register(mcp, policy=_policy(store), pipeline=pipeline, max_chunk_chars=40)
        small = await mcp.tools["read_ingestion_chunks"]("canvas-a", job_id, ctx=_context())
        assert small["error"] == "max_chars_too_small"
        assert small["next_ordinal"] == -1
        whole = await mcp.tools["read_ingestion_chunks"]("canvas-a", job_id, -1, 1, 80, _context())
        assert whole["chunks"][0]["text"] == "x" * 80
        with pytest.raises(ValueError, match="invalid_pagination"):
            await mcp.tools["read_ingestion_chunks"]("canvas-a", job_id, -1, 1, 8001, _context())
    finally:
        store.close()
        cache.close()


async def test_small_max_chars_does_not_return_prefix_or_advance_cursor(tmp_path: Path) -> None:
    tools, store, cache, job_id = _tools(tmp_path)
    try:
        small = await tools["read_ingestion_chunks"]("canvas-a", job_id, -1, 1, 4, _context())
        assert small == {
            "job_id": job_id,
            "data_classification": "restricted",
            "error": "max_chars_too_small",
            "next_ordinal": -1,
            "required_chars": len("a complete chunk"),
        }
        retry = await tools["read_ingestion_chunks"]("canvas-a", job_id, -1, 1, 80, _context())
        assert retry["chunks"] == [{"ordinal": 0, "text": "a complete chunk", "metadata": {}}]
        assert retry["next_ordinal"] == 0
        assert retry["has_more"] is True
        default_page = await tools["read_ingestion_chunks"](
            "canvas-a", job_id, ctx=_context()
        )
        assert default_page["chunks"][0]["text"] == "a complete chunk"
    finally:
        store.close()
        cache.close()
