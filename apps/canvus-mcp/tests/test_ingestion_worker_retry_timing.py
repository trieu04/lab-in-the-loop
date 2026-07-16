"""Retry timing and terminal poison state are durable worker contracts."""

from __future__ import annotations

import hashlib
from pathlib import Path

from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.ingestion_worker import IngestionWorker


async def test_transient_failure_waits_for_backoff_then_becomes_sanitized_terminal_poison(tmp_path: Path) -> None:
    now = [10.0]
    source = tmp_path / "bad.pdf"
    source.write_bytes(b"not a pdf")
    store = IngestionStore(tmp_path / "ingestion.db", clock=lambda: now[0])
    pipeline = IngestionPipeline(store=store, cache=AssetCache(tmp_path / "cache"))
    job = pipeline.enqueue_file(
        canvas_id="canvas-a",
        source_ref="pdf:source-a",
        path=source,
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        mime_type="application/pdf",
        extractor_version="pdf-v1",
    )
    worker = IngestionWorker(
        pipeline,
        owner="worker",
        lease_seconds=30,
        max_attempts=2,
        base_backoff_seconds=5,
        rng=lambda: 0.0,
    )
    try:
        assert await worker.run_once()
        now[0] = 14.99
        assert not await worker.run_once()
        now[0] = 15.0
        assert await worker.run_once()
        unit = store.conn.execute("SELECT status, failure_code FROM units WHERE job_id=?", (job.id,)).fetchone()
        assert store.get_job(job.id).status.value == "failed"
        assert tuple(unit) == ("poison", "malformed")
    finally:
        store.close()
