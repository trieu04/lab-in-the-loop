"""Forward migration coverage for durable submitted model-call intents."""

from __future__ import annotations

import shutil
from pathlib import Path

from lab_agent.state_store import IntentStatus, StateStore

MIGRATIONS = Path(__file__).parents[1] / "lab_agent" / "migrations"


def _insert_intent(
    store: StateStore, key: str, kind: str, status: str, *, external_id: str | None = None
) -> None:
    store.conn.execute(
        "INSERT INTO side_effect_intents "
        "(idempotency_key, canvas_id, kind, input_hash, status, external_id, "
        "attempt_count, created_at, updated_at) VALUES (?, 'canvas', ?, 'hash', ?, ?, 0, 1, 1)",
        (key, kind, status, external_id),
    )


def test_upgrade_preserves_rows_and_promotes_legacy_pending_model_calls(
    tmp_path, clock, rng
) -> None:
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    for name in ("001_durable_harness.sql", "002_artifact_documents.sql"):
        shutil.copy2(MIGRATIONS / name, migration_dir / name)
    db_path = tmp_path / "state.db"
    legacy = StateStore(db_path, clock=clock, rng=rng, migrations_dir=migration_dir)
    _insert_intent(legacy, "model-pending", "model_call", "pending")
    _insert_intent(legacy, "canvas-pending", "create_browser", "pending")
    _insert_intent(legacy, "model-executed", "model_call", "executed", external_id="req")
    legacy.close()

    shutil.copy2(MIGRATIONS / "003_model_call_submitted.sql", migration_dir)
    upgraded = StateStore(db_path, clock=clock, rng=rng, migrations_dir=migration_dir)
    try:
        versions = upgraded.conn.execute(
            "SELECT version, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert [row["version"] for row in versions] == [1, 2, 3]
        assert all(len(row["checksum"]) == 64 for row in versions)
        assert upgraded.get_intent("model-pending").status is IntentStatus.SUBMITTED
        assert upgraded.get_intent("canvas-pending").status is IntentStatus.PENDING
        assert upgraded.get_intent("model-executed").status is IntentStatus.EXECUTED
        assert upgraded.get_intent("model-executed").external_id == "req"
        assert upgraded.integrity_check() == []
    finally:
        upgraded.close()
