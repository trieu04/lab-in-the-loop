"""Tenant-qualified, versioned hash chains for immutable audit events."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from lab_agent.state.audit_hashes import GENESIS_HASH, hash_v1, hash_v2
from lab_agent.state.models import AuditEvent, Clock

MAX_PAYLOAD_BYTES = 4096
_DEFAULT_TENANT_ID = "default"


class AuditPayloadTooLargeError(RuntimeError):
    """Raised when a compact audit payload exceeds the fixed byte limit."""


class AuditChainTamperError(RuntimeError):
    """Raised when a versioned audit hash chain has been altered or broken."""


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def payload_size_bytes(payload: dict[str, Any]) -> int:
    """Return a payload's UTF-8 size under the durable canonical encoding."""
    return len(_canonical_json(payload).encode("utf-8"))


def _row_to_event(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        id=row["id"], sequence=row["sequence"], tenant_id=row["tenant_id"],
        hash_version=row["hash_version"], canvas_id=row["canvas_id"], round=row["round"],
        event=row["event"], payload=json.loads(row["payload_json"]),
        previous_hash=row["previous_hash"], event_hash=row["event_hash"],
        created_at=row["created_at"],
    )


def _verify_legacy_prefix(conn: sqlite3.Connection) -> tuple[int, str]:
    previous_hash = GENESIS_HASH
    sequence = 0
    rows = conn.execute("SELECT * FROM audit_events WHERE hash_version=1 ORDER BY sequence").fetchall()
    for row in rows:
        if row["tenant_id"] != _DEFAULT_TENANT_ID:
            raise AuditChainTamperError("v1 audit event belongs to a non-default tenant")
        if row["previous_hash"] != previous_hash:
            raise AuditChainTamperError(f"sequence {row['sequence']} previous_hash does not match prior event")
        recomputed = hash_v1(
            row["sequence"], row["canvas_id"], row["event"], row["payload_json"], row["previous_hash"]
        )
        if recomputed != row["event_hash"]:
            raise AuditChainTamperError(f"sequence {row['sequence']} event_hash does not match recomputed hash")
        sequence, previous_hash = row["sequence"], row["event_hash"]
    return sequence, previous_hash


def _verify_v2_chain(conn: sqlite3.Connection, tenant_id: str, legacy_tip: tuple[int, str]) -> None:
    legacy_sequence, previous_hash = legacy_tip
    expected_sequence = legacy_sequence + 1 if tenant_id == _DEFAULT_TENANT_ID else 1
    rows = conn.execute(
        "SELECT * FROM audit_events WHERE tenant_id=? AND hash_version=2 ORDER BY sequence", (tenant_id,)
    ).fetchall()
    for row in rows:
        if row["sequence"] != expected_sequence:
            raise AuditChainTamperError(f"tenant {tenant_id!r} sequence is not contiguous")
        if row["previous_hash"] != previous_hash:
            raise AuditChainTamperError(f"tenant {tenant_id!r} previous_hash does not match prior event")
        recomputed = hash_v2(
            row["sequence"], row["tenant_id"], row["canvas_id"], row["round"], row["event"],
            row["payload_json"], row["previous_hash"], row["created_at"],
        )
        if recomputed != row["event_hash"]:
            raise AuditChainTamperError(f"tenant {tenant_id!r} event_hash does not match recomputed hash")
        expected_sequence += 1
        previous_hash = row["event_hash"]


def append_event(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, canvas_id: str,
    event: str, payload: dict[str, Any], round: int | None = None,
) -> AuditEvent:
    """Append a v2 event to one tenant chain, verifying its legacy anchor first."""
    payload_json = _canonical_json(payload)
    if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise AuditPayloadTooLargeError(
            f"audit payload for {event!r} exceeds {MAX_PAYLOAD_BYTES} bytes; store ids/hashes/reasons"
        )
    conn.execute("BEGIN IMMEDIATE")
    try:
        legacy_sequence, legacy_hash = _verify_legacy_prefix(conn)
        previous = conn.execute(
            "SELECT sequence, event_hash FROM audit_events "
            "WHERE tenant_id=? AND hash_version=2 ORDER BY sequence DESC LIMIT 1", (tenant_id,)
        ).fetchone()
        sequence = previous["sequence"] + 1 if previous else (
            legacy_sequence + 1 if tenant_id == _DEFAULT_TENANT_ID else 1
        )
        previous_hash = previous["event_hash"] if previous else legacy_hash
        created_at = clock()
        event_hash = hash_v2(
            sequence, tenant_id, canvas_id, round, event, payload_json, previous_hash, created_at
        )
        cursor = conn.execute(
            "INSERT INTO audit_events "
            "(tenant_id, hash_version, sequence, canvas_id, round, event, payload_json, "
            "previous_hash, event_hash, created_at) VALUES (?, 2, ?, ?, ?, ?, ?, ?, ?, ?)",
            (tenant_id, sequence, canvas_id, round, event, payload_json, previous_hash, event_hash, created_at),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    row = conn.execute("SELECT * FROM audit_events WHERE id=?", (cursor.lastrowid,)).fetchone()
    if row is None:  # pragma: no cover - SQLite returned an inserted row id above.
        raise RuntimeError("inserted audit event is missing")
    return _row_to_event(row)


def verify_chain(conn: sqlite3.Connection, *, tenant_id: str) -> None:
    """Verify all legacy v1 events and only ``tenant_id``'s v2 suffix."""
    _verify_v2_chain(conn, tenant_id, _verify_legacy_prefix(conn))


def verify_all_chains(conn: sqlite3.Connection) -> None:
    """Operator-only verification of every tenant v2 chain plus the v1 prefix."""
    legacy_tip = _verify_legacy_prefix(conn)
    tenants = conn.execute("SELECT DISTINCT tenant_id FROM audit_events WHERE hash_version=2").fetchall()
    for row in tenants:
        _verify_v2_chain(conn, row["tenant_id"], legacy_tip)


def list_events(
    conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str | None = None
) -> list[AuditEvent]:
    query = "SELECT * FROM audit_events WHERE tenant_id=?"
    values: tuple[str, ...] = (tenant_id,)
    if canvas_id is not None:
        query += " AND canvas_id=?"
        values = (tenant_id, canvas_id)
    rows = conn.execute(f"{query} ORDER BY sequence", values).fetchall()
    return [_row_to_event(row) for row in rows]


def find_terminal_event(
    conn: sqlite3.Connection, *, tenant_id: str, canvas_id: str, trigger_id: str
) -> AuditEvent | None:
    """Return the newest terminal event for the tenant/canvas/trigger identity."""
    row = conn.execute(
        "SELECT * FROM audit_events WHERE tenant_id=? AND canvas_id=? "
        "AND event='loop_stopped' AND json_extract(payload_json, '$.trigger_id')=? "
        "ORDER BY sequence DESC LIMIT 1", (tenant_id, canvas_id, trigger_id),
    ).fetchone()
    return _row_to_event(row) if row is not None else None


__all__ = [
    "MAX_PAYLOAD_BYTES", "AuditChainTamperError", "AuditPayloadTooLargeError", "append_event",
    "find_terminal_event", "list_events", "payload_size_bytes", "verify_all_chains", "verify_chain",
]
