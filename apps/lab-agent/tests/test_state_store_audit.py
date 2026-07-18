"""Append-only, sequenced, hash-chained audit log for the durable SQLite
ledger (``lab_agent.state_store``).

Shared ``_Clock``/``_Rng``/``store`` fixtures live in ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest

from lab_agent.state_store import AuditChainTamperError, AuditPayloadTooLargeError, StateStore


def test_append_audit_event_chains_from_genesis(store: StateStore) -> None:
    event = store.append_audit_event("c1", "loop_started", {"trigger_id": "t1"})
    assert event.sequence == 1
    assert event.previous_hash == "0" * 64
    assert len(event.event_hash) == 64


def test_append_audit_event_links_to_previous_hash(store: StateStore) -> None:
    first = store.append_audit_event("c1", "loop_started", {"trigger_id": "t1"})
    second = store.append_audit_event("c1", "loop_completed", {"trigger_id": "t1"})
    assert second.previous_hash == first.event_hash
    assert second.sequence == 2


def test_append_audit_event_rejects_oversized_payload(store: StateStore) -> None:
    huge_payload = {"body": "x" * 5000}
    with pytest.raises(AuditPayloadTooLargeError):
        store.append_audit_event("c1", "loop_started", huge_payload)


def test_verify_audit_chain_passes_on_untampered_log(store: StateStore) -> None:
    for i in range(5):
        store.append_audit_event("c1", f"event_{i}", {"i": i})
    store.verify_audit_chain()  # must not raise


def test_verify_audit_chain_detects_tampered_payload(store: StateStore) -> None:
    store.append_audit_event("c1", "loop_started", {"trigger_id": "t1"})
    store.append_audit_event("c1", "loop_completed", {"trigger_id": "t1"})
    # Directly mutate a stored payload -- bypassing the API, simulating tampering.
    store.conn.execute(
        "UPDATE audit_events SET payload_json='{\"trigger_id\":\"forged\"}' WHERE sequence=1"
    )
    with pytest.raises(AuditChainTamperError):
        store.verify_audit_chain()


def test_verify_audit_chain_detects_broken_previous_hash_link(store: StateStore) -> None:
    store.append_audit_event("c1", "loop_started", {"trigger_id": "t1"})
    store.append_audit_event("c1", "loop_completed", {"trigger_id": "t1"})
    store.conn.execute("UPDATE audit_events SET previous_hash=? WHERE sequence=2", ("f" * 64,))
    with pytest.raises(AuditChainTamperError):
        store.verify_audit_chain()


def test_list_audit_events_scoped_to_canvas(store: StateStore) -> None:
    store.append_audit_event("c1", "loop_started", {"trigger_id": "t1"})
    store.append_audit_event("c2", "loop_started", {"trigger_id": "t2"})
    events = store.list_audit_events("c1")
    assert [e.canvas_id for e in events] == ["c1"]
