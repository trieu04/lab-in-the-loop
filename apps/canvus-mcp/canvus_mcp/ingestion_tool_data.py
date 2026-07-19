"""Bounded public response records for ingestion MCP tools."""

from __future__ import annotations

from typing import Any

from canvus_mcp.ingestion_store import IngestionStore, JobNotFoundError
from canvus_mcp.ingestion_types import Job


def job_data(
    store: IngestionStore, job: Job, canvas_id: str, *, source_ref: str | None = None
) -> dict[str, Any]:
    """Build job data from exact or explicitly bounded source provenance."""
    sources = store.sources_for_job(job.id, canvas_id=canvas_id, limit=20)
    source = (
        store.source_for_job(job.id, canvas_id=canvas_id, source_ref=source_ref)
        if source_ref is not None else None
    )
    if source_ref is not None and source is None:
        raise JobNotFoundError(str(job.id))
    if source is None and len(sources) == 1:
        source = sources[0]
    if not sources:
        raise JobNotFoundError(str(job.id))
    response: dict[str, Any] = {
        "job_id": job.id,
        "status": job.status.value,
        "asset_sha256": job.asset_sha256,
        "extractor_version": job.extractor_version,
        "mime_type": job.mime_type,
        "unit_count": job.unit_count,
        "completed_units": job.completed_units,
        "failed_units": job.failed_units,
        "cancel_requested": job.cancel_requested,
    }
    if source is not None:
        response.update(_source_record(*source))
        return response
    response["data_classification"] = "unknown"
    response["sources"] = [
        _source_record(value, classification) for value, classification in sources
    ]
    return response


def _source_record(source_ref: str, classification: str) -> dict[str, str]:
    kind, _, source_id = source_ref.partition(":")
    return {
        "source_kind": kind,
        "source_id": source_id,
        "data_classification": classification,
    }


__all__ = ["job_data"]
