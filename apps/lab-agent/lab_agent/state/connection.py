"""SQLite connection setup and the versioned migration runner.

WAL journal mode, ``synchronous=NORMAL``, ``foreign_keys=ON``, and a busy
timeout are set on every connection. Migrations are plain ``NNN_name.sql``
files (checked in under ``lab_agent/migrations/``) applied once, in order,
inside a short ``BEGIN IMMEDIATE`` transaction each; the applied checksum is
recorded so on-disk drift after the fact is rejected rather than silently
re-applied or ignored.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from lab_agent.state.models import Clock

_COMMENT_RE = re.compile(r"--[^\n]*")

_VERSION_SEP = "_"

_BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at REAL NOT NULL,
    checksum   TEXT NOT NULL
);
"""


class MigrationChecksumError(RuntimeError):
    """An applied migration's on-disk checksum no longer matches the checksum
    recorded when it was applied -- schema drift on a live ledger."""


class MigrationOrderError(RuntimeError):
    """A migration filename is not a unique, numerically-versioned ``NNN_*.sql``."""


@dataclass(frozen=True)
class _Migration:
    version: int
    name: str
    sql: str
    checksum: str


def _parse_version(filename: str) -> int:
    prefix = filename.split(_VERSION_SEP, 1)[0]
    try:
        return int(prefix)
    except ValueError as exc:
        raise MigrationOrderError(
            f"migration filename must start with a numeric version: {filename!r}"
        ) from exc


def _load_migration(name: str, sql: str) -> _Migration:
    version = _parse_version(name)
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    return _Migration(version=version, name=name, sql=sql, checksum=checksum)


def _discover_migrations(migrations_dir: Path | None) -> list[_Migration]:
    """Load every ``*.sql`` migration, sorted by version.

    ``migrations_dir`` overrides the packaged ``lab_agent/migrations/``
    directory -- tests use it to exercise drift/ordering failures without
    touching the real schema file.
    """
    if migrations_dir is not None:
        entries = [(p.name, p.read_text(encoding="utf-8")) for p in sorted(migrations_dir.glob("*.sql"))]
    else:
        package = resources.files("lab_agent.migrations")
        entries = sorted(
            (entry.name, entry.read_text(encoding="utf-8"))
            for entry in package.iterdir()
            if entry.name.endswith(".sql")
        )

    migrations: list[_Migration] = []
    seen: set[int] = set()
    for name, sql in entries:
        migration = _load_migration(name, sql)
        if migration.version in seen:
            raise MigrationOrderError(f"duplicate migration version {migration.version} ({name})")
        seen.add(migration.version)
        migrations.append(migration)
    migrations.sort(key=lambda m: m.version)
    return migrations


def _split_statements(sql: str) -> list[str]:
    """Split a migration script into individual statements on ``;``.

    Migration files are checked-in DDL with no string literals containing a
    ``--`` comment marker or a semicolon, so stripping ``-- ...`` comments and
    then splitting on ``;`` is safe. This lets each statement run inside our
    own explicit transaction (``sqlite3.executescript`` would silently commit
    any open transaction first, breaking atomicity).
    """
    uncommented = _COMMENT_RE.sub("", sql)
    return [stmt.strip() for stmt in uncommented.split(";") if stmt.strip()]


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with the harness's durability PRAGMAs set."""
    conn = sqlite3.connect(str(db_path), isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def migrate(conn: sqlite3.Connection, *, clock: Clock, migrations_dir: Path | None = None) -> None:
    """Apply any un-applied versioned migrations; reject checksum drift.

    Idempotent: safe to call on every startup. ``schema_migrations`` itself is
    bootstrapped here (it must exist before migrations can be tracked at all),
    separate from the numbered migration files it tracks.
    """
    conn.execute(_BOOTSTRAP_SQL)
    applied = {
        row["version"]: row["checksum"]
        for row in conn.execute("SELECT version, checksum FROM schema_migrations")
    }
    for migration in _discover_migrations(migrations_dir):
        recorded = applied.get(migration.version)
        if recorded is not None:
            if recorded != migration.checksum:
                raise MigrationChecksumError(
                    f"migration {migration.version} ({migration.name}) checksum drift: "
                    f"recorded={recorded} on_disk={migration.checksum}"
                )
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in _split_statements(migration.sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at, checksum) VALUES (?, ?, ?)",
                (migration.version, clock(), migration.checksum),
            )
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def integrity_check(conn: sqlite3.Connection) -> list[str]:
    """Run ``PRAGMA integrity_check``; return problems (empty list == healthy)."""
    rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check").fetchall()]
    return [] if rows == ["ok"] else rows


__all__ = [
    "MigrationChecksumError",
    "MigrationOrderError",
    "connect",
    "integrity_check",
    "migrate",
]
