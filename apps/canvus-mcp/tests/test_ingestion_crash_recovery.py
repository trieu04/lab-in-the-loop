"""SQLite work claims survive an ungraceful process exit."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from canvus_mcp.ingestion_store import IngestionStore


def test_crashed_process_claim_is_recovered_with_attempt_history(tmp_path: Path) -> None:
    db_path = tmp_path / "ingestion.db"
    script = """
from pathlib import Path
from canvus_mcp.ingestion_store import IngestionStore
from canvus_mcp.ingestion_types import UnitSpec
store = IngestionStore(Path(__import__('sys').argv[1]), clock=lambda: 100.0, token_factory=lambda: 'first')
sha = 'a' * 64
store.upsert_asset(sha, size_bytes=1, mime_type='text/plain')
store.create_job(sha, 'v1', 'text/plain', [UnitSpec('whole', 0)])
assert store.claim_next('crashed-worker', lease_seconds=5) is not None
__import__('os')._exit(0)
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    result = subprocess.run([sys.executable, "-c", script, str(db_path)], env=env, check=False)
    assert result.returncode == 0
    store = IngestionStore(db_path, clock=lambda: 106.0, token_factory=lambda: "second")
    try:
        assert store.recover_expired_leases() == 1
        claim = store.claim_next("recovered-worker", lease_seconds=5)
        assert claim is not None and claim.generation == 2 and claim.attempt_number == 2
        history = store.conn.execute("SELECT generation, status FROM attempts ORDER BY generation").fetchall()
        assert [tuple(row) for row in history] == [(1, "expired"), (2, "running")]
    finally:
        store.close()
