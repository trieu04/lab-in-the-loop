"""Recovery behavior of the bounded standalone local ingestion worker."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.ingestion_worker import IngestionWorker


@pytest.mark.asyncio
async def test_worker_completes_once_and_never_reruns_completed_unit(tmp_path: Path) -> None:
    now = [10.0]
    source = tmp_path / "source.txt"
    source.write_text("evidence")
    store = IngestionStore(tmp_path / "ingestion.db", clock=lambda: now[0])
    pipeline = IngestionPipeline(store=store, cache=AssetCache(tmp_path / "cache"))
    try:
        job = pipeline.enqueue_file(
            canvas_id="canvas", source_ref="source", path=source,
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            mime_type="text/plain", extractor_version="v1",
        )
        worker = IngestionWorker(pipeline, owner="test", concurrency=1, lease_seconds=30)
        assert await worker.run_once()
        assert not await worker.run_once()
        assert store.get_job(job.id).status == "completed"
        assert [chunk.text for chunk in store.read_chunks(job.id, canvas_id="canvas", source_ref="source")] == ["evidence"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_worker_retries_then_poison_units_after_bounded_attempts(tmp_path: Path) -> None:
    now = [10.0]
    source = tmp_path / "bad.pdf"
    source.write_bytes(b"not a pdf")
    store = IngestionStore(tmp_path / "ingestion.db", clock=lambda: now[0])
    pipeline = IngestionPipeline(store=store, cache=AssetCache(tmp_path / "cache"))
    try:
        job = pipeline.enqueue_file(
            canvas_id="canvas", source_ref="source", path=source,
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            mime_type="application/pdf", extractor_version="v1",
        )
        worker = IngestionWorker(pipeline, owner="test", lease_seconds=30, max_attempts=2, rng=lambda: 0.0)
        assert await worker.run_once()
        assert store.get_job(job.id).status == "queued"
        now[0] = 12.0
        assert await worker.run_once()
        assert store.get_job(job.id).status == "failed"
    finally:
        store.close()
