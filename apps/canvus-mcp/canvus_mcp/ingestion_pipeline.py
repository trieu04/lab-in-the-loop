"""Idempotent enqueue path from an immutable cache entry to durable work units."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from canvus_mcp.extractors import Extractor, LocalExtractor
from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.ingestion_types import ClaimedUnit, ExtractionResult, Job, UnitSpec

UnitPlanner = Callable[[bytes, str], Sequence[UnitSpec]]


class IngestionPipeline:
    """Records source provenance separately from globally deduplicated content."""

    def __init__(
        self,
        *,
        store: IngestionStore,
        cache: AssetCache,
        extractor: Extractor | None = None,
        planner: UnitPlanner | None = None,
    ) -> None:
        self.store = store
        self.cache = cache
        self.extractor = extractor or LocalExtractor()
        self.planner = planner or self.extractor.plan

    def enqueue_file(
        self,
        *,
        canvas_id: str,
        source_ref: str,
        path: Path,
        expected_sha256: str,
        mime_type: str,
        extractor_version: str,
        classification: str | None = None,
    ) -> Job:
        """Verify/copy a mutable download, then create or reuse its derived job.

        The input path is deliberately never persisted: future work identity is
        only the verified immutable content hash plus extractor version.
        """
        digest = self.cache.import_file(path, expected_sha256=expected_sha256)
        data = self.cache.read(digest)
        units = tuple(self.planner(data, mime_type))
        if not units:
            raise ValueError("unit planner returned no work")
        self.store.upsert_asset(digest, size_bytes=len(data), mime_type=mime_type)
        self.store.record_source(
            canvas_id=canvas_id,
            source_ref=source_ref,
            asset_sha256=digest,
            classification=classification,
        )
        job, _ = self.store.create_job(digest, extractor_version, mime_type, units)
        return job

    def extract_claim(self, claim: ClaimedUnit) -> ExtractionResult:
        """Run a synchronous bounded parser over immutable claimed bytes."""
        data = self.cache.read(claim.asset_sha256)
        return self.extractor.extract(data, claim.mime_type, claim.spec)


__all__ = ["IngestionPipeline", "UnitPlanner"]
