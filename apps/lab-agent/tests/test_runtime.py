"""Tests for lab_agent.runtime: fail-closed CLI startup and clean shutdown.

Covers requirement #4's "runtime ids remain unique even with identical
config" and requirement #2's "verify SQLite integrity and audit chain before
any canvas work" -- both only observable by driving the real
``build_runtime_context``/``close_runtime_context`` pair against an on-disk
SQLite file, not the ``StateStore`` unit tests alone.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from lab_agent.config import Settings
from lab_agent.runtime import (
    RuntimeStartupError,
    build_runtime_context,
    close_runtime_context,
    release_lease_with_audit,
)
from lab_agent.state_store import StateStore


def _settings(tmp_path, name: str = "state.db") -> Settings:
    return Settings(state_db_path=str(tmp_path / name))  # type: ignore[call-arg]


def test_build_runtime_context_creates_parent_dir_and_opens_store(tmp_path):
    settings = _settings(tmp_path / "nested" / "dir")
    ctx = build_runtime_context(settings)
    try:
        assert (tmp_path / "nested" / "dir").is_dir()
        assert ctx.store.integrity_check() == []
        assert ctx.runtime_instance_id
    finally:
        close_runtime_context(ctx)


def test_build_runtime_context_assigns_unique_runtime_id_with_identical_config(tmp_path):
    settings = _settings(tmp_path)
    ctx1 = build_runtime_context(settings)
    close_runtime_context(ctx1)
    ctx2 = build_runtime_context(settings)
    try:
        assert ctx1.runtime_instance_id != ctx2.runtime_instance_id
    finally:
        close_runtime_context(ctx2)


def test_build_runtime_context_fails_closed_on_tampered_audit_chain(tmp_path):
    settings = _settings(tmp_path)
    store = StateStore(tmp_path / "state.db")
    store.append_audit_event("c", "attempt_leased", {"trigger_id": "t1"})
    store.conn.execute(
        "UPDATE audit_events SET payload_json = ? WHERE sequence = 1",
        (json.dumps({"trigger_id": "tampered"}),),
    )
    store.conn.commit()
    store.close()

    with pytest.raises(RuntimeStartupError, match="Durable ledger verification failed"):
        build_runtime_context(settings)


def test_build_runtime_context_fails_closed_on_corrupt_sqlite_file(tmp_path):
    db_path = tmp_path / "state.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_bytes(b"not a sqlite file at all")
    settings = _settings(tmp_path)

    with pytest.raises(RuntimeStartupError):
        build_runtime_context(settings)


def test_close_runtime_context_closes_the_connection(tmp_path):
    ctx = build_runtime_context(_settings(tmp_path))
    close_runtime_context(ctx)
    with pytest.raises(sqlite3.ProgrammingError):
        ctx.store.integrity_check()


def test_release_lease_with_audit_releases_held_lease_and_appends_event(tmp_path):
    store = StateStore(tmp_path / "state.db")
    try:
        store.acquire_canvas_lease("c", runtime_instance_id="rt1", ttl_seconds=60)

        released = release_lease_with_audit(store, "rt1", "c")

        assert released is True
        assert store.get_canvas_lease("c") is None
        events = [e for e in store.list_audit_events("c") if e.event == "canvas_lease_released"]
        assert len(events) == 1
        assert events[0].payload["runtime_instance_id"] == "rt1"
    finally:
        store.close()


def test_release_lease_with_audit_is_a_noop_when_not_held(tmp_path):
    store = StateStore(tmp_path / "state.db")
    try:
        store.acquire_canvas_lease("c", runtime_instance_id="owner", ttl_seconds=60)

        released = release_lease_with_audit(store, "someone-else", "c")

        assert released is False
        assert store.get_canvas_lease("c") is not None  # owner's lease untouched
        events = [e for e in store.list_audit_events("c") if e.event == "canvas_lease_released"]
        assert events == []
    finally:
        store.close()
