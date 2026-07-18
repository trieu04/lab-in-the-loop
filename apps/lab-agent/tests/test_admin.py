"""Tests for lab_agent.admin: the operator commands behind the CLI's
``integrity``/``list-quarantined``/``reset``/``backup`` subcommands.

Drives quarantine through the real durable-attempt state machine (fail an
attempt with ``max_attempts=1``) rather than hand-crafting rows, so these
tests exercise the same path an actual retry-exhausted trigger takes.
"""

from __future__ import annotations

import json

from lab_agent import admin
from lab_agent.config import Settings
from lab_agent.runtime import RuntimeContext
from lab_agent.state_store import AttemptStatus, StateStore

RUNTIME_ID = "test-runtime"


def _ctx(store: StateStore) -> RuntimeContext:
    return RuntimeContext(store=store, runtime_instance_id=RUNTIME_ID, settings=Settings())


def _quarantine(store: StateStore, canvas_id: str, trigger_id: str) -> None:
    """Drive one attempt to quarantine via the real retry state machine
    (``max_attempts=1`` -- a single failure exhausts it)."""
    store.ensure_attempt(canvas_id, trigger_id)
    store.lease_due_attempt(canvas_id, trigger_id, lease_owner=RUNTIME_ID, lease_ttl_seconds=30)
    updated = store.mark_attempt_failed(
        canvas_id, trigger_id, lease_owner=RUNTIME_ID, error="boom",
        base_seconds=1, max_seconds=60, max_attempts=1,
    )
    assert updated.status == AttemptStatus.QUARANTINED


def test_check_integrity_ok_on_healthy_store(tmp_path, capsys):
    store = StateStore(tmp_path / "state.db")
    try:
        code = admin.check_integrity(_ctx(store))
        assert code == 0
        assert "OK" in capsys.readouterr().out
        events = [e for e in store.list_audit_events() if e.event == "operator_integrity_check"]
        assert len(events) == 1
        assert events[0].payload["ok"] is True
    finally:
        store.close()


def test_check_integrity_fails_closed_on_tampered_audit_chain(tmp_path, capsys):
    store = StateStore(tmp_path / "state.db")
    try:
        store.append_audit_event("c", "attempt_leased", {"trigger_id": "t1"})
        # Tamper an existing event's payload without recomputing its hash --
        # simulates corruption/tampering, must be caught by verify_audit_chain.
        store.conn.execute(
            "UPDATE audit_events SET payload_json = ? WHERE sequence = 1",
            (json.dumps({"trigger_id": "tampered"}),),
        )
        store.conn.commit()

        code = admin.check_integrity(_ctx(store))
        assert code == 1
        out = capsys.readouterr().out
        assert "AUDIT CHAIN PROBLEM" in out
        events = [e for e in store.list_audit_events() if e.event == "operator_integrity_check"]
        assert events[-1].payload["ok"] is False
    finally:
        store.close()


def test_list_quarantined_reports_none_when_empty(tmp_path, capsys):
    store = StateStore(tmp_path / "state.db")
    try:
        code = admin.list_quarantined(_ctx(store), None)
        assert code == 0
        assert "No quarantined attempts." in capsys.readouterr().out
    finally:
        store.close()


def test_list_quarantined_filters_by_canvas(tmp_path, capsys):
    store = StateStore(tmp_path / "state.db")
    try:
        _quarantine(store, "canvas-a", "loop:c1")
        _quarantine(store, "canvas-b", "loop:c2")

        code = admin.list_quarantined(_ctx(store), "canvas-a")
        assert code == 0
        out = capsys.readouterr().out
        assert "canvas-a" in out
        assert "loop:c1" in out
        assert "canvas-b" not in out
    finally:
        store.close()


def test_reset_attempt_succeeds_on_quarantined_attempt(tmp_path, capsys):
    store = StateStore(tmp_path / "state.db")
    try:
        _quarantine(store, "canvas-a", "loop:c1")

        code = admin.reset_attempt(_ctx(store), "canvas-a", "loop:c1")
        assert code == 0
        assert "Reset canvas-a/loop:c1 to pending." in capsys.readouterr().out
        attempt = store.get_attempt("canvas-a", "loop:c1")
        assert attempt is not None
        assert attempt.status == AttemptStatus.PENDING
        events = [e for e in store.list_audit_events("canvas-a") if e.event == "operator_reset"]
        assert len(events) == 1
    finally:
        store.close()


def test_reset_attempt_fails_when_not_quarantined(tmp_path, capsys):
    store = StateStore(tmp_path / "state.db")
    try:
        store.ensure_attempt("canvas-a", "loop:c1")  # left pending, not quarantined

        code = admin.reset_attempt(_ctx(store), "canvas-a", "loop:c1")
        assert code == 1
        assert "ERROR" in capsys.readouterr().out
    finally:
        store.close()


def test_backup_writes_consistent_copy_and_creates_parent_dirs(tmp_path, capsys):
    store = StateStore(tmp_path / "state.db")
    try:
        store.append_audit_event("c", "attempt_leased", {"trigger_id": "t1"})
        dest = tmp_path / "backups" / "state.db.bak"

        code = admin.backup(_ctx(store), str(dest))
        assert code == 0
        assert dest.exists()
        assert f"Backup written to {dest}" in capsys.readouterr().out
        events = [e for e in store.list_audit_events() if e.event == "operator_backup"]
        assert len(events) == 1
        assert events[0].payload["destination"] == str(dest)

        restored = StateStore(dest)
        try:
            restored.verify_audit_chain()  # backup is a valid, integrity-clean ledger
            assert restored.integrity_check() == []
        finally:
            restored.close()
    finally:
        store.close()
