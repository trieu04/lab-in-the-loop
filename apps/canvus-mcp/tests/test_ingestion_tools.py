"""MCP ingestion-tool registration, authorization, and bounded-read tests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from canvus_mcp.access_control import AccessDenied, AccessPolicy, Role
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
class _Request:
    headers: list[tuple[str, str]]


@dataclass
class _RequestContext:
    request: _Request | None


@dataclass
class _Context:
    request_context: _RequestContext


def _context(token: str | None) -> _Context:
    headers = [] if token is None else [("authorization", f"Bearer {token}")]
    return _Context(_RequestContext(_Request(headers)))


def _policy(store: IngestionStore) -> AccessPolicy:
    return AccessPolicy(
        store=store,
        reader_token=SecretStr("reader-token"),
        trusted_service_token=SecretStr("service-token"),
        operator_token=SecretStr("operator-token"),
        reader_canvases=("canvas-a",),
        trusted_service_canvases=("canvas-a",),
        operator_canvases=("canvas-a",),
        stdio_role=Role.READER,
        stdio_canvases=("canvas-a",),
    )


@pytest.fixture
def toolset(tmp_path: Path) -> tuple[dict[str, Any], IngestionStore]:
    store = IngestionStore(tmp_path / "ingestion.db")
    pipeline = IngestionPipeline(store=store, cache=AssetCache(tmp_path / "cache"))
    mcp = _MCP()
    ingestion.register(mcp, policy=_policy(store), pipeline=pipeline, max_chunk_chars=80)
    try:
        yield mcp.tools, store
    finally:
        store.close()


def _seed(store: IngestionStore, tmp_path: Path) -> int:
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    job = IngestionPipeline(store=store, cache=AssetCache(tmp_path / "seed-cache")).enqueue_file(
        canvas_id="canvas-a",
        source_ref="pdf:source-1",
        path=source,
        expected_sha256=digest,
        mime_type="text/plain",
        extractor_version="text-v1",
        classification="restricted",
    )
    claim = store.claim_next("test", lease_seconds=30)
    assert claim is not None
    store.complete_unit(claim, [(0, "first chunk", {}), (1, "second chunk", {})])
    return job.id


def test_registers_exact_ingestion_tool_names(toolset: tuple[dict[str, Any], IngestionStore]) -> None:
    tools, _ = toolset
    assert {"enqueue_ingestion", "get_ingestion_status", "read_ingestion_chunks", "retry_ingestion", "cancel_ingestion"} <= set(tools)


async def test_ingestion_reads_require_bearer_and_hide_cross_canvas_existence(
    toolset: tuple[dict[str, Any], IngestionStore], tmp_path: Path
) -> None:
    tools, store = toolset
    job_id = _seed(store, tmp_path)
    with pytest.raises(AccessDenied, match="access_denied"):
        await tools["get_ingestion_status"]("canvas-a", job_id, _context(None))
    with pytest.raises(AccessDenied, match="access_denied"):
        await tools["get_ingestion_status"]("canvas-b", job_id, _context("reader-token"))
    result = await tools["get_ingestion_status"]("canvas-a", job_id, _context("reader-token"))
    assert result["data_classification"] == "restricted"
    assert "path" not in repr(result).lower()


async def test_chunk_reads_are_bounded_and_do_not_mutate_job(
    toolset: tuple[dict[str, Any], IngestionStore], tmp_path: Path
) -> None:
    tools, store = toolset
    job_id = _seed(store, tmp_path)
    before = tuple(store.conn.execute("SELECT status, updated_at FROM jobs WHERE id=?", (job_id,)).fetchone())
    page = await tools["read_ingestion_chunks"]("canvas-a", job_id, -1, 1, 80, _context("reader-token"))
    after = tuple(store.conn.execute("SELECT status, updated_at FROM jobs WHERE id=?", (job_id,)).fetchone())
    assert before == after
    assert page["chunks"] == [{"ordinal": 0, "text": "first chunk", "metadata": {}}]
    assert page["next_ordinal"] == 0
    assert page["has_more"] is True
    assert len(str(page)) <= 80 + 250


async def test_operator_only_retry_and_cancel_are_audited_without_raw_arguments(
    toolset: tuple[dict[str, Any], IngestionStore], tmp_path: Path
) -> None:
    tools, store = toolset
    job_id = _seed(store, tmp_path)
    with pytest.raises(AccessDenied, match="access_denied"):
        await tools["cancel_ingestion"]("canvas-a", job_id, "super-secret raw reason", _context("service-token"))
    audit = store.conn.execute("SELECT * FROM authorization_audit ORDER BY id DESC LIMIT 1").fetchone()
    assert audit is not None
    assert audit["reason"] == "action_not_authorized"
    assert "super-secret" not in str(tuple(audit))
    cancelled = await tools["cancel_ingestion"]("canvas-a", job_id, "operator reason", _context("operator-token"))
    assert cancelled["status"] == "cancelled"
    retried = await tools["retry_ingestion"]("canvas-a", job_id, _context("operator-token"))
    assert retried["status"] == "completed"
