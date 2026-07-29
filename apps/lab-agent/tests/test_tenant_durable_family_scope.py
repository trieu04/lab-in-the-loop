"""Tenant isolation and default-tenant compatibility for durable families."""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_agent.notifications import NotificationEnvelope
from lab_agent.state_store import StateStore
from lab_agent.tenant import TenantContext


def _context(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id, ["canvas-shared"], "research.example")


def _envelope() -> NotificationEnvelope:
    return NotificationEnvelope(
        canvas_id="canvas-shared",
        trigger_id="trigger-1",
        closure_id="closure-1",
        round_index=1,
        reason="max_rounds",
    )


def test_budget_reservation_identity_and_totals_are_tenant_scoped(tmp_path, clock, rng) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, clock=clock, rng=rng, tenant_context=_context("tenant-a"))
    second = StateStore(path, clock=clock, rng=rng, tenant_context=_context("tenant-b"))
    try:
        args = {
            "reservation_id": "reservation-1", "intent_key": "intent-1",
            "canvas_id": "canvas-shared", "run_id": "run-1", "tokens": 10,
            "cost_usd": 0.25, "run_token_limit": 20, "run_cost_limit": 1.0,
            "canvas_token_limit": 20, "canvas_cost_limit": 1.0,
        }
        reserved_a = first.reserve_budget(**args)
        reserved_b = second.reserve_budget(**args)

        assert reserved_a.tenant_id == "tenant-a"
        assert reserved_b.tenant_id == "tenant-b"
        assert first.budget_totals("canvas-shared") == (0, 0.0, 10, 0.25)
        assert second.budget_totals("canvas-shared") == (0, 0.0, 10, 0.25)
        with pytest.raises(RuntimeError, match="reservation 'other-tenant' is missing"):
            first.settle_budget("other-tenant", action="released")
    finally:
        first.close()
        second.close()


def test_intent_identity_reconciliation_and_reads_are_tenant_scoped(tmp_path, clock, rng) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, clock=clock, rng=rng, tenant_context=_context("tenant-a"))
    second = StateStore(path, clock=clock, rng=rng, tenant_context=_context("tenant-b"))
    try:
        args = {
            "idempotency_key": "intent-shared", "canvas_id": "canvas-shared",
            "kind": "create_note", "input_hash": "hash-a",
        }
        intent_a = first.prepare_intent(**args)
        intent_b = second.prepare_intent(**args)
        second.prepare_intent(
            idempotency_key="other-tenant", canvas_id="canvas-shared",
            kind="create_note", input_hash="hash-b",
        )

        assert intent_a.tenant_id == "tenant-a"
        assert intent_b.tenant_id == "tenant-b"
        assert first.get_intent("other-tenant") is None
        with pytest.raises(RuntimeError, match="intent 'other-tenant' not found"):
            first.mark_intent_reconciled("other-tenant")
    finally:
        first.close()
        second.close()


def test_notification_outbox_claims_queries_and_admin_facade_are_tenant_scoped(tmp_path, clock, rng) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, clock=clock, rng=rng, tenant_context=_context("tenant-a"))
    second = StateStore(path, clock=clock, rng=rng, tenant_context=_context("tenant-b"))
    try:
        record_a = first.enqueue_notification(_envelope())
        record_b = second.enqueue_notification(_envelope())

        assert record_a.tenant_id == "tenant-a"
        assert record_b.tenant_id == "tenant-b"
        assert record_a.logical_key != record_b.logical_key
        assert first.list_notification_records() == [record_a]
        assert second.list_notification_records() == [record_b]
        lease_a = first.lease_due_notification(
            record_a.logical_key, lease_owner="worker-a", lease_ttl_seconds=10,
            reconciliation_window_seconds=30,
        )
        lease_b = second.lease_due_notification(
            record_b.logical_key, lease_owner="worker-b", lease_ttl_seconds=10,
            reconciliation_window_seconds=30,
        )
        assert lease_a is not None and lease_b is not None
    finally:
        first.close()
        second.close()


