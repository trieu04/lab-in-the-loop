"""Content-addressed cache and idempotent enqueue tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from canvus_mcp.ingestion_cache import AssetCache, CacheIntegrityError
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore


def test_cache_is_content_addressed_and_rejects_mismatch_or_symlink(tmp_path: Path) -> None:
    cache = AssetCache(tmp_path / "cache")
    sha = cache.store_bytes(b"hello")
    assert sha == hashlib.sha256(b"hello").hexdigest()
    assert cache.read(sha) == b"hello"

    file = tmp_path / "mutable.txt"
    file.write_bytes(b"other")
    with pytest.raises(CacheIntegrityError):
        cache.import_file(file, expected_sha256=sha)
    link = tmp_path / "link.txt"
    link.symlink_to(file)
    with pytest.raises(CacheIntegrityError):
        cache.import_file(link, expected_sha256=hashlib.sha256(b"other").hexdigest())


def test_enqueue_deduplicates_bytes_but_preserves_canvas_sources(tmp_path: Path) -> None:
    cache = AssetCache(tmp_path / "cache")
    store = IngestionStore(tmp_path / "ingestion.db")
    pipeline = IngestionPipeline(store=store, cache=cache)
    source = tmp_path / "report.txt"
    source.write_text("bounded evidence")
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    try:
        first = pipeline.enqueue_file(
            canvas_id="canvas-a", source_ref="widget-a", path=source, expected_sha256=sha,
            mime_type="text/plain", extractor_version="text-v1",
        )
        second = pipeline.enqueue_file(
            canvas_id="canvas-b", source_ref="widget-b", path=source, expected_sha256=sha,
            mime_type="text/plain", extractor_version="text-v1",
        )
        changed = pipeline.enqueue_file(
            canvas_id="canvas-a", source_ref="widget-a", path=source, expected_sha256=sha,
            mime_type="text/plain", extractor_version="text-v2",
        )
        assert first.id == second.id
        assert changed.id != first.id
        assert store.can_read(first.id, canvas_id="canvas-a", source_ref="widget-a")
        assert store.can_read(first.id, canvas_id="canvas-b", source_ref="widget-b")
        assert not store.can_read(first.id, canvas_id="canvas-a", source_ref="widget-b")
    finally:
        store.close()
