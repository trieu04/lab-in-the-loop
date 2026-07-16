"""Regression tests for ingestion restart atomicity and authorization ordering."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from canvus_mcp.access_control import AccessDenied, AccessPolicy, Role
from canvus_mcp.ingestion_cache import AssetCache
from canvus_mcp.ingestion_pipeline import IngestionPipeline
from canvus_mcp.ingestion_store import IngestionStore, LeaseLostError
from canvus_mcp.ingestion_types import UnitSpec
from canvus_mcp.tools import ingestion


class _MCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *_args: Any, **_kwargs: Any):
        def register(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return register


class _Downloader:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self, *_args: str) -> dict[str, object]:
        self.calls += 1
        raise AssertionError("authorization must run before acquisition")


def _reader_context() -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers=[("authorization", "Bearer reader-token")])
        )
    )


def test_restart_reclaims_only_unfinished_work_and_stale_generation_cannot_commit(tmp_path: Path) -> None:
    now = [100.0]
    db_path = tmp_path / "ingestion.db"
    first = IngestionStore(db_path, clock=lambda: now[0], token_factory=iter(("a", "b")).__next__)
    sha = "a" * 64
    try:
        first.upsert_asset(sha, size_bytes=2, mime_type="text/plain")
        job, _ = first.create_job(sha, "v1", "text/plain", [UnitSpec("line", 0), UnitSpec("line", 1)])
        completed = first.claim_next("worker-a", lease_seconds=5)
        stalled = first.claim_next("worker-a", lease_seconds=5)
        assert completed is not None and stalled is not None
        first.complete_unit(completed, [(0, "already durable", {})])
    finally:
        first.close()

    now[0] = 106.0
    resumed = IngestionStore(db_path, clock=lambda: now[0], token_factory=lambda: "c")
    try:
        assert resumed.recover_expired_leases() == 1
        reclaimed = resumed.claim_next("worker-b", lease_seconds=5)
        assert reclaimed is not None and reclaimed.spec.ordinal == 1
        with pytest.raises(LeaseLostError):
            resumed.complete_unit(stalled, [(1, "stale output", {})])
        resumed.complete_unit(reclaimed, [(1, "resumed output", {})])
        assert resumed.get_job(job.id).status.value == "completed"
        rows = resumed.conn.execute(
            "SELECT ordinal, text FROM chunks WHERE job_id=? ORDER BY ordinal", (job.id,)
        ).fetchall()
        assert [tuple(row) for row in rows] == [(0, "already durable"), (1, "resumed output")]
    finally:
        resumed.close()


def test_chunk_insert_failure_rolls_back_completion_before_safe_retry(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db", clock=lambda: 10.0, token_factory=lambda: "lease")
    try:
        sha = "b" * 64
        store.upsert_asset(sha, size_bytes=1, mime_type="text/plain")
        job, _ = store.create_job(sha, "v1", "text/plain", [UnitSpec("whole", 0)])
        claim = store.claim_next("worker", lease_seconds=30)
        assert claim is not None
        store.conn.execute(
            "INSERT INTO chunks(job_id, ordinal, unit_id, text, metadata_json, created_at) VALUES (?, 1, ?, ?, ?, ?)",
            (job.id, claim.unit_id, "preexisting", "{}", 10.0),
        )

        with pytest.raises(sqlite3.IntegrityError):
            store.complete_unit(claim, [(0, "must roll back", {}), (1, "conflict", {})])

        assert store.conn.execute(
            "SELECT text FROM chunks WHERE job_id=? ORDER BY ordinal", (job.id,)
        ).fetchall()[0][0] == "preexisting"
        assert store.conn.execute("SELECT status FROM units WHERE id=?", (claim.unit_id,)).fetchone()[0] == "leased"
        store.conn.execute("DELETE FROM chunks WHERE job_id=?", (job.id,))
        store.complete_unit(claim, [(0, "safe retry", {})])
        assert store.get_job(job.id).status.value == "completed"
        assert store.conn.execute("SELECT ordinal, text FROM chunks WHERE job_id=?", (job.id,)).fetchall()[0][0] == 0
    finally:
        store.close()


async def test_enqueue_denial_precedes_download_cache_and_durable_mutation(tmp_path: Path) -> None:
    store = IngestionStore(tmp_path / "ingestion.db")
    cache = AssetCache(tmp_path / "cache")
    downloader = _Downloader()
    mcp = _MCP()
    policy = AccessPolicy(
        store=store,
        reader_token=SecretStr("reader-token"),
        trusted_service_token=None,
        operator_token=None,
        reader_canvases=("canvas-a",),
        trusted_service_canvases=(),
        operator_canvases=(),
        stdio_role=Role.READER,
        stdio_canvases=(),
    )
    ingestion.register(
        mcp,
        policy=policy,
        pipeline=IngestionPipeline(store=store, cache=cache),
        downloader=downloader,
        max_chunk_chars=80,
    )
    try:
        with pytest.raises(AccessDenied, match="access_denied"):
            await mcp.tools["enqueue_ingestion"]("canvas-a", "pdf", "source-secret", ctx=_reader_context())
        audit = store.conn.execute("SELECT * FROM authorization_audit").fetchone()
        assert downloader.calls == 0
        assert list((tmp_path / "cache").glob("**/*")) == []
        assert store.conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 0
        assert store.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert audit is not None and audit["reason"] == "action_not_authorized"
        assert "reader-token" not in str(tuple(audit)) and "source-secret" not in str(tuple(audit))
    finally:
        store.close()
