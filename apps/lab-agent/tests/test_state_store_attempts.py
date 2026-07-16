"""Workflow-attempt lifecycle, lease expiry, backoff, and quarantine for the
durable SQLite ledger (``lab_agent.state_store``).

Shared ``_Clock``/``_Rng``/``store`` fixtures live in ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest

from lab_agent.state_store import (
    AttemptNotQuarantinedError,
    AttemptStatus,
    StaleLeaseError,
    StateStore,
)
from tests.conftest import _Clock, _Rng

# ── workflow attempt lifecycle ────────────────────────────────────────


def test_ensure_attempt_creates_pending(store: StateStore) -> None:
    attempt = store.ensure_attempt("c1", "t1")
    assert attempt.status == AttemptStatus.PENDING
    assert attempt.attempt_count == 0


def test_ensure_attempt_is_idempotent_and_never_resurrects_completed(store: StateStore) -> None:
    store.ensure_attempt("c1", "t1")
    leased = store.lease_due_attempt("c1", "t1", lease_owner="owner-a", lease_ttl_seconds=30)
    assert leased is not None
    assert store.mark_attempt_completed("c1", "t1", lease_owner="owner-a") is True

    # Re-calling ensure_attempt on an already-completed row must not reset it.
    attempt = store.ensure_attempt("c1", "t1")
    assert attempt.status == AttemptStatus.COMPLETED


def test_lease_due_attempt_claims_pending_and_increments_count(store: StateStore) -> None:
    store.ensure_attempt("c1", "t1")
    leased = store.lease_due_attempt("c1", "t1", lease_owner="owner-a", lease_ttl_seconds=30)
    assert leased is not None
    assert leased.status == AttemptStatus.RUNNING
    assert leased.lease_owner == "owner-a"
    assert leased.attempt_count == 1


def test_lease_due_attempt_returns_none_when_lease_still_live(store: StateStore) -> None:
    store.ensure_attempt("c1", "t1")
    store.lease_due_attempt("c1", "t1", lease_owner="owner-a", lease_ttl_seconds=30)
    # Same attempt, still running with an unexpired lease -- not due yet.
    second = store.lease_due_attempt("c1", "t1", lease_owner="owner-b", lease_ttl_seconds=30)
    assert second is None


def test_lease_due_attempt_returns_none_for_missing_row(store: StateStore) -> None:
    assert store.lease_due_attempt("c1", "missing", lease_owner="owner-a", lease_ttl_seconds=30) is None


def test_crashed_running_attempt_becomes_leasable_after_expiry(store: StateStore, clock: _Clock) -> None:
    store.ensure_attempt("c1", "t1")
    store.lease_due_attempt("c1", "t1", lease_owner="owner-a", lease_ttl_seconds=30)
    clock.advance(31)  # simulate owner-a crashing; its lease expires
    reclaimed = store.lease_due_attempt("c1", "t1", lease_owner="owner-b", lease_ttl_seconds=30)
    assert reclaimed is not None
    assert reclaimed.lease_owner == "owner-b"
    assert reclaimed.attempt_count == 2


def test_mark_completed_is_permanent_and_blocks_release(store: StateStore, clock: _Clock) -> None:
    store.ensure_attempt("c1", "t1")
    store.lease_due_attempt("c1", "t1", lease_owner="owner-a", lease_ttl_seconds=30)
    assert store.mark_attempt_completed("c1", "t1", lease_owner="owner-a") is True

    clock.advance(1000)  # even long after any lease would have expired
    assert store.lease_due_attempt("c1", "t1", lease_owner="owner-b", lease_ttl_seconds=30) is None
    assert store.get_attempt("c1", "t1").status == AttemptStatus.COMPLETED


def test_mark_completed_returns_false_for_stale_owner(store: StateStore, clock: _Clock) -> None:
    store.ensure_attempt("c1", "t1")
    store.lease_due_attempt("c1", "t1", lease_owner="owner-a", lease_ttl_seconds=30)
    clock.advance(31)
    store.lease_due_attempt("c1", "t1", lease_owner="owner-b", lease_ttl_seconds=30)

    # owner-a's stale completion must not clobber owner-b's newer lease.
    assert store.mark_attempt_completed("c1", "t1", lease_owner="owner-a") is False
    assert store.get_attempt("c1", "t1").lease_owner == "owner-b"


# ── retry backoff & quarantine ────────────────────────────────────────


def test_mark_failed_schedules_backoff_retry(store: StateStore, clock: _Clock, rng: _Rng) -> None:
    store.ensure_attempt("c1", "t1")
    store.lease_due_attempt("c1", "t1", lease_owner="owner-a", lease_ttl_seconds=30)
    rng.value = 0.5
    attempt = store.mark_attempt_failed(
        "c1", "t1", lease_owner="owner-a", error="boom",
        base_seconds=10.0, max_seconds=100.0, max_attempts=5,
    )
    assert attempt.status == AttemptStatus.FAILED
    assert attempt.last_error == "boom"
    # attempt_count=1 -> ceiling=min(100, 10*2**0)=10 -> delay=0.5*10=5
    assert attempt.next_retry_at == pytest.approx(clock.now + 5.0)


def test_mark_failed_quarantines_after_max_attempts(store: StateStore) -> None:
    store.ensure_attempt("c1", "t1")
    store.lease_due_attempt("c1", "t1", lease_owner="owner-a", lease_ttl_seconds=30)
    attempt = store.mark_attempt_failed(
        "c1", "t1", lease_owner="owner-a", error="final failure",
        base_seconds=1.0, max_seconds=10.0, max_attempts=1,
    )
    assert attempt.status == AttemptStatus.QUARANTINED
    assert attempt.next_retry_at is None


def test_mark_failed_with_stale_lease_raises_and_connection_stays_usable(store: StateStore) -> None:
    store.ensure_attempt("c1", "t1")
    store.lease_due_attempt("c1", "t1", lease_owner="owner-a", lease_ttl_seconds=30)
    with pytest.raises(StaleLeaseError):
        store.mark_attempt_failed(
            "c1", "t1", lease_owner="not-the-owner", error="boom",
            base_seconds=1.0, max_seconds=10.0, max_attempts=5,
        )
    # Regression guard: a manual ROLLBACK-before-raise inside the same try
    # block previously caused the outer `except: ROLLBACK` to run twice,
    # raising OperationalError instead of StaleLeaseError and, worse,
    # leaving the connection unable to start a fresh transaction.
    assert store.get_attempt("c1", "t1").status == AttemptStatus.RUNNING
    store.ensure_attempt("c2", "unrelated")  # connection must still work


def test_reset_quarantined_returns_attempt_to_pending(store: StateStore) -> None:
    store.ensure_attempt("c1", "t1")
    store.lease_due_attempt("c1", "t1", lease_owner="owner-a", lease_ttl_seconds=30)
    store.mark_attempt_failed(
        "c1", "t1", lease_owner="owner-a", error="dead",
        base_seconds=1.0, max_seconds=10.0, max_attempts=1,
    )
    reset = store.reset_quarantined_attempt("c1", "t1")
    assert reset.status == AttemptStatus.PENDING
    assert reset.attempt_count == 0
    assert reset.last_error is None


def test_reset_quarantined_raises_if_not_quarantined(store: StateStore) -> None:
    store.ensure_attempt("c1", "t1")
    with pytest.raises(AttemptNotQuarantinedError):
        store.reset_quarantined_attempt("c1", "t1")


def test_list_quarantined_attempts_scoped_by_canvas(store: StateStore) -> None:
    for canvas_id, trigger_id in (("c1", "t1"), ("c1", "t2"), ("c2", "t1")):
        store.ensure_attempt(canvas_id, trigger_id)
        store.lease_due_attempt(canvas_id, trigger_id, lease_owner="owner-a", lease_ttl_seconds=30)
        store.mark_attempt_failed(
            canvas_id, trigger_id, lease_owner="owner-a", error="dead",
            base_seconds=1.0, max_seconds=10.0, max_attempts=1,
        )
    assert {a.trigger_id for a in store.list_quarantined_attempts("c1")} == {"t1", "t2"}
    assert len(store.list_quarantined_attempts()) == 3
