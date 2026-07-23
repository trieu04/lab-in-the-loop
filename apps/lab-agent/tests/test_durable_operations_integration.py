"""Operational-surface integration tests for the durable harness.

Drives real on-disk SQLite ``StateStore`` instances (WAL mode, real
migrations) against ``FakeMCP`` -- no mocks of the harness itself. Covers the
operator/runtime surface rather than the write-path crash boundaries: the
single-writer canvas lease, quarantine visibility + operator reset, backup /
restore preserving dedup state, and audit-chain integrity across a full
``process_once`` cycle. No sleeps: lease-expiry and retry determinism come
from injected clocks and ``rng=lambda: 0.0``.

Companion module ``test_durable_recovery_integration.py`` covers the
crash/restart/recovery write-path boundaries.
"""

from __future__ import annotations

import tempfile

from lab_agent import admin
from lab_agent.config import Settings
from lab_agent.runtime import RuntimeContext
from lab_agent.state_store import LeaseHeldByOtherError, StateStore
from lab_agent.watch import process_once
from tests.conftest import IDEA_WORKFLOW, MALFORMED_SETUP, SETUP
from tests.fakes import FakeMCP, ScriptedAdapter


def _settings(**kw):
    """Settings with a configured artifact base URL -- generated Setup/Result/
    Closed nodes are Browser widgets that fail closed without one."""
    return Settings(artifact_public_base_url="https://lab.test", **kw)  # type: ignore[call-arg]


# ── Single-writer canvas lease ───────────────────────────────────────────


async def test_second_runtime_is_blocked_by_a_live_canvas_lease(tmp_path):
    store1 = StateStore(tmp_path / "state.db")
    store2 = StateStore(tmp_path / "state.db")
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    try:
        store1.acquire_canvas_lease("c", runtime_instance_id="rt1", ttl_seconds=120)

        counts = await process_once(mcp, adapter, _settings(), store2, "rt2", "c")  # type: ignore[arg-type]

        assert counts == {"setups": 0, "runs": 0, "loops": 0, "validations": 0}  # denied -> no work attempted
        assert len(mcp.notes) == 1  # only the seed idea note; nothing written
        denied = [e for e in store2.list_audit_events("c") if e.event == "canvas_lease_denied"]
        assert len(denied) == 1
    finally:
        store1.close()
        store2.close()


def test_lease_expiry_allows_a_new_owner_to_acquire():
    clock = {"t": 1000.0}
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(f"{tmp}/state.db", clock=lambda: clock["t"])
        try:
            store.acquire_canvas_lease("c", runtime_instance_id="rt1", ttl_seconds=10)
            clock["t"] += 11  # advance past the TTL -- rt1's lease is now stale

            lease = store.acquire_canvas_lease("c", runtime_instance_id="rt2", ttl_seconds=10)
            assert lease.runtime_instance_id == "rt2"
        finally:
            store.close()


def test_live_lease_from_another_owner_raises_lease_held_by_other():
    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(f"{tmp}/state.db")
        try:
            store.acquire_canvas_lease("c", runtime_instance_id="rt1", ttl_seconds=120)
            try:
                store.acquire_canvas_lease("c", runtime_instance_id="rt2", ttl_seconds=120)
                raise AssertionError("expected LeaseHeldByOtherError")
            except LeaseHeldByOtherError:
                pass
        finally:
            store.close()


# ── Quarantine: visible, resettable, retryable after operator reset ─────────


async def test_quarantine_after_max_attempts_then_operator_reset_recovers(tmp_path):
    settings = _settings(max_attempts=2, retry_base_seconds=0.001, retry_max_seconds=0.001)  # type: ignore[call-arg]
    store = StateStore(tmp_path / "state.db", rng=lambda: 0.0)  # zero jitter -> immediately due retry
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    failing_adapter = ScriptedAdapter({"ExperimentSetup": MALFORMED_SETUP})
    try:
        await process_once(mcp, failing_adapter, settings, store, "rt1", "c")  # attempt 1/2 -> failed
        await process_once(mcp, failing_adapter, settings, store, "rt1", "c")  # attempt 2/2 -> quarantined

        quarantined = store.list_quarantined_attempts("c")
        assert len(quarantined) == 1
        assert quarantined[0].trigger_id == "idea_setup:idea1"

        ctx = RuntimeContext(store=store, runtime_instance_id="rt1", settings=settings)
        assert admin.reset_attempt(ctx, "c", "idea_setup:idea1") == 0
        assert store.list_quarantined_attempts("c") == []

        succeeding_adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
        counts = await process_once(mcp, succeeding_adapter, settings, store, "rt1", "c")
        assert counts["setups"] == 1  # reset attempt is retried and now succeeds
    finally:
        store.close()


# ── Backup / restore preserves durable dedup state ───────────────────────


async def test_backup_restore_preserves_completed_attempt_preventing_duplicate(tmp_path):
    """A completed attempt's dedup must survive backup+restore: replaying
    the *same* stale trigger snapshot against a store restored from backup
    must not re-run already-completed work (the canvas is untouched, so if
    dedup relied on canvas state alone this would still pass -- keeping the
    workflow snapshot static isolates the assertion to the restored ledger)."""
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    settings = _settings()
    store = StateStore(tmp_path / "state.db")
    backup_dest = tmp_path / "backup.db"
    try:
        first = await process_once(mcp, adapter, settings, store, "rt1", "c")  # type: ignore[arg-type]
        assert first["setups"] == 1

        ctx = RuntimeContext(store=store, runtime_instance_id="rt1", settings=settings)
        assert admin.backup(ctx, str(backup_dest)) == 0
    finally:
        store.close()

    restored = StateStore(backup_dest)
    try:
        second = await process_once(mcp, adapter, settings, restored, "rt1", "c")
        assert second["setups"] == 0  # the completed attempt survived backup/restore
        setups = [w for w in mcp.notes.values() if w["title"].startswith("[EXP:Setup")]
        assert len(setups) == 1  # no duplicate
        assert setups[0]["widget_type"] == "Browser"  # generated artifact is a Browser widget now
    finally:
        restored.close()


# ── Audit chain integrity across a realistic multi-event cycle ──────────────


async def test_audit_chain_verifies_after_a_full_process_once_cycle(tmp_path):
    store = StateStore(tmp_path / "state.db")
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    try:
        counts = await process_once(mcp, adapter, _settings(), store, "rt1", "c")  # type: ignore[arg-type]
        assert counts["setups"] == 1
        store.verify_audit_chain()  # does not raise
        events = {e.event for e in store.list_audit_events("c")}
        assert {"canvas_lease_acquired", "attempt_leased", "attempt_completed", "intent_reconciled"} <= events
    finally:
        store.close()
