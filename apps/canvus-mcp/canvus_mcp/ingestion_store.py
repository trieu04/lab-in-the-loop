"""Public durable SQLite facade for resumable local ingestion."""

from __future__ import annotations

import secrets
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from canvus_mcp.ingestion_schema import (
    MigrationChecksumError,
    MigrationOrderError,
    connect,
    integrity_check,
    migrate,
)
from canvus_mcp.ingestion_store_assets import IngestionStoreError, JobNotFoundError
from canvus_mcp.ingestion_store_retry import retry_job
from canvus_mcp.ingestion_store_work import LeaseLostError, WorkStore


class IngestionStore(WorkStore):
    """Owns one local WAL connection and its checksummed ingestion schema.

    Every state-changing method inherited from :class:`WorkStore` uses a short
    ``BEGIN IMMEDIATE`` transaction. Raw asset bytes are intentionally stored
    in the content-addressed cache, never in this database.
    """

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], float] = time.time,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
    ) -> None:
        self.conn: sqlite3.Connection = connect(path)
        self._closed = False
        self.clock = clock
        self.token_factory = token_factory
        try:
            migrate(self.conn, clock=clock)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Close the owned SQLite connection exactly once."""
        if not self._closed:
            self._closed = True
            self.conn.close()

    def integrity_check(self) -> list[str]:
        """Return any SQLite integrity failures."""
        return integrity_check(self.conn)

    def retry_job(self, job_id: int):
        """Requeue a cancelled or poisoned job without rerunning completed units."""
        return retry_job(self, job_id)

    def migration_versions(self) -> tuple[int, ...]:
        """Return the applied immutable migration versions."""
        return tuple(
            row["version"]
            for row in self.conn.execute(
                "SELECT version FROM ingestion_schema_migrations ORDER BY version"
            )
        )


__all__ = [
    "IngestionStore",
    "IngestionStoreError",
    "JobNotFoundError",
    "LeaseLostError",
    "MigrationChecksumError",
    "MigrationOrderError",
]
