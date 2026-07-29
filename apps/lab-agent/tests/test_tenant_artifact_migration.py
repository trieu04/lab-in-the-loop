"""Migration 016 preserves the old artifact graph under tenant ``default``."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from lab_agent.artifact_store import ArtifactStore
from lab_agent.state_store import StateStore

TOKEN = "legacy-capability"


def _legacy_migrations(destination: Path) -> None:
    source = Path(__file__).parents[1] / "lab_agent" / "migrations"
    destination.mkdir()
    for migration in source.glob("*.sql"):
        if int(migration.name[:3]) <= 15:
            shutil.copy(migration, destination / migration.name)


def test_migration_016_preserves_legacy_artifact_graph_as_default_tenant(tmp_path) -> None:
    db_path = tmp_path / "state.db"
    migration_dir = tmp_path / "legacy-migrations"
    _legacy_migrations(migration_dir)
    legacy = StateStore(db_path, migrations_dir=migration_dir)
    opaque_id = "legacy-artifact"
    legacy.conn.execute(
        "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (opaque_id, "canvas", "legacy-key", "setup", "DRAFT", 0, 1, "hash", 1.0, 1.0, "default"),
    )
    legacy.conn.execute(
        "INSERT INTO artifact_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            opaque_id,
            1,
            '{"old":true}',
            '{"extra":{},"tags":[]}',
            '{"model_name":"","provider":"claude","source_widget_id":"","trigger_id":""}',
            "hash",
            1.0,
        ),
    )
    legacy.conn.execute(
        "INSERT INTO artifact_tokens VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            hashlib.sha256(TOKEN.encode()).hexdigest(),
            opaque_id,
            "canvas",
            "active",
            1.0,
            None,
            "default",
        ),
    )
    legacy.conn.execute(
        "INSERT INTO artifact_widgets VALUES (?, ?, ?, ?, ?, ?)",
        (opaque_id, "canvas", "widget", 1.0, 1.0, "default"),
    )
    legacy.close()

    migrated = StateStore(db_path)
    try:
        document = ArtifactStore(migrated.conn).get_authorized_artifact(opaque_id, token=TOKEN)
        version_rows = migrated.conn.execute("SELECT tenant_id FROM artifact_versions").fetchall()
        foreign_keys = migrated.conn.execute("PRAGMA foreign_key_list(artifact_tokens)").fetchall()

        assert document is not None and document.tenant_id == "default"
        assert document.widget_id == "widget" and document.payload == {"old": True}
        assert [row["tenant_id"] for row in version_rows] == ["default"]
        assert any(row["table"] == "artifacts" for row in foreign_keys)
    finally:
        migrated.close()
