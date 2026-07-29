"""Adversarial same-tenant canvas isolation for Phase 9 P7a."""

from __future__ import annotations

import argparse

import pytest

from lab_agent import admin, cli
from lab_agent.config import Settings
from lab_agent.notifications import NotificationEnvelope
from lab_agent.runtime import RuntimeContext, build_runtime_context, close_runtime_context
from lab_agent.state_store import StateStore
from lab_agent.tenant import CanvasAccessDeniedError, TenantContext

TENANT = "tenant-a"
DOMAIN = "research.example"
A = "canvas-a"
B = "canvas-b"


def _context(*canvases: str) -> TenantContext:
    return TenantContext(TENANT, canvases, DOMAIN)


def _seed_b(path) -> str:
    store = StateStore(path, tenant_context=_context(A, B))
    try:
        store.prepare_intent(idempotency_key="intent-b", canvas_id=B, kind="write", input_hash="b")
        store.reserve_budget(
            reservation_id="budget-b", intent_key="intent-b", canvas_id=B, run_id="run-b",
            tokens=1, cost_usd=0.1, run_token_limit=None, run_cost_limit=None,
            canvas_token_limit=None, canvas_cost_limit=None,
        )
        notification = store.enqueue_notification(NotificationEnvelope(
            canvas_id=B, trigger_id="trigger-b", closure_id="close-b", round_index=1,
            reason="budget", tenant_id=TENANT,
        ))
        store.append_audit_event(B, "seed", {})
        return notification.logical_key
    finally:
        store.close()


def test_bound_store_cannot_be_unbound_or_rebound(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db", tenant_context=_context(A))
    try:
        with pytest.raises(RuntimeError, match="immutable"):
            store.bind_tenant_context(None)
        with pytest.raises(RuntimeError, match="immutable"):
            store.bind_tenant_context(_context(B))
    finally:
        store.close()

    legacy_store = StateStore(tmp_path / "legacy.db")
    try:
        with pytest.raises(RuntimeError, match="immutable"):
            legacy_store.bind_tenant_context(_context(A))
    finally:
        legacy_store.close()


def test_same_tenant_disallowed_key_only_control_plane_operations_fail(tmp_path) -> None:
    path = tmp_path / "state.db"
    notification_key = _seed_b(path)
    store = StateStore(path, tenant_context=_context(A))
    try:
        with pytest.raises(CanvasAccessDeniedError):
            store.ensure_attempt(B, "attempt-b")
        with pytest.raises(CanvasAccessDeniedError):
            store.get_intent("intent-b")
        with pytest.raises(CanvasAccessDeniedError):
            store.mark_intent_submitted("intent-b")
        with pytest.raises(CanvasAccessDeniedError):
            store.settle_budget("budget-b", action="released")
        with pytest.raises(CanvasAccessDeniedError):
            store.get_notification(notification_key)
        with pytest.raises(CanvasAccessDeniedError):
            store.reset_quarantined_notification(notification_key)
        assert store.list_incomplete_intents() == []
        assert store.list_notification_records() == []
        assert store.list_audit_events() == []
    finally:
        store.close()


def test_bound_operator_integrity_and_backup_require_explicit_global_authority(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db", tenant_context=_context(A))
    ctx = RuntimeContext(
        store=store, runtime_instance_id="rt", settings=Settings(), tenant_context=_context(A)
    )
    try:
        assert admin.check_integrity(ctx) == 0
        with pytest.raises(PermissionError):
            admin.backup(ctx, str(tmp_path / "backup.db"), global_authority=True)
    finally:
        store.close()


def test_runtime_binds_settings_scope_before_startup_verification(tmp_path) -> None:
    settings = Settings(
        state_db_path=str(tmp_path / "state.db"), tenant_id=TENANT,
        credential_domain=DOMAIN, allowed_canvas_ids=[A],
    )
    ctx = build_runtime_context(settings)
    try:
        assert ctx.store.tenant_context == _context(A)
        assert ctx.tenant_context == _context(A)
    finally:
        close_runtime_context(ctx)


@pytest.mark.asyncio
async def test_cli_nondefault_tenant_cannot_authorize_requested_canvas(monkeypatch) -> None:
    settings = Settings(tenant_id=TENANT, credential_domain=DOMAIN)
    built = False

    def build_runtime(_: Settings) -> object:
        nonlocal built
        built = True
        raise AssertionError("must reject before building runtime")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "build_runtime_context", build_runtime)
    result = await cli._run(argparse.Namespace(command="once", canvas=A, provider=None))
    assert result == 1
    assert not built
