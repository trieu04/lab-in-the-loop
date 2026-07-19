"""Asset, source, job, and derived-progress operations for ingestion storage."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence

from canvus_mcp.ingestion_schema import immediate
from canvus_mcp.ingestion_types import Job, JobStatus, StoredChunk, UnitSpec, UnitStatus
from canvus_mcp.ingestion_validation import is_extractor_version, is_sha256


class IngestionStoreError(RuntimeError):
    """Base exception for ingestion durable state failures."""


class JobNotFoundError(IngestionStoreError):
    """A requested durable job does not exist."""


def _job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"], asset_sha256=row["asset_sha256"], extractor_version=row["extractor_version"],
        mime_type=row["mime_type"], status=JobStatus(row["status"]), unit_count=row["unit_count"],
        completed_units=row["completed_units"], failed_units=row["failed_units"],
        cancel_requested=bool(row["cancel_requested"]),
    )


class AssetJobStore:
    """Mixin supplying non-leasing state methods to the public store facade."""

    conn: sqlite3.Connection
    clock: Callable[[], float]

    def upsert_asset(self, sha256: str, *, size_bytes: int, mime_type: str) -> None:
        if not is_sha256(sha256) or size_bytes < 0 or not mime_type:
            raise ValueError("invalid asset metadata")
        with immediate(self.conn):
            self.conn.execute(
                "INSERT INTO assets(sha256, size_bytes, mime_type, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(sha256) DO NOTHING", (sha256, size_bytes, mime_type, self.clock()),
            )

    def record_source(
        self, *, canvas_id: str, source_ref: str, asset_sha256: str, classification: str | None = None
    ) -> None:
        if not canvas_id or not source_ref or not is_sha256(asset_sha256):
            raise ValueError("invalid source provenance")
        with immediate(self.conn):
            self.conn.execute(
                "INSERT INTO sources(canvas_id, source_ref, asset_sha256, classification, created_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(canvas_id, source_ref, asset_sha256) DO NOTHING",
                (canvas_id, source_ref, asset_sha256, classification, self.clock()),
            )

    def create_job(
        self, asset_sha256: str, extractor_version: str, mime_type: str, units: Sequence[UnitSpec]
    ) -> tuple[Job, bool]:
        if not is_sha256(asset_sha256) or not is_extractor_version(extractor_version) or not mime_type or not units:
            raise ValueError("invalid job request")
        if len({unit.ordinal for unit in units}) != len(units):
            raise ValueError("unit ordinals must be unique")
        with immediate(self.conn):
            existing = self.conn.execute(
                "SELECT id FROM jobs WHERE asset_sha256 = ? AND extractor_version = ?",
                (asset_sha256, extractor_version),
            ).fetchone()
            if existing:
                return self.get_job(existing["id"]), False
            now = self.clock()
            cursor = self.conn.execute(
                "INSERT INTO jobs(asset_sha256, extractor_version, mime_type, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (asset_sha256, extractor_version, mime_type, JobStatus.QUEUED.value, now, now),
            )
            if cursor.lastrowid is None:
                raise IngestionStoreError("job insert did not return an id")
            job_id = cursor.lastrowid
            self.conn.executemany(
                "INSERT INTO units(job_id, unit_kind, ordinal, start_at, end_at, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (job_id, unit.kind, unit.ordinal, unit.start, unit.end, UnitStatus.PLANNED.value, now, now)
                    for unit in units
                ],
            )
            return self.get_job(job_id), True

    def get_job(self, job_id: int) -> Job:
        row = self.conn.execute(
            "SELECT j.*, COUNT(u.id) AS unit_count, "
            "COALESCE(SUM(u.status = ?), 0) AS completed_units, "
            "COALESCE(SUM(u.status IN (?, ?)), 0) AS failed_units "
            "FROM jobs j LEFT JOIN units u ON u.job_id = j.id WHERE j.id = ? GROUP BY j.id",
            (UnitStatus.COMPLETED.value, UnitStatus.POISON.value, UnitStatus.CANCELLED.value, job_id),
        ).fetchone()
        if row is None:
            raise JobNotFoundError(str(job_id))
        return _job(row)

    def get_job_for_canvas(self, job_id: int, *, canvas_id: str) -> Job | None:
        """Return a job only when its immutable asset has this canvas source."""
        if not canvas_id:
            return None
        exists = self.conn.execute(
            "SELECT 1 FROM jobs j JOIN sources s ON s.asset_sha256=j.asset_sha256 "
            "WHERE j.id=? AND s.canvas_id=? LIMIT 1", (job_id, canvas_id)
        ).fetchone()
        return self.get_job(job_id) if exists is not None else None

    def source_for_job(
        self, job_id: int, *, canvas_id: str, source_ref: str | None = None
    ) -> tuple[str, str] | None:
        """Return requested provenance, never an arbitrary same-canvas source."""
        query = (
            "SELECT source_ref, COALESCE(classification, 'unknown') AS classification FROM sources s "
            "JOIN jobs j ON j.asset_sha256=s.asset_sha256 WHERE j.id=? AND s.canvas_id=?"
        )
        params: tuple[object, ...] = (job_id, canvas_id)
        if source_ref is not None:
            query += " AND s.source_ref=?"
            params += (source_ref,)
        query += " ORDER BY s.id LIMIT 1"
        row = self.conn.execute(query, params).fetchone()
        return None if row is None else (row["source_ref"], row["classification"])

    def sources_for_job(self, job_id: int, *, canvas_id: str, limit: int = 20) -> list[tuple[str, str]]:
        """Return a bounded explicit source set for ambiguous job-only reads."""
        if not 1 <= limit <= 20:
            raise ValueError("invalid source limit")
        rows = self.conn.execute(
            "SELECT source_ref, COALESCE(classification, 'unknown') AS classification FROM sources s "
            "JOIN jobs j ON j.asset_sha256=s.asset_sha256 WHERE j.id=? AND s.canvas_id=? "
            "ORDER BY s.id LIMIT ?",
            (job_id, canvas_id, limit),
        ).fetchall()
        return [(row["source_ref"], row["classification"]) for row in rows]

    def read_completed_chunks_for_canvas(
        self, job_id: int, *, canvas_id: str, after_ordinal: int, limit: int
    ) -> list[StoredChunk]:
        """Read durable chunks without changing job, lease, or progress state."""
        if after_ordinal < -1 or limit < 1:
            raise ValueError("invalid chunk page")
        if self.source_for_job(job_id, canvas_id=canvas_id) is None:
            return []
        rows = self.conn.execute(
            "SELECT ordinal, text, metadata_json FROM chunks WHERE job_id=? AND ordinal>? ORDER BY ordinal LIMIT ?",
            (job_id, after_ordinal, limit),
        ).fetchall()
        return [StoredChunk(row["ordinal"], row["text"], json.loads(row["metadata_json"])) for row in rows]

    def _refresh_job(self, job_id: int) -> None:
        counts = self.conn.execute(
            "SELECT j.cancel_requested, COUNT(u.id) total, SUM(u.status = ?) complete, "
            "SUM(u.status = ?) leased, SUM(u.status = ?) poison "
            "FROM jobs j JOIN units u ON u.job_id = j.id WHERE j.id = ? GROUP BY j.id",
            (
                UnitStatus.COMPLETED.value,
                UnitStatus.LEASED.value,
                UnitStatus.POISON.value,
                job_id,
            ),
        ).fetchone()
        if counts is None:
            raise JobNotFoundError(str(job_id))
        if counts["cancel_requested"]:
            status = JobStatus.CANCELLED
        elif counts["complete"] == counts["total"]:
            status = JobStatus.COMPLETED
        elif counts["poison"]:
            status = JobStatus.FAILED
        elif counts["leased"]:
            status = JobStatus.RUNNING
        else:
            status = JobStatus.QUEUED
        self.conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, self.clock(), job_id),
        )


__all__ = ["AssetJobStore", "IngestionStoreError", "JobNotFoundError"]
