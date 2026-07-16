"""Ingestion's local raw-cache and SQLite artifacts must stay untracked."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_default_ingestion_database_and_cache_paths_are_gitignored() -> None:
    root = Path(__file__).resolve().parents[3]
    paths = (
        "apps/canvus-mcp/.state/ingestion.db",
        "apps/canvus-mcp/.state/ingestion-cache/immutable-raw-bytes",
    )
    for path in paths:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "-q", path],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
