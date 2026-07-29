"""Operator scripts enforce configured tenant/canvas boundaries before I/O."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from lab_agent import artifact_migration as migration
from lab_agent.config import Settings
from lab_agent.demo_seed import build_seed_title, seed_demo
from lab_agent.state_store import StateStore
from lab_agent.tenant import CanvasAccessDeniedError, TenantContext
from tests.fakes import FakeMCP

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "seed-demo-canvas.py"


class NoMCPCalls:
    """Fails if scope validation reaches an external MCP method."""

    def __init__(self) -> None:
        self.calls = 0

    async def call_tool(self, _name: str, _arguments: dict[str, Any]) -> str:
        self.calls += 1
        raise AssertionError("unauthorized scope reached MCP")


def _settings() -> Settings:
    return Settings(
        tenant_id="tenant-a",
        credential_domain="tenant-a.example",
        allowed_canvas_ids=["canvas-a"],
    )


def _load_seed() -> Any:
    spec = importlib.util.spec_from_file_location("seed_demo", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("apply", [False, True])
async def test_migration_rejects_unauthorized_canvas_before_mcp_scan(apply: bool) -> None:
    mcp = NoMCPCalls()

    with pytest.raises(CanvasAccessDeniedError):
        await migration.run_migration(
            mcp, None, _settings(), canvas_id="canvas-b", apply=apply
        )

    assert mcp.calls == 0


def test_seed_rejects_unauthorized_canvas_before_client_creation(monkeypatch) -> None:
    seed = _load_seed()
    created = False

    def unexpected_client(*_args: object, **_kwargs: object) -> object:
        nonlocal created
        created = True
        raise AssertionError("unauthorized scope created MCP client")

    monkeypatch.setattr(seed, "Settings", _settings)
    monkeypatch.setattr(seed, "MCPClient", unexpected_client)
    args = argparse.Namespace(
        canvas="canvas-b", ragcluster_widget_id="cluster", idea_text="{idea: demo}",
        idea_key="demo", title=None, x=0.0, y=0.0, apply=False, json=False,
    )

    with pytest.raises(CanvasAccessDeniedError):
        asyncio.run(seed._seed(args))

    assert created is False


def test_seed_lock_paths_are_tenant_qualified() -> None:
    seed = _load_seed()
    args = argparse.Namespace(canvas="canvas", ragcluster_widget_id="cluster", idea_key="demo")

    assert seed._seed_lock_path(args, "tenant-a") != seed._seed_lock_path(args, "tenant-b")


class SeedMCP(FakeMCP):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "check_ragcluster_connections":
            return '{"clusters":[{"widget_id":"cluster","found":true,"is_ragcluster":true}]}'
        return await super().call_tool(name, arguments)


async def test_seed_apply_uses_durable_audited_note_and_connector(tmp_path) -> None:
    context = TenantContext("tenant-a", ["canvas-a"], "tenant-a.example")
    store = StateStore(tmp_path / "state.db", tenant_context=context)
    args = argparse.Namespace(
        canvas="canvas-a", ragcluster_widget_id="cluster", idea_text="{idea: demo}",
        idea_key="demo", title=None, x=0.0, y=0.0, apply=True,
    )
    try:
        result = await seed_demo(
            SeedMCP(live=True), store, args, tenant_id="tenant-a",
            title=build_seed_title(args, "tenant-a"),
        )
        intents = store.conn.execute("SELECT kind, status FROM side_effect_intents").fetchall()
        audit_events = store.list_audit_events("canvas-a")
    finally:
        store.close()

    assert result["applied"] is True and result["idea_widget_id"]
    assert {tuple(row) for row in intents} == {
        ("create_note_demo_seed", "reconciled"), ("create_connector", "reconciled"),
    }
    assert [event.event for event in audit_events] == ["intent_reconciled", "intent_reconciled"]
