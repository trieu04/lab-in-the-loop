"""Concurrent immutable-content enqueue must converge on one cache entry and job."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore


def test_concurrent_same_version_enqueue_reuses_one_asset_cache_and_job(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("bounded evidence", encoding="utf-8")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    db_path, cache_path = tmp_path / "ingestion.db", tmp_path / "cache"
    bootstrap = IngestionStore(db_path)
    bootstrap.close()
    barrier = Barrier(2)

    def enqueue(canvas_id: str) -> int:
        store = IngestionStore(db_path)
        try:
            pipeline = IngestionPipeline(store=store, cache=AssetCache(cache_path))
            barrier.wait(timeout=2)
            return pipeline.enqueue_file(
                canvas_id=canvas_id,
                source_ref="pdf:source",
                path=source,
                expected_sha256=digest,
                mime_type="text/plain",
                extractor_version="text-v1",
            ).id
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        job_ids = list(pool.map(enqueue, ("canvas-a", "canvas-b")))

    verify = IngestionStore(db_path)
    try:
        assert job_ids[0] == job_ids[1]
        assert verify.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
        assert verify.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert len(list(cache_path.glob("**/" + digest))) == 1
    finally:
        verify.close()
