"""Tenant isolation and legacy migration for result and continuation records."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from lab_agent.models.experiment import ExperimentResult
from lab_agent.state_store import StateStore
from lab_agent.tenant import CanvasAccessDeniedError, TenantContext

_CANVAS = "canvas-shared"
_HASH = "a" * 64


def _context(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id, [_CANVAS], "research.example")


def _result(summary: str) -> ExperimentResult:
    return ExperimentResult(summary=summary, metrics=["signal=1"])


def test_result_and_continuation_records_are_tenant_isolated_and_restart_safe(tmp_path) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, tenant_context=_context("tenant-a"))
    second = StateStore(path, tenant_context=_context("tenant-b"))
    try:
        continuation_a = first.ensure_loop_continuation(
            _CANVAS, "setup:setup", 1, "setup", "result"
        )
        continuation_b = second.ensure_loop_continuation(
            _CANVAS, "setup:setup", 1, "setup", "result"
        )
        generation_a = first.persist_result_generation(
            _CANVAS, "setup", 1, _HASH, _HASH, _result("tenant a")
        )
        generation_b = second.persist_result_generation(
            _CANVAS, "setup", 1, _HASH, _HASH, _result("tenant b")
        )
        first.save_loop_continuation(
            replace(continuation_a, staged_setup_id="next-a", staged_proposal_hash=_HASH)
        )

        assert continuation_a.tenant_id == "tenant-a"
        assert continuation_b.tenant_id == "tenant-b"
        assert continuation_a.run_id != continuation_b.run_id
        assert generation_a.tenant_id == "tenant-a"
        assert generation_b.tenant_id == "tenant-b"
        assert first.get_result_generation(_CANVAS, "setup", 1, _HASH, _HASH) == generation_a
        assert second.get_result_generation(_CANVAS, "setup", 1, _HASH, _HASH) == generation_b
        assert second.get_loop_continuation(_CANVAS, "setup:setup") == continuation_b
        with pytest.raises(CanvasAccessDeniedError):
            first.get_loop_continuation("other-canvas", "setup:setup")
    finally:
        first.close()
        second.close()

    restarted = StateStore(path, tenant_context=_context("tenant-a"))
    try:
        restored = restarted.get_loop_continuation(_CANVAS, "setup:setup")
        assert restored is not None and restored.run_id == continuation_a.run_id
        assert restored.staged_setup_id == "next-a"
    finally:
        restarted.close()


def test_migration_019_preserves_legacy_result_and_continuation_as_default(tmp_path) -> None:
    migrations = tmp_path / "legacy-migrations"
    migrations.mkdir()
    source = Path(__file__).resolve().parents[1] / "lab_agent" / "migrations"
    for migration in source.glob("0*.sql"):
        if int(migration.name[:3]) <= 18:
            (migrations / migration.name).write_text(migration.read_text())

    path = tmp_path / "legacy.db"
    legacy = StateStore(path, migrations_dir=migrations)
    try:
        continuation = legacy.ensure_loop_continuation(_CANVAS, "setup:setup", 1, "setup", "result")
        legacy.persist_result_generation(_CANVAS, "setup", 1, _HASH, _HASH, _result("legacy"))
    finally:
        legacy.close()

    upgraded = StateStore(path)
    try:
        restored = upgraded.get_loop_continuation(_CANVAS, "setup:setup")
        generation = upgraded.get_result_generation(_CANVAS, "setup", 1, _HASH, _HASH)
        assert restored is not None and restored.tenant_id == "default"
        assert restored.run_id == continuation.run_id
        assert generation is not None and generation.tenant_id == "default"
        assert generation.result.summary == "legacy"
    finally:
        upgraded.close()
