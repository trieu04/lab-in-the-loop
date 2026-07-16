"""SQLite connection setup and checksummed ingestion-schema migrations."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

Clock = Callable[[], float]


class MigrationChecksumError(RuntimeError):
    """An applied migration changed on disk."""


class MigrationOrderError(RuntimeError):
    """Migration names are not unique, ordered numeric versions."""


def connect(path: Path) -> sqlite3.Connection:
    """Open a local durable database with safe single-host SQLite settings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection, *, clock: Clock = time.time, migrations_dir: Path | None = None) -> None:
    """Apply immutable numbered SQL migrations and record their SHA-256 values."""
    directory = migrations_dir or Path(__file__).with_name("ingestion_migrations")
    files = sorted(directory.glob("*.sql"))
    versions: list[int] = []
    for file in files:
        prefix = file.name.partition("_")[0]
        if not prefix.isdigit():
            raise MigrationOrderError(f"invalid migration name: {file.name}")
        versions.append(int(prefix))
    if len(versions) != len(set(versions)):
        raise MigrationOrderError("duplicate migration version")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ingestion_schema_migrations "
        "(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at REAL NOT NULL)"
    )
    applied = {row["version"]: row["checksum"] for row in conn.execute("SELECT version, checksum FROM ingestion_schema_migrations")}
    for version, file in zip(versions, files, strict=True):
        sql = file.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        if version in applied:
            if applied[version] != checksum:
                raise MigrationChecksumError(f"checksum mismatch for migration {version}")
            continue
        applied_at = float(clock())
        script = (
            "BEGIN IMMEDIATE;\n"
            f"{sql}\n"
            "INSERT INTO ingestion_schema_migrations(version, checksum, applied_at) "
            f"VALUES ({version}, '{checksum}', {applied_at:.17g});\nCOMMIT;"
        )
        try:
            conn.executescript(script)
        except sqlite3.Error:
            conn.rollback()
            raise


@contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[None]:
    """Run a short writer transaction that fails atomically."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def integrity_check(conn: sqlite3.Connection) -> list[str]:
    """Return non-healthy SQLite integrity rows, or an empty list."""
    return [row[0] for row in conn.execute("PRAGMA integrity_check") if row[0] != "ok"]


__all__ = [
    "MigrationChecksumError",
    "MigrationOrderError",
    "connect",
    "immediate",
    "integrity_check",
    "migrate",
]
