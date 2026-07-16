"""Durability and lease tests for the local ingestion SQLite store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from canvus_mcp.ingestion_schema import MigrationChecksumError, connect, migrate
from canvus_mcp.ingestion_store import IngestionStore, LeaseLostError
from canvus_mcp.ingestion_types import UnitSpec


def make_store(tmp_path: Path) -> IngestionStore:
    return IngestionStore(tmp_path / "ingestion.db", clock=lambda: 100.0, token_factory=lambda: "token")


def test_migrations_reopen_and_integrity(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    assert store.integrity_check() == []
    assert store.migration_versions() == (1, 2, 3, 4)
    store.close()

    reopened = make_store(tmp_path)
    try:
        assert reopened.migration_versions() == (1, 2, 3, 4)
        assert reopened.integrity_check() == []
    finally:
        reopened.close()


def test_migration_checksum_drift_is_rejected(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    migration = migrations / "001_initial.sql"
    migration.write_text("CREATE TABLE durable_test (id INTEGER PRIMARY KEY);")
    conn = connect(tmp_path / "custom.db")
    try:
        migrate(conn, migrations_dir=migrations)
        migration.write_text("CREATE TABLE durable_test (id INTEGER PRIMARY KEY, name TEXT);")
        with pytest.raises(MigrationChecksumError):
            migrate(conn, migrations_dir=migrations)
    finally:
        conn.close()


def test_failed_migration_rolls_back_its_partial_schema(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001_broken.sql").write_text("CREATE TABLE partial_test (id INTEGER); INVALID SQL;")
    conn = connect(tmp_path / "broken.db")
    try:
        with pytest.raises(sqlite3.Error):
            migrate(conn, migrations_dir=migrations)
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='partial_test'").fetchone() is None
    finally:
        conn.close()


def test_sources_share_asset_but_job_is_version_deduplicated(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        sha = "a" * 64
        store.upsert_asset(sha, size_bytes=3, mime_type="text/plain")
        store.record_source(canvas_id="canvas-a", source_ref="widget-a", asset_sha256=sha)
        store.record_source(canvas_id="canvas-b", source_ref="widget-b", asset_sha256=sha)
        first, created = store.create_job(sha, "text-v1", "text/plain", [UnitSpec("whole", 0)])
        second, repeated = store.create_job(sha, "text-v1", "text/plain", [UnitSpec("whole", 0)])
        changed, versioned = store.create_job(sha, "text-v2", "text/plain", [UnitSpec("whole", 0)])

        assert created and not repeated and versioned
        assert first.id == second.id
        assert changed.id != first.id
        assert store.can_read(first.id, canvas_id="canvas-a", source_ref="widget-a")
        assert not store.can_read(first.id, canvas_id="canvas-a", source_ref="widget-b")
    finally:
        store.close()


def test_expired_lease_reclaims_and_stale_completion_fails(tmp_path: Path) -> None:
    now = [100.0]
    tokens = iter(("first", "second"))
    store = IngestionStore(tmp_path / "ingestion.db", clock=lambda: now[0], token_factory=lambda: next(tokens))
    try:
        sha = "b" * 64
        store.upsert_asset(sha, size_bytes=1, mime_type="text/plain")
        job, _ = store.create_job(sha, "v1", "text/plain", [UnitSpec("whole", 0)])
        first = store.claim_next("worker-a", lease_seconds=5)
        assert first is not None
        now[0] = 106.0
        assert store.recover_expired_leases() == 1
        second = store.claim_next("worker-b", lease_seconds=5)
        assert second is not None
        with pytest.raises(LeaseLostError):
            store.complete_unit(first, ())
        store.complete_unit(second, ())
        assert store.get_job(job.id).status == "completed"
    finally:
        store.close()


def test_reopen_after_crash_before_completion_reclaims_unfinished_unit(tmp_path: Path) -> None:
    now = [100.0]
    db_path = tmp_path / "ingestion.db"
    first = IngestionStore(db_path, clock=lambda: now[0], token_factory=lambda: "token-a")
    sha = "e" * 64
    first.upsert_asset(sha, size_bytes=1, mime_type="text/plain")
    first.create_job(sha, "v1", "text/plain", [UnitSpec("whole", 0)])
    assert first.claim_next("worker", lease_seconds=5) is not None
    first.close()

    now[0] = 106.0
    resumed = IngestionStore(db_path, clock=lambda: now[0], token_factory=lambda: "token-b")
    try:
        assert resumed.recover_expired_leases() == 1
        assert resumed.claim_next("worker", lease_seconds=5) is not None
    finally:
        resumed.close()


def test_completion_inserts_chunks_and_marks_unit_atomically(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        sha = "c" * 64
        store.upsert_asset(sha, size_bytes=1, mime_type="text/plain")
        job, _ = store.create_job(sha, "v1", "text/plain", [UnitSpec("whole", 0)])
        claim = store.claim_next("worker", lease_seconds=60)
        assert claim is not None
        store.complete_unit(claim, ((0, "bounded text", {"kind": "text"}),))
        assert store.get_job(job.id).completed_units == 1
        assert [chunk.text for chunk in store.read_chunks(job.id, canvas_id="c", source_ref="s")] == []
        store.record_source(canvas_id="c", source_ref="s", asset_sha256=sha)
        assert [chunk.text for chunk in store.read_chunks(job.id, canvas_id="c", source_ref="s")] == ["bounded text"]
    finally:
        store.close()


def test_cancellation_blocks_racing_completion_and_poison_is_terminal(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    try:
        sha = "d" * 64
        store.upsert_asset(sha, size_bytes=1, mime_type="text/plain")
        job, _ = store.create_job(sha, "v1", "text/plain", [UnitSpec("whole", 0)])
        claim = store.claim_next("worker", lease_seconds=60)
        assert claim is not None
        store.cancel_job(job.id)
        with pytest.raises(LeaseLostError):
            store.complete_unit(claim, ())

        retry, _ = store.create_job(sha, "v2", "text/plain", [UnitSpec("whole", 0)])
        poison_claim = store.claim_next("worker", lease_seconds=60)
        assert poison_claim is not None
        state = store.fail_unit(poison_claim, "malformed", retry_at=101.0, max_attempts=1)
        assert state == "poison"
        assert store.get_job(retry.id).status == "failed"
    finally:
        store.close()
