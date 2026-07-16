"""Durability, provenance, and extractor-version boundary regressions."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.ingestion_tool_data import job_data
from canvus_mcp.ingestion_types import UnitSpec


def _job(store: IngestionStore, cache: AssetCache, source: Path, version: str = "local-v1") -> int:
    job = IngestionPipeline(store=store, cache=cache).enqueue_file(
        canvas_id="canvas-a", source_ref="pdf:first", path=source,
        expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        mime_type="text/plain", extractor_version=version,
    )
    return job.id


def test_expired_crash_attempts_terminalize_at_the_configured_poison_limit(tmp_path: Path) -> None:
    now = [0.0]
    store = IngestionStore(tmp_path / "ingestion.db", clock=lambda: now[0])
    cache = AssetCache(tmp_path / "cache")
    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    try:
        job_id = _job(store, cache, source)
        for attempt in range(3):
            claim = store.claim_next("worker", lease_seconds=1)
            assert claim is not None
            now[0] += 2
            assert store.recover_expired_leases(max_attempts=3) == 1
            status = store.conn.execute("SELECT status FROM units WHERE id=?", (claim.unit_id,)).fetchone()[0]
            assert status == ("poison" if attempt == 2 else "retry")
        assert store.get_job(job_id).status.value == "failed"
    finally:
        cache.close()
        store.close()


def test_exact_source_provenance_is_available_for_same_canvas_deduplicated_jobs(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    cache = AssetCache(tmp_path / "cache")
    source = tmp_path / "source.txt"
    source.write_text("same bytes", encoding="utf-8")
    try:
        first = _job(store, cache, source)
        pipeline = IngestionPipeline(store=store, cache=cache)
        second = pipeline.enqueue_file(
            canvas_id="canvas-a", source_ref="pdf:second", path=source,
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            mime_type="text/plain", extractor_version="local-v1",
        )
        assert second.id == first
        assert store.source_for_job(first, canvas_id="canvas-a", source_ref="pdf:second") == ("pdf:second", "unknown")
    finally:
        cache.close()
        store.close()


def test_job_responses_keep_enqueue_source_exact_and_status_sources_explicit(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    cache = AssetCache(tmp_path / "cache")
    source = tmp_path / "source.txt"
    source.write_text("same bytes", encoding="utf-8")
    try:
        job_id = _job(store, cache, source)
        pipeline = IngestionPipeline(store=store, cache=cache)
        job = pipeline.enqueue_file(
            canvas_id="canvas-a", source_ref="pdf:second", path=source,
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            mime_type="text/plain", extractor_version="local-v1",
        )
        assert job.id == job_id
        assert job_data(store, job, "canvas-a", source_ref="pdf:second")["source_id"] == "second"
        status = job_data(store, job, "canvas-a")
        assert "source_id" not in status
        assert [item["source_id"] for item in status["sources"]] == ["first", "second"]
    finally:
        cache.close()
        store.close()


def test_extractor_version_rejects_unbounded_or_unsafe_values_at_store_boundary(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    digest = "a" * 64
    try:
        store.upsert_asset(digest, size_bytes=1, mime_type="text/plain")
        with pytest.raises(ValueError, match="invalid job request"):
            store.create_job(digest, "x" * 33, "text/plain", [UnitSpec("whole", 0)])
        with pytest.raises(ValueError, match="invalid job request"):
            store.create_job(digest, "unsafe value", "text/plain", [UnitSpec("whole", 0)])
    finally:
        store.close()


def test_extractor_version_database_guard_rejects_unsafe_direct_job_insert(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    digest = "a" * 64
    try:
        store.upsert_asset(digest, size_bytes=1, mime_type="text/plain")
        with pytest.raises(Exception):
            store.conn.execute(
                "INSERT INTO jobs(asset_sha256, extractor_version, mime_type, status, created_at, updated_at) "
                "VALUES (?, 'unsafe value', 'text/plain', 'queued', 0, 0)", (digest,)
            )
    finally:
        store.close()
