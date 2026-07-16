"""Bounded standalone async worker for locally durable ingestion jobs."""

from __future__ import annotations

import asyncio
import random
import sqlite3
from collections.abc import Callable

import structlog

from canvus_mcp.ingestion_cache import CacheIntegrityError
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import LeaseLostError
from canvus_mcp.ingestion_types import (
    ClaimedUnit,
    ExtractionResult,
    ExtractionStatus,
    failed_extraction_result,
)

log = structlog.get_logger(__name__)


class IngestionWorker:
    """Claims small leased units; all blocking cache/parser work runs off-loop."""

    def __init__(
        self,
        pipeline: IngestionPipeline,
        *,
        owner: str,
        concurrency: int = 2,
        lease_seconds: float = 60.0,
        max_attempts: int = 3,
        base_backoff_seconds: float = 1.0,
        poll_seconds: float = 0.2,
        rng: Callable[[], float] = random.random,
    ) -> None:
        if not owner or not 1 <= concurrency <= 4 or lease_seconds < 0.1 or max_attempts < 1:
            raise ValueError("invalid worker configuration")
        self.pipeline, self.owner, self.concurrency = pipeline, owner, concurrency
        self.lease_seconds, self.max_attempts = lease_seconds, max_attempts
        self.base_backoff_seconds, self.poll_seconds, self.rng = base_backoff_seconds, poll_seconds, rng
        self._stopping = False

    def stop(self) -> None:
        """Stop claiming new work; already claimed units finish safely."""
        self._stopping = True

    async def run(self) -> None:
        """Run until stopped; stopped instances are one-shot and cannot restart."""
        if self._stopping:
            return
        pending: set[asyncio.Task[bool]] = set()
        try:
            while not self._stopping:
                while len(pending) < self.concurrency and not self._stopping:
                    pending.add(asyncio.create_task(self.run_once()))
                    await asyncio.sleep(0)
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                if not any(task.result() for task in done):
                    await asyncio.sleep(self.poll_seconds)
        except asyncio.CancelledError:
            self.stop()
            await asyncio.gather(*pending, return_exceptions=True)
            raise
        if pending:
            await asyncio.gather(*pending, return_exceptions=False)

    async def run_once(self) -> bool:
        """Process one claimed unit, returning whether a unit was claimed."""
        if self._stopping:
            return False
        store = self.pipeline.store
        try:
            store.recover_expired_leases(max_attempts=self.max_attempts)
            claim = store.claim_next(self.owner, lease_seconds=self.lease_seconds)
        except sqlite3.Error:
            log.warning("ingestion_store_unavailable")
            return False
        if claim is None:
            return False
        try:
            result = await self._extract_with_renewal(claim)
        except (CacheIntegrityError, OSError):
            result = failed_extraction_result(ExtractionStatus.FAILED)
        except Exception:
            log.warning("ingestion_extract_failed", job_id=claim.job_id, unit_id=claim.unit_id)
            result = failed_extraction_result(ExtractionStatus.FAILED)
        try:
            if result.status is ExtractionStatus.OK:
                chunks = [(chunk.ordinal, chunk.text, dict(chunk.metadata)) for chunk in result.chunks]
                store.complete_unit(claim, chunks)
            else:
                retry_at = store.clock() + self._backoff(claim.attempt_number)
                store.fail_unit(
                    claim,
                    result.failure_code or "failed",
                    retry_at=retry_at,
                    max_attempts=self.max_attempts,
                )
        except LeaseLostError:
            return False
        except sqlite3.Error:
            log.warning("ingestion_finalize_failed", job_id=claim.job_id, unit_id=claim.unit_id)
            return False
        return True

    async def _extract_with_renewal(self, claim: ClaimedUnit) -> ExtractionResult:
        task = asyncio.create_task(asyncio.to_thread(self.pipeline.extract_claim, claim))
        interval = self.lease_seconds / 2
        while True:
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=interval)
            except TimeoutError:
                renewed = self.pipeline.store.renew_lease(claim, lease_seconds=self.lease_seconds)
                if not renewed:
                    try:
                        await asyncio.shield(task)
                    except Exception:
                        pass
                    raise LeaseLostError("lease expired during extraction")
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(task)
                except Exception:
                    pass
                raise

    def _backoff(self, attempt: int) -> float:
        return self.base_backoff_seconds * (2 ** (attempt - 1)) * (1 + max(0.0, self.rng()) * 0.1)


__all__ = ["IngestionWorker"]
