"""Real-SQLite lifecycle tests for notification-outbox rows."""

from __future__ import annotations

import pytest

from lab_agent.state import notification_outbox, notification_queries
from lab_agent.state_store import StateStore


def _enqueue(store: StateStore):
    return notification_outbox.enqueue(
        store.conn,
        clock=store.clock,
        logical_key="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        canvas_id="canvas-1",
        closure_metadata={"closure_id": "closure-1", "reason": "max_rounds", "round": 3},
        message_id=notification_outbox.message_id_for("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    )


def test_enqueue_is_idempotent_and_survives_restart(tmp_path, clock, rng) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, clock=clock, rng=rng)
    entry = _enqueue(first)
    assert _enqueue(first) == entry
    assert entry.message_id == notification_outbox.message_id_for(entry.logical_key)
    with pytest.raises(notification_outbox.NotificationOutboxConflictError):
        notification_outbox.enqueue(
            first.conn,
            clock=clock,
            logical_key=entry.logical_key,
            canvas_id="canvas-1",
            closure_metadata={"closure_id": "closure-1", "reason": "different"},
            message_id=entry.message_id,
        )
    first.close()

    restarted = StateStore(path, clock=clock, rng=rng)
    try:
        assert notification_outbox.get(restarted.conn, logical_key=entry.logical_key) == entry
    finally:
        restarted.close()


def test_logical_key_and_message_id_can_coexist_across_tenants(store, clock) -> None:
    key = "b" * 64
    metadata = {"closure_id": "closure-1", "reason": "max_rounds", "round": 3}
    first = notification_outbox.enqueue(
        store.conn, clock=clock, tenant_id="tenant-a", logical_key=key, canvas_id="canvas-1",
        closure_metadata=metadata, message_id=notification_outbox.message_id_for(key),
    )
    second = notification_outbox.enqueue(
        store.conn, clock=clock, tenant_id="tenant-b", logical_key=key, canvas_id="canvas-1",
        closure_metadata=metadata, message_id=notification_outbox.message_id_for(key),
    )

    assert (first.tenant_id, second.tenant_id) == ("tenant-a", "tenant-b")
    assert notification_queries.list_due_keys(
        store.conn, tenant_id="tenant-a", now=clock(), limit=1
    ) == [key]
    assert notification_queries.list_due_keys(
        store.conn, tenant_id="tenant-b", now=clock(), limit=1
    ) == [key]


def test_competing_leases_are_fenced_by_generation(store, clock) -> None:
    entry = _enqueue(store)
    first = notification_outbox.lease_due(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="worker-a", lease_ttl_seconds=10,
        reconciliation_window_seconds=30,
    )
    assert first is not None
    assert notification_outbox.lease_due(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="worker-b", lease_ttl_seconds=10,
        reconciliation_window_seconds=30,
    ) is None

    clock.advance(11)
    second = notification_outbox.lease_ambiguous_reconciliation(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="worker-b", lease_ttl_seconds=10,
    )
    assert second is not None and second.lease_generation == first.lease_generation + 1
    with pytest.raises(notification_outbox.StaleNotificationLeaseError):
        notification_outbox.mark_sent(
            store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="worker-a", lease_generation=first.lease_generation
        )
    sent = notification_outbox.mark_reconciled_sent(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="worker-b", lease_generation=second.lease_generation
    )
    assert sent.status is notification_outbox.NotificationStatus.SENT


def test_transient_retry_quarantine_and_targeted_reset(store, clock, rng) -> None:
    entry = _enqueue(store)
    leased = notification_outbox.lease_due(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="worker", lease_ttl_seconds=10,
        reconciliation_window_seconds=30,
    )
    assert leased is not None
    retrying = notification_outbox.mark_transient_retry(
        store.conn, clock=clock, rng=rng, logical_key=entry.logical_key, lease_owner="worker",
        lease_generation=leased.lease_generation, base_seconds=10, max_seconds=60, max_attempts=2,
    )
    assert retrying.status is notification_outbox.NotificationStatus.PENDING
    assert retrying.next_retry_at == clock() + 5
    assert notification_outbox.lease_due(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="worker", lease_ttl_seconds=10,
        reconciliation_window_seconds=30,
    ) is None

    clock.advance(5)
    retry = notification_outbox.lease_due(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="worker", lease_ttl_seconds=10,
        reconciliation_window_seconds=30,
    )
    assert retry is not None
    quarantined = notification_outbox.mark_transient_retry(
        store.conn, clock=clock, rng=rng, logical_key=entry.logical_key, lease_owner="worker",
        lease_generation=retry.lease_generation, base_seconds=10, max_seconds=60, max_attempts=2,
    )
    assert quarantined.status is notification_outbox.NotificationStatus.QUARANTINED
    assert quarantined.failure_category is notification_outbox.FailureCategory.SMTP_TRANSIENT
    assert notification_outbox.list_records(store.conn, status=notification_outbox.NotificationStatus.QUARANTINED) == [quarantined]
    reset = notification_outbox.reset_quarantined(store.conn, clock=clock, logical_key=entry.logical_key)
    assert reset.status is notification_outbox.NotificationStatus.PENDING and reset.attempt_count == 0


def test_due_and_expiry_queries_use_bounded_indexes(store, clock) -> None:
    queries = (
        (
            "SELECT logical_key FROM notification_outbox WHERE tenant_id=? AND status='pending' "
            "AND next_retry_at IS NOT NULL AND next_retry_at<=? "
            "ORDER BY next_retry_at, created_at, logical_key LIMIT ?",
            ("default", clock(), 1), "idx_notification_outbox_pending_retry",
        ),
        (
            "SELECT logical_key FROM notification_outbox WHERE tenant_id=? AND status='pending' "
            "AND next_retry_at IS NULL ORDER BY created_at, logical_key LIMIT ?",
            ("default", 1), "idx_notification_outbox_pending_fresh",
        ),
        (
            "UPDATE notification_outbox SET status='ambiguous' "
            "WHERE tenant_id=? AND status='sending' AND lease_expires_at<=?",
            ("default", clock()), "idx_notification_outbox_sending_expiry",
        ),
        (
            "UPDATE notification_outbox SET status='quarantined' "
            "WHERE tenant_id=? AND status='ambiguous' AND reconciliation_deadline<=? "
            "AND (lease_expires_at IS NULL OR lease_expires_at<=?)",
            ("default", clock(), clock()), "idx_notification_outbox_reconciliation",
        ),
    )
    for sql, arguments, index_name in queries:
        plan = store.conn.execute(f"EXPLAIN QUERY PLAN {sql}", arguments).fetchall()
        details = " ".join(str(row["detail"]) for row in plan)
        assert index_name in details
        assert "TEMP B-TREE" not in details
    assert notification_queries.list_due_keys(store.conn, now=clock(), limit=1) == []


def test_ambiguous_send_never_returns_to_send_queue_after_deadline(store, clock) -> None:
    entry = _enqueue(store)
    leased = notification_outbox.lease_due(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="worker", lease_ttl_seconds=10,
        reconciliation_window_seconds=30,
    )
    assert leased is not None
    ambiguous = notification_outbox.mark_ambiguous(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="worker",
        lease_generation=leased.lease_generation, reconciliation_window_seconds=5,
    )
    assert ambiguous.status is notification_outbox.NotificationStatus.AMBIGUOUS
    assert notification_outbox.lease_due(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="other", lease_ttl_seconds=10,
        reconciliation_window_seconds=30,
    ) is None
    clock.advance(5)
    assert notification_outbox.lease_ambiguous_reconciliation(
        store.conn, clock=clock, logical_key=entry.logical_key, lease_owner="other", lease_ttl_seconds=10,
    ) is None
    assert notification_outbox.get(store.conn, logical_key=entry.logical_key).status is notification_outbox.NotificationStatus.QUARANTINED
