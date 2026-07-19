"""Canvas-scoped, mutation-free durable ingestion read tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore


def test_canvas_scoped_reads_expose_no_cross_canvas_job_existence(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    source = tmp_path / "report.txt"
    source.write_text("bounded source")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        job = IngestionPipeline(store=store, cache=AssetCache(tmp_path / "cache")).enqueue_file(
            canvas_id="canvas-a",
            source_ref="pdf:source-a",
            path=source,
            expected_sha256=digest,
            mime_type="text/plain",
            extractor_version="text-v1",
            classification=None,
        )
        before = store.conn.execute("SELECT status, updated_at FROM jobs WHERE id=?", (job.id,)).fetchone()
        assert store.get_job_for_canvas(job.id, canvas_id="canvas-a") is not None
        assert store.get_job_for_canvas(job.id, canvas_id="canvas-b") is None
        assert store.read_completed_chunks_for_canvas(job.id, canvas_id="canvas-a", after_ordinal=-1, limit=20) == []
        assert store.read_completed_chunks_for_canvas(job.id, canvas_id="canvas-b", after_ordinal=-1, limit=20) == []
        after = store.conn.execute("SELECT status, updated_at FROM jobs WHERE id=?", (job.id,)).fetchone()
        assert tuple(before) == tuple(after)
    finally:
        store.close()
