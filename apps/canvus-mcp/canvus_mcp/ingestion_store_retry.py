"""Operator retry transition for terminal durable ingestion jobs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from canvus_mcp.ingestion_schema import immediate
from canvus_mcp.ingestion_store_assets import JobNotFoundError
from canvus_mcp.ingestion_types import UnitStatus

if TYPE_CHECKING:
    from canvus_mcp.ingestion_store import IngestionStore
    from canvus_mcp.ingestion_types import Job


def retry_job(store: IngestionStore, job_id: int) -> Job:
    """Replan only incomplete terminal units, retaining completed immutable chunks."""
    with immediate(store.conn):
        now = store.clock()
        if store.conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None:
            raise JobNotFoundError(str(job_id))
        store.conn.execute(
            "UPDATE jobs SET cancel_requested=0, updated_at=? WHERE id=?", (now, job_id)
        )
        store.conn.execute("DELETE FROM cancellations WHERE job_id=?", (job_id,))
        store.conn.execute(
            "UPDATE units SET status=?, next_retry_at=NULL, failure_code=NULL, updated_at=? "
            "WHERE job_id=? AND status IN (?, ?)",
            (
                UnitStatus.PLANNED.value,
                now,
                job_id,
                UnitStatus.CANCELLED.value,
                UnitStatus.POISON.value,
            ),
        )
        store._refresh_job(job_id)
    return store.get_job(job_id)


__all__ = ["retry_job"]
