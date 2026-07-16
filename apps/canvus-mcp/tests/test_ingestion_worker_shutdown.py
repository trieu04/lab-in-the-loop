"""Graceful shutdown, bounded claims, and lease renewal worker regressions."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path

import pytest

from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.ingestion_types import UnitSpec
from canvus_mcp.ingestion_worker import IngestionWorker


async def test_stop_before_run_preserves_pending_stop_and_claims_nothing(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("evidence", encoding="utf-8")
    store = IngestionStore(tmp_path / "ingestion.db")
    pipeline = IngestionPipeline(store=store, cache=AssetCache(tmp_path / "cache"))
    try:
        pipeline.enqueue_file(
            canvas_id="canvas-a", source_ref="source-a", path=source,
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            mime_type="text/plain", extractor_version="v1",
        )
        worker = IngestionWorker(pipeline, owner="worker")
        worker.stop()
        await worker.run()
        assert store.conn.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    finally:
        store.close()


async def test_stop_drains_claimed_work_without_leaving_an_orphaned_lease(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("evidence", encoding="utf-8")
    store = IngestionStore(tmp_path / "ingestion.db")
    pipeline = IngestionPipeline(store=store, cache=AssetCache(tmp_path / "cache"))
    job = pipeline.enqueue_file(
        canvas_id="canvas-a", source_ref="pdf:source-a", path=source,
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        mime_type="text/plain", extractor_version="text-v1",
    )
    started, release = threading.Event(), threading.Event()
    original_extract = pipeline.extract_claim

    def blocked_extract(claim):
        started.set()
        assert release.wait(timeout=1)
        return original_extract(claim)

    monkeypatch.setattr(pipeline, "extract_claim", blocked_extract)
    worker = IngestionWorker(pipeline, owner="worker", concurrency=1, lease_seconds=30)
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
        worker.stop()
        release.set()
        await asyncio.wait_for(task, timeout=1)
        assert store.get_job(job.id).status.value == "completed"
        assert store.conn.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0
    finally:
        release.set()
        if not task.done():
            worker.stop()
            await task
        store.close()


async def test_stop_never_claims_above_concurrency_and_leaves_unclaimed_work_recoverable(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("evidence", encoding="utf-8")
    store = IngestionStore(tmp_path / "ingestion.db")
    pipeline = IngestionPipeline(
        store=store,
        cache=AssetCache(tmp_path / "cache"),
        planner=lambda _data, _mime: [UnitSpec("line", ordinal) for ordinal in range(3)],
    )
    job = pipeline.enqueue_file(
        canvas_id="canvas-a", source_ref="pdf:source-a", path=source,
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        mime_type="text/plain", extractor_version="text-v1",
    )
    started, release = threading.Event(), threading.Event()
    running = [0]
    lock = threading.Lock()
    original_extract = pipeline.extract_claim

    def blocked_extract(claim):
        with lock:
            running[0] += 1
            if running[0] == 2:
                started.set()
        assert release.wait(timeout=1)
        return original_extract(claim)

    monkeypatch.setattr(pipeline, "extract_claim", blocked_extract)
    worker = IngestionWorker(pipeline, owner="worker", concurrency=2, lease_seconds=30)
    task = asyncio.create_task(worker.run())
    try:
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
        assert store.conn.execute("SELECT COUNT(*) FROM units WHERE status='leased'").fetchone()[0] == 2
        worker.stop()
        release.set()
        await asyncio.wait_for(task, timeout=1)
        assert store.get_job(job.id).completed_units == 2
        assert store.conn.execute("SELECT COUNT(*) FROM units WHERE status='planned'").fetchone()[0] == 1
    finally:
        release.set()
        if not task.done():
            worker.stop()
            await task
        store.close()


async def test_cancellation_drains_shielded_extraction_before_returning(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("evidence", encoding="utf-8")
    store = IngestionStore(tmp_path / "ingestion.db")
    pipeline = IngestionPipeline(store=store, cache=AssetCache(tmp_path / "cache"))
    pipeline.enqueue_file(
        canvas_id="canvas-a", source_ref="pdf:source-a", path=source,
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        mime_type="text/plain", extractor_version="text-v1",
    )
    started, release = threading.Event(), threading.Event()
    original_extract = pipeline.extract_claim

    def blocked_extract(claim):
        started.set()
        assert release.wait(timeout=1)
        return original_extract(claim)

    monkeypatch.setattr(pipeline, "extract_claim", blocked_extract)
    task = asyncio.create_task(IngestionWorker(pipeline, owner="worker").run_once())
    try:
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        if not task.done():
            await task
        pipeline.cache.close()
        store.close()


async def test_long_running_extraction_renews_its_lease_before_commit(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("evidence", encoding="utf-8")
    store = IngestionStore(tmp_path / "ingestion.db")
    pipeline = IngestionPipeline(store=store, cache=AssetCache(tmp_path / "cache"))
    pipeline.enqueue_file(
        canvas_id="canvas-a", source_ref="pdf:source-a", path=source,
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        mime_type="text/plain", extractor_version="text-v1",
    )
    renewed, release = threading.Event(), threading.Event()
    original_extract, original_renew = pipeline.extract_claim, store.renew_lease

    def blocked_extract(claim):
        assert release.wait(timeout=1)
        return original_extract(claim)

    def record_renew(claim, *, lease_seconds):
        renewed.set()
        return original_renew(claim, lease_seconds=lease_seconds)

    monkeypatch.setattr(pipeline, "extract_claim", blocked_extract)
    monkeypatch.setattr(store, "renew_lease", record_renew)
    worker = IngestionWorker(pipeline, owner="worker", lease_seconds=0.3)
    task = asyncio.create_task(worker.run_once())
    try:
        await asyncio.wait_for(asyncio.to_thread(renewed.wait), timeout=1)
        release.set()
        assert await asyncio.wait_for(task, timeout=1)
        assert store.conn.execute("SELECT COUNT(*) FROM leases").fetchone()[0] == 0
    finally:
        release.set()
        if not task.done():
            await task
        store.close()