def test_migration_014_preserves_default_tenant_rows(tmp_path, clock, rng) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    source = Path(__file__).resolve().parents[1] / "lab_agent" / "migrations"
    for migration in source.glob("0*.sql"):
        if int(migration.name[:3]) <= 13:
            (migrations / migration.name).write_text(migration.read_text())
    path = tmp_path / "state.db"
    legacy = StateStore(path, clock=clock, rng=rng, migrations_dir=migrations)
    try:
        legacy.reserve_budget(
            reservation_id="legacy", intent_key="legacy", canvas_id="canvas", run_id="run",
            tokens=1, cost_usd=0.1, run_token_limit=2, run_cost_limit=1.0,
            canvas_token_limit=2, canvas_cost_limit=1.0,
        )
        legacy.prepare_intent(
            idempotency_key="legacy", canvas_id="canvas", kind="create_note", input_hash="hash",
        )
        legacy_notification = legacy.enqueue_notification(
            NotificationEnvelope(
                canvas_id="canvas", trigger_id="trigger", closure_id="closure",
                round_index=0, reason="max_rounds",
            )
        )
        legacy.conn.execute(
            "INSERT INTO orchestrator_edges (tenant_id, canvas_id, connector_id, kind, round, created_at) "
            "VALUES ('default', 'canvas', 'connector', 'setup', 1, ?)", (clock(),)
        )
    finally:
        legacy.close()
    upgraded = StateStore(path, clock=clock, rng=rng)
    try:
        assert upgraded.get_intent("legacy").tenant_id == "default"
        assert upgraded.get_notification(legacy_notification.logical_key).tenant_id == "default"
        assert upgraded.get_edge("canvas", "connector").tenant_id == "default"
        assert upgraded.budget_totals("canvas") == (0, 0.0, 1, 0.1)
    finally:
        upgraded.close()


def test_core_dtos_and_connector_identity_are_tenant_scoped(tmp_path, clock, rng) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, clock=clock, rng=rng, tenant_context=_context("tenant-a"))
    second = StateStore(path, clock=clock, rng=rng, tenant_context=_context("tenant-b"))
    try:
        attempt_a = first.ensure_attempt("canvas-shared", "trigger")
        attempt_b = second.ensure_attempt("canvas-shared", "trigger")
        lease_a = first.acquire_canvas_lease("canvas-shared", runtime_instance_id="a", ttl_seconds=10)
        lease_b = second.acquire_canvas_lease("canvas-shared", runtime_instance_id="b", ttl_seconds=10)
        edge_a = first.record_edge("canvas-shared", "connector", kind="setup", round=1)
        edge_b = second.record_edge("canvas-shared", "connector", kind="setup", round=1)

        assert (attempt_a.tenant_id, attempt_b.tenant_id) == ("tenant-a", "tenant-b")
        assert (lease_a.tenant_id, lease_b.tenant_id) == ("tenant-a", "tenant-b")
        assert (edge_a.tenant_id, edge_b.tenant_id) == ("tenant-a", "tenant-b")
    finally:
        first.close()
        second.close()


def test_legacy_store_uses_default_tenant_for_all_durable_families(store: StateStore) -> None:
    reservation = store.reserve_budget(
        reservation_id="legacy-reservation", intent_key="legacy-intent", canvas_id="canvas",
        run_id="run", tokens=1, cost_usd=0.1, run_token_limit=10, run_cost_limit=1.0,
        canvas_token_limit=10, canvas_cost_limit=1.0,
    )
    intent = store.prepare_intent(
        idempotency_key="legacy-key", canvas_id="canvas", kind="create_note", input_hash="hash",
    )
    notification = store.enqueue_notification(
        NotificationEnvelope(
            canvas_id="canvas", trigger_id="trigger", closure_id="closure",
            round_index=0, reason="max_rounds",
        )
    )

    assert (reservation.tenant_id, intent.tenant_id, notification.tenant_id) == (
        "default", "default", "default",
    )
