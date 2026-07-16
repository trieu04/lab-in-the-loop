"""Migrations, integrity checks, startup PRAGMAs, and backup/restore for the
durable SQLite ledger (``lab_agent.state_store``).

Shared ``_Clock``/``_Rng``/``store`` fixtures live in ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest

from lab_agent.state.connection import MigrationOrderError, connect, integrity_check, migrate
from lab_agent.state_store import MigrationChecksumError, StateStore


def test_migrate_creates_all_ledger_tables(store: StateStore) -> None:
    tables = {
        row["name"]
        for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {
        "schema_migrations",
        "workflow_attempts",
        "canvas_leases",
        "side_effect_intents",
        "orchestrator_edges",
        "audit_events",
    } <= tables


def test_migrate_records_applied_version_and_checksum(store: StateStore) -> None:
    row = store.conn.execute("SELECT version, checksum FROM schema_migrations").fetchone()
    assert row["version"] == 1
    assert len(row["checksum"]) == 64  # sha256 hex digest


def test_terminal_audit_lookup_is_indexed_and_trigger_scoped(store: StateStore) -> None:
    first = {"trigger_id": "loop:first", "reason": "max_rounds"}
    second = {"trigger_id": "loop:second", "reason": "model_decision"}
    store.append_audit_event("canvas", "loop_stopped", first, round=1)
    store.append_audit_event("canvas", "loop_stopped", second, round=2)

    found = store.find_terminal_event("canvas", "loop:first")
    indexes = {
        row["name"] for row in store.conn.execute("PRAGMA index_list('audit_events')")
    }

    plan = store.conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM audit_events WHERE canvas_id=? "
        "AND event='loop_stopped' AND json_extract(payload_json, '$.trigger_id')=? "
        "ORDER BY sequence DESC LIMIT 1", ("canvas", "loop:first"),
    ).fetchall()
    assert found is not None and found.payload == first
    assert store.find_terminal_event("canvas", "loop:missing") is None
    assert "idx_audit_events_terminal_trigger" in indexes
    assert any("idx_audit_events_terminal_trigger" in row["detail"] for row in plan)


def test_migrate_is_idempotent_on_reopen(tmp_path, clock, rng) -> None:
    db_path = tmp_path / "state.db"
    first = StateStore(db_path, clock=clock, rng=rng)
    first_versions = tuple(
        row["version"]
        for row in first.conn.execute("SELECT version FROM schema_migrations ORDER BY version")
    )
    first.close()

    second = StateStore(db_path, clock=clock, rng=rng)
    try:
        second_versions = tuple(
            row["version"]
            for row in second.conn.execute("SELECT version FROM schema_migrations ORDER BY version")
        )
        assert second_versions == first_versions
    finally:
        second.close()


def test_migration_checksum_drift_raises(tmp_path, clock) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_initial.sql").write_text("CREATE TABLE t (id INTEGER PRIMARY KEY);")

    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    migrate(conn, clock=clock, migrations_dir=migrations_dir)

    # Simulate on-disk drift: same version, different SQL body/checksum.
    (migrations_dir / "001_initial.sql").write_text("CREATE TABLE t (id INTEGER PRIMARY KEY, x TEXT);")
    with pytest.raises(MigrationChecksumError):
        migrate(conn, clock=clock, migrations_dir=migrations_dir)
    conn.close()


def test_migration_duplicate_version_raises_order_error(tmp_path, clock) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "001_a.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);")
    (migrations_dir / "001_b.sql").write_text("CREATE TABLE b (id INTEGER PRIMARY KEY);")

    conn = connect(tmp_path / "state.db")
    with pytest.raises(MigrationOrderError):
        migrate(conn, clock=clock, migrations_dir=migrations_dir)
    conn.close()


def test_migration_non_numeric_prefix_raises_order_error(tmp_path, clock) -> None:
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "abc_bad.sql").write_text("CREATE TABLE a (id INTEGER PRIMARY KEY);")

    conn = connect(tmp_path / "state.db")
    with pytest.raises(MigrationOrderError):
        migrate(conn, clock=clock, migrations_dir=migrations_dir)
    conn.close()


def test_integrity_check_healthy_db_returns_empty_list(store: StateStore) -> None:
    assert store.integrity_check() == []


def test_startup_pragmas_are_set(store: StateStore) -> None:
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert store.conn.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
    assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_backup_produces_a_restorable_independent_copy(tmp_path, clock, rng) -> None:
    source = StateStore(tmp_path / "source.db", clock=clock, rng=rng)
    source.ensure_attempt("c1", "t1")
    source.append_audit_event("c1", "loop_started", {"trigger_id": "t1"})

    backup_path = tmp_path / "backup.db"
    source.backup(backup_path)
    source.close()

    restored = StateStore(backup_path, clock=clock, rng=rng)
    try:
        assert restored.get_attempt("c1", "t1") is not None
        assert restored.integrity_check() == []
        restored.verify_audit_chain()  # backup preserved the hash chain intact
    finally:
        restored.close()


def test_sqlite_integrity_check_via_raw_connection(tmp_path, clock) -> None:
    db_path = tmp_path / "state.db"
    conn = connect(db_path)
    migrate(conn, clock=clock)
    assert integrity_check(conn) == []
    conn.close()
