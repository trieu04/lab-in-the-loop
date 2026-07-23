"""``audit_events``: an append-only, sequenced, hash-chained safety log.

Each event's hash covers its sequence, canvas id, event name, canonical
payload JSON, and the previous event's hash -- a single global chain across
canvases, so tampering or dropping any past event is detectable by
recomputing the chain from the genesis hash.

Payloads must be small (ids/hashes/reasons only -- never full document text
or credentials, per docs/code-standards.md's audit policy); this module
enforces that with a byte-size cap rather than trusting every caller.

``MAX_PAYLOAD_BYTES`` and :func:`payload_size_bytes` are the one authoritative
size contract for a durable audit payload; callers that must fit several
pieces (e.g. evidence rows plus a reason) into one payload should size
against these rather than guessing or duplicating the limit (see
:mod:`lab_agent.grounding`).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from lab_agent.state.models import AuditEvent, Clock

_GENESIS_HASH = "0" * 64
MAX_PAYLOAD_BYTES = 4096


class AuditPayloadTooLargeError(RuntimeError):
    """Raised when a payload is too large to be an id/hash/reason summary --
    the audit log must never carry full document bodies or credentials."""


class AuditChainTamperError(RuntimeError):
    """Raised by :func:`verify_chain` on the first broken hash link
    (tampering or corruption)."""


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def payload_size_bytes(payload: dict[str, Any]) -> int:
    """Byte size of ``payload`` under the same canonical encoding
    :func:`append_event` checks against :data:`MAX_PAYLOAD_BYTES` -- callers
    can use this to size a payload *before* appending it, instead of
    discovering it is too large via :class:`AuditPayloadTooLargeError`."""
    return len(_canonical_json(payload).encode("utf-8"))


def _hash_event(sequence: int, canvas_id: str, event: str, payload_json: str, previous_hash: str) -> str:
    digest = hashlib.sha256()
    for part in (str(sequence), canvas_id, event, payload_json, previous_hash):
        digest.update(part.encode("utf-8"))
        digest.update(b"|")
    return digest.hexdigest()


def _row_to_event(row: sqlite3.Row) -> AuditEvent:
    return AuditEvent(
        id=row["id"],
        sequence=row["sequence"],
        canvas_id=row["canvas_id"],
        round=row["round"],
        event=row["event"],
        payload=json.loads(row["payload_json"]),
        previous_hash=row["previous_hash"],
        event_hash=row["event_hash"],
        created_at=row["created_at"],
    )


def append_event(
    conn: sqlite3.Connection,
    *,
    clock: Clock,
    canvas_id: str,
    event: str,
    payload: dict[str, Any],
    round: int | None = None,
) -> AuditEvent:
    """Append one hash-chained event; raises :class:`AuditPayloadTooLargeError`
    if ``payload`` serializes past the size cap."""
    payload_json = _canonical_json(payload)
    if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise AuditPayloadTooLargeError(
            f"audit payload for {event!r} exceeds {MAX_PAYLOAD_BYTES} bytes; "
            "store ids/hashes/reasons, not full bodies"
        )
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        prev = conn.execute(
            "SELECT sequence, event_hash FROM audit_events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = (prev["sequence"] + 1) if prev is not None else 1
        previous_hash = prev["event_hash"] if prev is not None else _GENESIS_HASH
        event_hash = _hash_event(sequence, canvas_id, event, payload_json, previous_hash)
        conn.execute(
            "INSERT INTO audit_events "
            "(sequence, canvas_id, round, event, payload_json, previous_hash, event_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (sequence, canvas_id, round, event, payload_json, previous_hash, event_hash, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    row = conn.execute("SELECT * FROM audit_events WHERE sequence=?", (sequence,)).fetchone()
    if row is None:  # pragma: no cover - just inserted above
        raise RuntimeError(f"audit event sequence={sequence} missing immediately after append")
    return _row_to_event(row)


def verify_chain(conn: sqlite3.Connection) -> None:
    """Walk every event by sequence and recompute its hash and link.

    Raises :class:`AuditChainTamperError` on the first mismatch (a changed
    payload/event, a dropped event, or a reordered/forged link).
    """
    expected_previous = _GENESIS_HASH
    for row in conn.execute("SELECT * FROM audit_events ORDER BY sequence ASC").fetchall():
        if row["previous_hash"] != expected_previous:
            raise AuditChainTamperError(f"sequence {row['sequence']} previous_hash does not match prior event")
        recomputed = _hash_event(row["sequence"], row["canvas_id"], row["event"], row["payload_json"], row["previous_hash"])
        if recomputed != row["event_hash"]:
            raise AuditChainTamperError(f"sequence {row['sequence']} event_hash does not match recomputed hash")
        expected_previous = row["event_hash"]


def list_events(conn: sqlite3.Connection, *, canvas_id: str | None = None) -> list[AuditEvent]:
    if canvas_id is None:
        rows = conn.execute("SELECT * FROM audit_events ORDER BY sequence ASC").fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM audit_events WHERE canvas_id=? ORDER BY sequence ASC", (canvas_id,)
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def find_terminal_event(
    conn: sqlite3.Connection, *, canvas_id: str, trigger_id: str
) -> AuditEvent | None:
    """Return the newest terminal audit for one trigger via the indexed hot path."""
    row = conn.execute(
        "SELECT * FROM audit_events "
        "WHERE canvas_id=? AND event='loop_stopped' "
        "AND json_extract(payload_json, '$.trigger_id')=? "
        "ORDER BY sequence DESC LIMIT 1",
        (canvas_id, trigger_id),
    ).fetchone()
    return _row_to_event(row) if row is not None else None


__all__ = [
    "MAX_PAYLOAD_BYTES",
    "AuditChainTamperError",
    "AuditPayloadTooLargeError",
    "append_event",
    "find_terminal_event",
    "list_events",
    "payload_size_bytes",
    "verify_chain",
]
