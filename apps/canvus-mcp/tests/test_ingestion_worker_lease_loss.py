"""Lease-loss extraction drainage regression."""

from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path

from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.ingestion_worker import IngestionWorker


async def test_lease_loss_waits_for_existing_extraction_before_releasing_slot(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.txt"
    source.write_text("evidence", encoding="utf-8")
    store = IngestionStore(tmp_path / "ingestion.db")
    cache = AssetCache(tmp_path / "cache")
    pipeline = IngestionPipeline(store=store, cache=cache)
    pipeline.enqueue_file(
        canvas_id="canvas-a", source_ref="pdf:source-a", path=source,
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        mime_type="text/plain", extractor_version="text-v1",
    )
    started, release = threading.Event(), threading.Event()
    extract = pipeline.extract_claim

    def blocked(claim):
        started.set()
        assert release.wait(timeout=1)
        return extract(claim)

    monkeypatch.setattr(pipeline, "extract_claim", blocked)
    monkeypatch.setattr(store, "renew_lease", lambda *_args, **_kwargs: False)
    task = asyncio.create_task(IngestionWorker(pipeline, owner="worker", lease_seconds=0.1).run_once())
    try:
        await asyncio.wait_for(asyncio.to_thread(started.wait), timeout=1)
        await asyncio.sleep(0.1)
        assert not task.done()
        release.set()
        assert not await asyncio.wait_for(task, timeout=1)
    finally:
        release.set()
        if not task.done():
            await task
        cache.close()
        store.close()
