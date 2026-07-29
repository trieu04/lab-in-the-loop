"""CLI notification administration retains the configured tenant boundary."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from lab_agent import cli
from lab_agent.config import Settings
from lab_agent.state_store import StateStore
from lab_agent.tenant import TenantContext


@pytest.mark.asyncio
async def test_notification_admin_binds_configured_tenant(monkeypatch, tmp_path) -> None:
    store = StateStore(
        tmp_path / "state.db",
        tenant_context=TenantContext("tenant-a", ["canvas-a"], "research.example"),
    )
    settings = Settings(
        tenant_id="tenant-a",
        credential_domain="research.example",
        allowed_canvas_ids=["canvas-a"],
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_runtime_context", lambda _: SimpleNamespace(store=store))
    monkeypatch.setattr(cli, "close_runtime_context", lambda _: None)
    try:
        result = await cli._run(
            argparse.Namespace(command="notification-status", canvas=None)
        )
        assert result == 0
        assert store.tenant_id == "tenant-a"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_tenant_notification_admin_requires_configured_canvas_scope(monkeypatch) -> None:
    settings = Settings(tenant_id="tenant-a", credential_domain="research.example")
    built = False

    def build_runtime(_: Settings) -> object:
        nonlocal built
        built = True
        raise AssertionError("tenant scope must be validated before startup")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_runtime_context", build_runtime)

    result = await cli._run(argparse.Namespace(command="notification-status", canvas=None))

    assert result == 1
    assert not built
