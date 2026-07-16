"""Canvas writer leases, side-effect intents, and orchestrator edges for the
durable SQLite ledger (``lab_agent.state_store``).

Shared ``_Clock``/``_Rng``/``store`` fixtures live in ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest

from lab_agent.state_store import (
    IntentHashMismatchError,
    IntentStatus,
    LeaseHeldByOtherError,
    StateStore,
)
from tests.conftest import _Clock

# ── canvas writer lease ────────────────────────────────────────────────


def test_acquire_canvas_lease_first_time(store: StateStore, clock: _Clock) -> None:
    lease = store.acquire_canvas_lease("c1", runtime_instance_id="inst-a", ttl_seconds=60)
    assert lease.runtime_instance_id == "inst-a"
    assert lease.expires_at == pytest.approx(clock.now + 60)


def test_acquire_canvas_lease_renews_same_owner(store: StateStore, clock: _Clock) -> None:
    store.acquire_canvas_lease("c1", runtime_instance_id="inst-a", ttl_seconds=60)
    clock.advance(10)
    renewed = store.acquire_canvas_lease("c1", runtime_instance_id="inst-a", ttl_seconds=60)
    assert renewed.expires_at == pytest.approx(clock.now + 60)


def test_acquire_canvas_lease_blocked_by_other_live_owner(store: StateStore) -> None:
    store.acquire_canvas_lease("c1", runtime_instance_id="inst-a", ttl_seconds=60)
    with pytest.raises(LeaseHeldByOtherError):
        store.acquire_canvas_lease("c1", runtime_instance_id="inst-b", ttl_seconds=60)
    # Regression guard: same double-rollback class of bug as StaleLeaseError.
    assert store.get_canvas_lease("c1").runtime_instance_id == "inst-a"
    store.ensure_attempt("c2", "unrelated")  # connection must still work


def test_acquire_canvas_lease_available_after_expiry(store: StateStore, clock: _Clock) -> None:
    store.acquire_canvas_lease("c1", runtime_instance_id="inst-a", ttl_seconds=60)
    clock.advance(61)
    taken = store.acquire_canvas_lease("c1", runtime_instance_id="inst-b", ttl_seconds=60)
    assert taken.runtime_instance_id == "inst-b"


def test_release_canvas_lease_removes_row(store: StateStore) -> None:
    store.acquire_canvas_lease("c1", runtime_instance_id="inst-a", ttl_seconds=60)
    assert store.release_canvas_lease("c1", runtime_instance_id="inst-a") is True
    assert store.get_canvas_lease("c1") is None


def test_release_canvas_lease_noop_for_non_owner(store: StateStore) -> None:
    store.acquire_canvas_lease("c1", runtime_instance_id="inst-a", ttl_seconds=60)
    assert store.release_canvas_lease("c1", runtime_instance_id="inst-b") is False
    assert store.get_canvas_lease("c1") is not None


# ── side-effect intents ─────────────────────────────────────────────────


def test_prepare_intent_creates_pending(store: StateStore) -> None:
    intent = store.prepare_intent(idempotency_key="k1", canvas_id="c1", kind="create_note", input_hash="h1")
    assert intent.status == IntentStatus.PENDING
    assert intent.attempt_count == 0


def test_prepare_intent_same_hash_is_idempotent(store: StateStore) -> None:
    first = store.prepare_intent(idempotency_key="k1", canvas_id="c1", kind="create_note", input_hash="h1")
    second = store.prepare_intent(idempotency_key="k1", canvas_id="c1", kind="create_note", input_hash="h1")
    assert first == second


def test_prepare_intent_hash_mismatch_fails_closed_and_connection_stays_usable(store: StateStore) -> None:
    store.prepare_intent(idempotency_key="k1", canvas_id="c1", kind="create_note", input_hash="h1")
    with pytest.raises(IntentHashMismatchError):
        store.prepare_intent(idempotency_key="k1", canvas_id="c1", kind="create_note", input_hash="h2")
    # Regression guard: same double-rollback class of bug as StaleLeaseError.
    assert store.get_intent("k1").input_hash == "h1"
    store.ensure_attempt("c2", "unrelated")  # connection must still work


def test_intent_execute_then_reconcile_flow(store: StateStore) -> None:
    store.prepare_intent(idempotency_key="k1", canvas_id="c1", kind="create_note", input_hash="h1")
    executed = store.mark_intent_executed("k1", external_id="note-123")
    assert executed.status == IntentStatus.EXECUTED
    assert executed.external_id == "note-123"

    reconciled = store.mark_intent_reconciled("k1")
    assert reconciled.status == IntentStatus.RECONCILED
    assert reconciled.reconciled_at is not None


def test_mark_intent_failed_does_not_claim_completion(store: StateStore) -> None:
    store.prepare_intent(idempotency_key="k1", canvas_id="c1", kind="create_note", input_hash="h1")
    failed = store.mark_intent_failed("k1", error="provider timeout", next_retry_at=2000.0)
    assert failed.status == IntentStatus.FAILED
    assert failed.last_error == "provider timeout"
    assert failed.attempt_count == 1


def test_list_incomplete_intents_excludes_reconciled(store: StateStore) -> None:
    store.prepare_intent(idempotency_key="k1", canvas_id="c1", kind="create_note", input_hash="h1")
    store.prepare_intent(idempotency_key="k2", canvas_id="c1", kind="create_note", input_hash="h2")
    store.mark_intent_executed("k2", external_id="note-2")
    store.mark_intent_reconciled("k2")

    incomplete = store.list_incomplete_intents("c1")
    assert {i.idempotency_key for i in incomplete} == {"k1"}


# ── orchestrator edges ───────────────────────────────────────────────────


def test_record_edge_then_get(store: StateStore) -> None:
    store.record_edge("c1", "conn1", kind="setup", round=1)
    edge = store.get_edge("c1", "conn1")
    assert edge is not None
    assert edge.kind == "setup"
    assert edge.round == 1


def test_record_edge_upserts_kind_and_round(store: StateStore) -> None:
    store.record_edge("c1", "conn1", kind="setup", round=1)
    store.record_edge("c1", "conn1", kind="result", round=2)
    edge = store.get_edge("c1", "conn1")
    assert edge.kind == "result"
    assert edge.round == 2


def test_list_edges_scoped_to_canvas_ordered_by_round(store: StateStore) -> None:
    store.record_edge("c1", "conn2", kind="result", round=2)
    store.record_edge("c1", "conn1", kind="setup", round=1)
    store.record_edge("c2", "conn3", kind="setup", round=1)
    edges = store.list_edges("c1")
    assert [e.connector_id for e in edges] == ["conn1", "conn2"]
