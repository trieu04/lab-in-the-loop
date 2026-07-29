"""Adversarial durable-state tests for the Phase 9 tenant boundary."""

from __future__ import annotations

import argparse

import pytest

from lab_agent import cli
from lab_agent.config import Settings
from lab_agent.state_store import StateStore
from lab_agent.tenant import CanvasAccessDeniedError, TenantContext


def _context(tenant_id: str, *canvas_ids: str) -> TenantContext:
    return TenantContext(tenant_id, canvas_ids, "research.example")


def test_tenant_bound_store_rejects_unallowed_canvas_before_writing(tmp_path) -> None:
    store = StateStore(
        tmp_path / "state.db",
        tenant_context=_context("tenant-a", "canvas-a"),
    )

    with pytest.raises(CanvasAccessDeniedError):
        store.ensure_attempt("canvas-b", "idea:1")

    with pytest.raises(CanvasAccessDeniedError):
        store.get_attempt("canvas-b", "idea:1")
    assert store.conn.execute("SELECT COUNT(*) FROM workflow_attempts").fetchone()[0] == 0


def test_tenant_bound_stores_isolate_same_canvas_durable_rows(tmp_path) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, tenant_context=_context("tenant-a", "canvas-shared"))
    second = StateStore(path, tenant_context=_context("tenant-b", "canvas-shared"))

    first.ensure_attempt("canvas-shared", "idea:1")
    second.ensure_attempt("canvas-shared", "idea:1")

    assert first.get_attempt("canvas-shared", "idea:1") is not None
    assert second.get_attempt("canvas-shared", "idea:1") is not None
    assert (
        first.conn.execute(
            "SELECT COUNT(*) FROM workflow_attempts "
            "WHERE canvas_id='canvas-shared'"
        ).fetchone()[0]
        == 2
    )


def test_tenant_bound_leases_are_isolated_by_tenant_and_canvas(tmp_path) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, tenant_context=_context("tenant-a", "canvas-shared"))
    second = StateStore(path, tenant_context=_context("tenant-b", "canvas-shared"))

    first.acquire_canvas_lease(
        "canvas-shared", runtime_instance_id="runtime-a", ttl_seconds=60
    )
    second.acquire_canvas_lease(
        "canvas-shared", runtime_instance_id="runtime-b", ttl_seconds=60
    )

    assert first.get_canvas_lease("canvas-shared").runtime_instance_id == "runtime-a"
    assert second.get_canvas_lease("canvas-shared").runtime_instance_id == "runtime-b"


@pytest.mark.asyncio
async def test_cli_rejects_denied_canvas_before_runtime_construction(monkeypatch) -> None:
    settings = Settings(allowed_canvas_ids=["canvas-a"])
    runtime_built = False

    def build_runtime(_: Settings) -> object:
        nonlocal runtime_built
        runtime_built = True
        raise AssertionError("runtime must not be constructed")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_runtime_context", build_runtime)

    result = await cli._run(
        argparse.Namespace(command="once", canvas="canvas-b", provider=None)
    )

    assert result == 1
    assert not runtime_built
