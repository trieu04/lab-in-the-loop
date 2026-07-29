"""Tenant-aware audit-chain migration, isolation, and verification tests."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from lab_agent import admin
from lab_agent.config import Settings
from lab_agent.runtime import RuntimeContext, build_runtime_context, close_runtime_context
from lab_agent.state_store import AuditChainTamperError, StateStore
from lab_agent.tenant import CanvasAccessDeniedError, TenantContext

_MIGRATIONS = Path(__file__).parents[1] / "lab_agent" / "migrations"


def _context(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id, ("shared",), "research.example")


def _legacy_hash(sequence: int, canvas_id: str, event: str, payload_json: str, previous: str) -> str:
    digest = hashlib.sha256()
    for part in (str(sequence), canvas_id, event, payload_json, previous):
        digest.update(part.encode("utf-8"))
        digest.update(b"|")
    return digest.hexdigest()


def _legacy_store(path: Path, migrations: Path) -> tuple[sqlite3.Row, sqlite3.Row]:
    for source in _MIGRATIONS.glob("*.sql"):
        if source.name != "015_tenant_audit_chain_v2.sql":
            shutil.copy(source, migrations / source.name)
    store = StateStore(path, migrations_dir=migrations)
    try:
        first_payload = '{"trigger_id":"legacy-1"}'
        first_hash = _legacy_hash(1, "shared", "loop_started", first_payload, "0" * 64)
        second_payload = '{"trigger_id":"legacy-1"}'
        second_hash = _legacy_hash(2, "shared", "loop_stopped", second_payload, first_hash)
        store.conn.executemany(
            "INSERT INTO audit_events "
            "(sequence, canvas_id, round, event, payload_json, previous_hash, event_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (1, "shared", None, "loop_started", first_payload, "0" * 64, first_hash, 10.0),
                (2, "shared", 1, "loop_stopped", second_payload, first_hash, second_hash, 11.0),
            ],
        )
        return tuple(store.conn.execute("SELECT * FROM audit_events ORDER BY id"))  # type: ignore[return-value]
    finally:
        store.close()


def test_migration_preserves_legacy_hashes_and_anchors_v2_chains(tmp_path) -> None:
    path = tmp_path / "state.db"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    legacy = _legacy_store(path, migrations)

    default = StateStore(path, tenant_context=_context("default"))
    other = StateStore(path, tenant_context=_context("tenant-b"))
    try:
        preserved = default.conn.execute("SELECT * FROM audit_events WHERE hash_version=1 ORDER BY id").fetchall()
        preserved_values = [
            tuple(row[name] for name in ("id", "sequence", "canvas_id", "round", "event", "payload_json", "previous_hash", "event_hash", "created_at"))
            for row in preserved
        ]
        legacy_values = [
            tuple(row[name] for name in ("id", "sequence", "canvas_id", "round", "event", "payload_json", "previous_hash", "event_hash", "created_at"))
            for row in legacy
        ]
        assert preserved_values == legacy_values
        assert {(row["tenant_id"], row["hash_version"]) for row in preserved} == {("default", 1)}

        default_v2 = default.append_audit_event("shared", "v2_default", {"ok": True})
        other_v2 = other.append_audit_event("shared", "v2_other", {"ok": True})

        assert (default_v2.sequence, default_v2.previous_hash, default_v2.hash_version) == (3, legacy[-1]["event_hash"], 2)
        assert (other_v2.sequence, other_v2.previous_hash, other_v2.hash_version) == (1, legacy[-1]["event_hash"], 2)
        default.verify_audit_chain()
        other.verify_audit_chain()
    finally:
        default.close()
        other.close()


def test_stale_audit_insert_without_tenant_or_version_fails_closed(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute(
                "INSERT INTO audit_events "
                "(sequence, canvas_id, event, payload_json, previous_hash, event_hash, created_at) "
                "VALUES (1, 'shared', 'stale', '{}', '0', '0', 1)"
            )
    finally:
        store.close()


def test_tenant_bound_audit_isolates_same_canvas_and_trigger(tmp_path) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, tenant_context=_context("tenant-a"))
    second = StateStore(path, tenant_context=_context("tenant-b"))
    try:
        first.append_audit_event("shared", "loop_stopped", {"trigger_id": "same"})
        second.append_audit_event("shared", "loop_stopped", {"trigger_id": "same"})

        first_terminal = first.find_terminal_event("shared", "same")
        second_terminal = second.find_terminal_event("shared", "same")
        assert first_terminal is not None and first_terminal.tenant_id == "tenant-a"
        assert second_terminal is not None and second_terminal.tenant_id == "tenant-b"
        assert [event.tenant_id for event in first.list_audit_events("shared")] == ["tenant-a"]
        with pytest.raises(CanvasAccessDeniedError):
            first.list_audit_events("other")
    finally:
        first.close()
        second.close()


def test_tenant_verification_is_local_but_operator_verification_is_global(tmp_path) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, tenant_context=_context("tenant-a"))
    second = StateStore(path, tenant_context=_context("tenant-b"))
    try:
        first.append_audit_event("shared", "event", {"tenant": "a"})
        second.append_audit_event("shared", "event", {"tenant": "b"})
        second.conn.execute(
            "UPDATE audit_events SET payload_json='{" + '"tenant":"forged"' + "}' "
            "WHERE tenant_id='tenant-b'"
        )

        first.verify_audit_chain()
        with pytest.raises(AuditChainTamperError):
            second.verify_audit_chain()
        with pytest.raises(AuditChainTamperError):
            first.verify_all_audit_chains()
    finally:
        first.close()
        second.close()


def test_operator_integrity_verifies_all_tenant_chains(tmp_path) -> None:
    path = tmp_path / "state.db"
    first = StateStore(path, tenant_context=_context("tenant-a"))
    second = StateStore(path, tenant_context=_context("tenant-b"))
    try:
        first.append_audit_event("shared", "event", {"tenant": "a"})
        second.append_audit_event("shared", "event", {"tenant": "b"})
        second.conn.execute("UPDATE audit_events SET payload_json='{\"tenant\":\"forged\"}' WHERE tenant_id='tenant-b'")

        context = RuntimeContext(first, "runtime", Settings())
        assert admin.check_integrity(context) == 1
        # Global integrity must not be attributed to an arbitrary tenant canvas.
        assert first.list_audit_events("shared")[-1].event == "event"
    finally:
        first.close()
        second.close()


def test_runtime_binds_configured_tenant_before_audit_verification(tmp_path) -> None:
    settings = Settings(
        state_db_path=str(tmp_path / "state.db"), tenant_id="tenant-a",
        credential_domain="research.example", allowed_canvas_ids=["shared"],
    )
    context = build_runtime_context(settings)
    try:
        assert context.store.tenant_id == "tenant-a"
        context.store.append_audit_event("shared", "event", {"ok": True})
        with pytest.raises(CanvasAccessDeniedError):
            context.store.append_audit_event("other", "event", {"ok": False})
    finally:
        close_runtime_context(context)
