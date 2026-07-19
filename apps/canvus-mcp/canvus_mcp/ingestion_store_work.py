"""Leasing, retry, cancellation, and chunk-completion operations."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping

from canvus_mcp.ingestion_schema import immediate
from canvus_mcp.ingestion_store_assets import AssetJobStore, JobNotFoundError
from canvus_mcp.ingestion_types import ClaimedUnit, StoredChunk, UnitSpec, UnitStatus

_FAILURE = re.compile(r"^[a-z_]{1,48}$")


class LeaseLostError(RuntimeError):
    """A completion/failure does not own the current unit lease."""


class WorkStore(AssetJobStore):
    """Mixin implementing restart-safe worker state transitions."""

    token_factory: Callable[[], str]
    def claim_next(self, owner: str, *, lease_seconds: float) -> ClaimedUnit | None:
        if not owner or lease_seconds <= 0:
            raise ValueError("invalid lease request")
        with immediate(self.conn):
            now = self.clock()
            row = self.conn.execute(
                "SELECT u.*, j.asset_sha256, j.mime_type FROM units u JOIN jobs j ON j.id = u.job_id "
                "WHERE j.cancel_requested = 0 AND u.status IN (?, ?) "
                "AND (u.next_retry_at IS NULL OR u.next_retry_at <= ?) ORDER BY u.id LIMIT 1",
                (UnitStatus.PLANNED.value, UnitStatus.RETRY.value, now),
            ).fetchone()
            if row is None:
                return None
            token, generation = self.token_factory(), row["generation"] + 1
            self.conn.execute(
                "UPDATE units SET status=?, generation=?, attempt_count=attempt_count+1, updated_at=? "
                "WHERE id=? AND status IN (?, ?)",
                (
                    UnitStatus.LEASED.value,
                    generation,
                    now,
                    row["id"],
                    UnitStatus.PLANNED.value,
                    UnitStatus.RETRY.value,
                ),
            )
            self.conn.execute(
                "INSERT INTO leases(unit_id, token, generation, owner, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], token, generation, owner, now + lease_seconds, now),
            )
            self.conn.execute(
                "INSERT INTO attempts(unit_id, generation, status, started_at) VALUES (?, ?, 'running', ?)",
                (row["id"], generation, now),
            )
            self._refresh_job(row["job_id"])
            return ClaimedUnit(
                job_id=row["job_id"],
                unit_id=row["id"],
                asset_sha256=row["asset_sha256"],
                mime_type=row["mime_type"],
                spec=UnitSpec(
                    row["unit_kind"], row["ordinal"], row["start_at"], row["end_at"]
                ),
                lease_token=token,
                generation=generation,
                attempt_number=row["attempt_count"] + 1,
            )
    def renew_lease(self, claim: ClaimedUnit, *, lease_seconds: float) -> bool:
        with immediate(self.conn):
            now = self.clock()
            result = self.conn.execute(
                "UPDATE leases SET expires_at=? WHERE unit_id=? AND token=? AND generation=? AND expires_at>?",
                (now + lease_seconds, claim.unit_id, claim.lease_token, claim.generation, now),
            )
            return result.rowcount == 1
    def recover_expired_leases(self, *, max_attempts: int = 3) -> int:
        """Reclaim expired work without allowing crashes to bypass poison limits."""
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        with immediate(self.conn):
            now = self.clock()
            rows = self.conn.execute(
                "SELECT l.unit_id, l.generation, u.job_id, u.attempt_count FROM leases l "
                "JOIN units u ON u.id=l.unit_id WHERE l.expires_at <= ?", (now,)
            ).fetchall()
            for row in rows:
                terminal = row["attempt_count"] >= max_attempts
                status = UnitStatus.POISON if terminal else UnitStatus.RETRY
                self.conn.execute(
                    "UPDATE units SET status=?, next_retry_at=?, failure_code=?, updated_at=? "
                    "WHERE id=? AND status=?",
                    (
                        status.value, None if terminal else now,
                        "lease_expired" if terminal else None, now, row["unit_id"], UnitStatus.LEASED.value,
                    ),
                )
                self.conn.execute(
                    "UPDATE attempts SET status='expired', failure_code='lease_expired', finished_at=? "
                    "WHERE unit_id=? AND generation=? AND status='running'",
                    (now, row["unit_id"], row["generation"]),
                )
            self.conn.execute("DELETE FROM leases WHERE expires_at <= ?", (now,))
            for job_id in {row["job_id"] for row in rows}:
                self._refresh_job(job_id)
            return len(rows)
    def complete_unit(
        self, claim: ClaimedUnit, chunks: Iterable[tuple[int, str, Mapping[str, object]]]
    ) -> None:
        values = tuple(chunks)
        if len({ordinal for ordinal, _, _ in values}) != len(values):
            raise ValueError("chunk ordinals must be unique")
        with immediate(self.conn):
            self._assert_live_lease(claim)
            now = self.clock()
            chunk_rows = [
                (claim.job_id, ordinal, claim.unit_id, text, json.dumps(metadata, sort_keys=True), now)
                for ordinal, text, metadata in values
            ]
            self.conn.executemany(
                "INSERT INTO chunks(job_id, ordinal, unit_id, text, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                chunk_rows,
            )
            self.conn.execute(
                "UPDATE units SET status=?, updated_at=? WHERE id=?",
                (UnitStatus.COMPLETED.value, now, claim.unit_id),
            )
            self.conn.execute(
                "UPDATE attempts SET status='completed', finished_at=? WHERE unit_id=? AND generation=?",
                (now, claim.unit_id, claim.generation),
            )
            self.conn.execute("DELETE FROM leases WHERE unit_id=?", (claim.unit_id,))
            self._refresh_job(claim.job_id)
    def fail_unit(self, claim: ClaimedUnit, failure_code: str, *, retry_at: float, max_attempts: int) -> str:
        code = failure_code if _FAILURE.fullmatch(failure_code) else "failed"
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        with immediate(self.conn):
            unit = self._assert_live_lease(claim)
            now = self.clock()
            terminal = unit["attempt_count"] >= max_attempts
            status = UnitStatus.POISON if terminal else UnitStatus.RETRY
            self.conn.execute(
                "UPDATE units SET status=?, next_retry_at=?, failure_code=?, updated_at=? WHERE id=?",
                (status.value, None if terminal else retry_at, code, now, claim.unit_id),
            )
            self.conn.execute(
                "UPDATE attempts SET status='failed', failure_code=?, finished_at=? WHERE unit_id=? AND generation=?",
                (code, now, claim.unit_id, claim.generation),
            )
            self.conn.execute("DELETE FROM leases WHERE unit_id=?", (claim.unit_id,))
            self._refresh_job(claim.job_id)
            return status.value
    def cancel_job(self, job_id: int, *, reason: str = "") -> None:
        with immediate(self.conn):
            now = self.clock()
            if self.conn.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,)).rowcount != 1:
                raise JobNotFoundError(str(job_id))
            self.conn.execute(
                "INSERT OR REPLACE INTO cancellations(job_id, requested_at, reason) VALUES (?, ?, ?)",
                (job_id, now, reason),
            )
            self.conn.execute(
                "UPDATE units SET status=?, updated_at=? WHERE job_id=? AND status != ?",
                (UnitStatus.CANCELLED.value, now, job_id, UnitStatus.COMPLETED.value),
            )
            self.conn.execute(
                "UPDATE attempts SET status='cancelled', finished_at=? WHERE unit_id IN (SELECT id FROM units WHERE job_id=?) "
                "AND status='running'", (now, job_id),
            )
            self.conn.execute("DELETE FROM leases WHERE unit_id IN (SELECT id FROM units WHERE job_id=?)", (job_id,))
            self._refresh_job(job_id)
    def _assert_live_lease(self, claim: ClaimedUnit) -> sqlite3.Row:
        row = self.conn.execute(
            "SELECT u.*, j.cancel_requested, l.token FROM units u JOIN jobs j ON j.id=u.job_id "
            "JOIN leases l ON l.unit_id=u.id WHERE u.id=? AND l.token=? AND l.generation=? AND l.expires_at>?",
            (claim.unit_id, claim.lease_token, claim.generation, self.clock()),
        ).fetchone()
        if row is None or row["status"] != UnitStatus.LEASED.value or row["cancel_requested"]:
            raise LeaseLostError("lease is no longer current")
        return row
    def read_chunks(self, job_id: int, *, canvas_id: str, source_ref: str) -> list[StoredChunk]:
        if not self.can_read(job_id, canvas_id=canvas_id, source_ref=source_ref):
            return []
        rows = self.conn.execute(
            "SELECT ordinal, text, metadata_json FROM chunks WHERE job_id=? ORDER BY ordinal", (job_id,)
        ).fetchall()
        return [StoredChunk(row["ordinal"], row["text"], json.loads(row["metadata_json"])) for row in rows]
    def can_read(self, job_id: int, *, canvas_id: str, source_ref: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM jobs j JOIN sources s ON s.asset_sha256=j.asset_sha256 "
            "WHERE j.id=? AND s.canvas_id=? AND s.source_ref=?", (job_id, canvas_id, source_ref),
        ).fetchone() is not None


__all__ = ["LeaseLostError", "WorkStore"]
