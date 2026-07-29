"""Tenant-qualified, fenced SQLite lifecycle for metadata-only notifications."""
# ruff: noqa: E701, E702
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from lab_agent.notifications import message_id_for_logical_key
from lab_agent.state.models import Clock, RandomSource


class NotificationStatus(StrEnum):
    PENDING = "pending"; SENDING = "sending"; SENT = "sent"; QUARANTINED = "quarantined"; AMBIGUOUS = "ambiguous"


class FailureCategory(StrEnum):
    SMTP_TRANSIENT = "smtp_transient"; SMTP_REJECTED = "smtp_rejected"; SMTP_AMBIGUOUS = "smtp_ambiguous"; IDEMPOTENCY_CONFLICT = "idempotency_conflict"


class NotificationOutboxConflictError(RuntimeError): pass
class StaleNotificationLeaseError(RuntimeError): pass
class NotificationNotQuarantinedError(RuntimeError): pass


@dataclass(frozen=True)
class NotificationOutboxRecord:
    logical_key: str; canvas_id: str; closure_metadata: dict[str, object]; status: NotificationStatus
    attempt_count: int; next_retry_at: float | None; lease_owner: str | None; lease_expires_at: float | None
    lease_generation: int; reconciliation_deadline: float | None; message_id: str; failure_category: FailureCategory | None
    created_at: float; updated_at: float; sent_at: float | None; tenant_id: str = "default"


def _tenant(value: str) -> str:
    return _text(value, "tenant_id")


def message_id_for(logical_key: str) -> str:
    return message_id_for_logical_key(_text(logical_key, "logical_key"))


def enqueue(
    conn: sqlite3.Connection, *, clock: Clock, logical_key: str, canvas_id: str,
    closure_metadata: Mapping[str, object], message_id: str, tenant_id: str = "default",
) -> NotificationOutboxRecord:
    tenant, key, canvas, metadata, now = _tenant(tenant_id), _text(logical_key, "logical_key"), _text(canvas_id, "canvas_id"), _metadata(closure_metadata), clock()
    if message_id != message_id_for_logical_key(key):
        raise ValueError("message_id does not match logical_key")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM notification_outbox WHERE tenant_id=? AND logical_key=?", (tenant, key)).fetchone()
        if row is not None:
            if row["canvas_id"] != canvas or row["closure_metadata_json"] != metadata or row["message_id"] != message_id:
                raise NotificationOutboxConflictError(f"logical key {key!r} has incompatible closure metadata")
            conn.execute("COMMIT")
            return _row(row)
        conn.execute(
            "INSERT INTO notification_outbox (tenant_id,logical_key,canvas_id,closure_metadata_json,status,attempt_count,lease_generation,message_id,created_at,updated_at) "
            "VALUES (?,?,?,?,'pending',0,0,?,?,?)", (tenant, key, canvas, metadata, message_id, now, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return _required(conn, key, tenant)


def get(conn: sqlite3.Connection, *, logical_key: str, tenant_id: str = "default") -> NotificationOutboxRecord | None:
    row = conn.execute("SELECT * FROM notification_outbox WHERE tenant_id=? AND logical_key=?", (_tenant(tenant_id), _text(logical_key, "logical_key"))).fetchone()
    return _row(row) if row is not None else None


def lease_due(conn: sqlite3.Connection, *, clock: Clock, logical_key: str, lease_owner: str, lease_ttl_seconds: float, reconciliation_window_seconds: float, tenant_id: str = "default") -> NotificationOutboxRecord | None:
    return _lease(conn, clock=clock, logical_key=logical_key, lease_owner=lease_owner, ttl=lease_ttl_seconds, window=reconciliation_window_seconds, ambiguous=False, tenant_id=tenant_id)


def lease_ambiguous_reconciliation(conn: sqlite3.Connection, *, clock: Clock, logical_key: str, lease_owner: str, lease_ttl_seconds: float, tenant_id: str = "default") -> NotificationOutboxRecord | None:
    return _lease(conn, clock=clock, logical_key=logical_key, lease_owner=lease_owner, ttl=lease_ttl_seconds, window=0, ambiguous=True, tenant_id=tenant_id)


def mark_sent(conn: sqlite3.Connection, *, clock: Clock, logical_key: str, lease_owner: str, lease_generation: int, tenant_id: str = "default") -> NotificationOutboxRecord:
    return _settle(conn, clock=clock, logical_key=logical_key, lease_owner=lease_owner, lease_generation=lease_generation, tenant_id=tenant_id, sql="status='sent',sent_at=?,next_retry_at=NULL,lease_owner=NULL,lease_expires_at=NULL,reconciliation_deadline=NULL,failure_category=NULL", values=lambda now: (now,))


def mark_reconciled_sent(
    conn: sqlite3.Connection, *, clock: Clock, logical_key: str, lease_owner: str,
    lease_generation: int, tenant_id: str = "default",
) -> NotificationOutboxRecord:
    return mark_sent(
        conn, clock=clock, logical_key=logical_key, lease_owner=lease_owner,
        lease_generation=lease_generation, tenant_id=tenant_id,
    )


def mark_transient_retry(conn: sqlite3.Connection, *, clock: Clock, rng: RandomSource, logical_key: str, lease_owner: str, lease_generation: int, base_seconds: float, max_seconds: float, max_attempts: int, tenant_id: str = "default") -> NotificationOutboxRecord:
    if base_seconds <= 0 or max_seconds <= 0 or max_attempts < 1: raise ValueError("retry values must be positive")
    row = _fenced_row(conn, clock=clock, logical_key=logical_key, lease_owner=lease_owner, generation=lease_generation, tenant_id=tenant_id)
    delay = rng() * min(max_seconds, base_seconds * 2 ** max(row["attempt_count"] - 1, 0))
    sql = "status='pending',next_retry_at=?,lease_owner=NULL,lease_expires_at=NULL,reconciliation_deadline=NULL,failure_category='smtp_transient'"
    if row["attempt_count"] >= max_attempts: sql, delay = "status='quarantined',next_retry_at=NULL,lease_owner=NULL,lease_expires_at=NULL,failure_category='smtp_transient'", None
    return _settle(conn, clock=clock, logical_key=logical_key, lease_owner=lease_owner, lease_generation=lease_generation, tenant_id=tenant_id, sql=sql, values=lambda now: () if delay is None else (now + delay,))


def quarantine(conn: sqlite3.Connection, *, clock: Clock, logical_key: str, lease_owner: str, lease_generation: int, failure_category: FailureCategory, tenant_id: str = "default") -> NotificationOutboxRecord:
    return _settle(conn, clock=clock, logical_key=logical_key, lease_owner=lease_owner, lease_generation=lease_generation, tenant_id=tenant_id, sql="status='quarantined',next_retry_at=NULL,lease_owner=NULL,lease_expires_at=NULL,failure_category=?", values=lambda _: (FailureCategory(failure_category).value,))


def mark_ambiguous(conn: sqlite3.Connection, *, clock: Clock, logical_key: str, lease_owner: str, lease_generation: int, reconciliation_window_seconds: float, tenant_id: str = "default") -> NotificationOutboxRecord:
    if reconciliation_window_seconds <= 0: raise ValueError("reconciliation_window_seconds must be positive")
    return _settle(conn, clock=clock, logical_key=logical_key, lease_owner=lease_owner, lease_generation=lease_generation, tenant_id=tenant_id, sql="status='ambiguous',next_retry_at=NULL,lease_owner=NULL,lease_expires_at=NULL,reconciliation_deadline=?,failure_category='smtp_ambiguous'", values=lambda now: (now + reconciliation_window_seconds,))


def reset_quarantined(conn: sqlite3.Connection, *, clock: Clock, logical_key: str, tenant_id: str = "default") -> NotificationOutboxRecord:
    tenant, key, now = _tenant(tenant_id), _text(logical_key, "logical_key"), clock()
    record = get(conn, tenant_id=tenant, logical_key=key)
    if record is None:
        raise NotificationNotQuarantinedError(f"notification {key!r} is not quarantined")
    changed = conn.execute("UPDATE notification_outbox SET status='pending',attempt_count=0,next_retry_at=NULL,lease_owner=NULL,lease_expires_at=NULL,reconciliation_deadline=NULL,failure_category=NULL,updated_at=? WHERE tenant_id=? AND canvas_id=? AND logical_key=? AND status='quarantined'", (now, tenant, record.canvas_id, key)).rowcount
    if changed != 1: raise NotificationNotQuarantinedError(f"notification {key!r} is not quarantined")
    return _required(conn, key, tenant)


def list_records(
    conn: sqlite3.Connection, *, canvas_id: str | None = None,
    allowed_canvas_ids: Sequence[str] | None = None,
    status: NotificationStatus | None = None, tenant_id: str = "default",
) -> list[NotificationOutboxRecord]:
    if canvas_id is not None and allowed_canvas_ids is not None:
        raise ValueError("use canvas_id or allowed_canvas_ids, not both")
    where, values = ["tenant_id=?"], [_tenant(tenant_id)]
    if canvas_id is not None:
        where.append("canvas_id=?")
        values.append(_text(canvas_id, "canvas_id"))
    elif allowed_canvas_ids is not None:
        canvases = tuple(_text(item, "canvas_id") for item in allowed_canvas_ids)
        if not canvases:
            return []
        where.append(f"canvas_id IN ({','.join('?' for _ in canvases)})")
        values.extend(canvases)
    if status is not None:
        where.append("status=?")
        values.append(NotificationStatus(status).value)
    query = "SELECT * FROM notification_outbox WHERE " + " AND ".join(where) + " ORDER BY created_at,logical_key"
    return [_row(row) for row in conn.execute(query, values).fetchall()]


def _lease(conn: sqlite3.Connection, *, clock: Clock, logical_key: str, lease_owner: str, ttl: float, window: float, ambiguous: bool, tenant_id: str) -> NotificationOutboxRecord | None:
    tenant, key, owner = _tenant(tenant_id), _text(logical_key, "logical_key"), _text(lease_owner, "lease_owner")
    if ttl <= 0 or window < 0: raise ValueError("lease values must be positive")
    now = clock(); conn.execute("BEGIN IMMEDIATE")
    try:
        _expire(conn, now, tenant)
        clause = "status='ambiguous' AND reconciliation_deadline>?" if ambiguous else "status='pending' AND (next_retry_at IS NULL OR next_retry_at<=?)"
        row = conn.execute(f"SELECT * FROM notification_outbox WHERE tenant_id=? AND logical_key=? AND {clause} AND (lease_expires_at IS NULL OR lease_expires_at<=?)", (tenant, key, now, now)).fetchone()
        if row is None: conn.execute("COMMIT"); return None
        conn.execute("UPDATE notification_outbox SET status=?,attempt_count=attempt_count+?,lease_owner=?,lease_expires_at=?,lease_generation=lease_generation+1,next_retry_at=NULL,reconciliation_deadline=?,updated_at=? WHERE tenant_id=? AND canvas_id=? AND logical_key=?", ("ambiguous" if ambiguous else "sending", int(not ambiguous), owner, now + ttl, row["reconciliation_deadline"] if ambiguous else now + ttl + window, now, tenant, row["canvas_id"], key))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK"); raise
    return _required(conn, key, tenant)


def _expire(conn: sqlite3.Connection, now: float, tenant_id: str) -> None:
    conn.execute("UPDATE notification_outbox SET status='ambiguous',lease_owner=NULL,lease_expires_at=NULL,failure_category='smtp_ambiguous',updated_at=? WHERE tenant_id=? AND status='sending' AND lease_expires_at<=?", (now, tenant_id, now))
    conn.execute("UPDATE notification_outbox SET status='quarantined',lease_owner=NULL,lease_expires_at=NULL,failure_category='smtp_ambiguous',updated_at=? WHERE tenant_id=? AND status='ambiguous' AND reconciliation_deadline<=? AND (lease_expires_at IS NULL OR lease_expires_at<=?)", (now, tenant_id, now, now))


def _settle(conn: sqlite3.Connection, *, clock: Clock, logical_key: str, lease_owner: str, lease_generation: int, tenant_id: str, sql: str, values: Callable[[float], tuple[object, ...]]) -> NotificationOutboxRecord:
    tenant, key, owner, now = _tenant(tenant_id), _text(logical_key, "logical_key"), _text(lease_owner, "lease_owner"), clock()
    record = get(conn, tenant_id=tenant, logical_key=key)
    if record is None:
        raise StaleNotificationLeaseError(f"notification {key!r} is not held by {owner!r}")
    changed = conn.execute(f"UPDATE notification_outbox SET {sql},updated_at=? WHERE tenant_id=? AND canvas_id=? AND logical_key=? AND lease_owner=? AND lease_generation=? AND lease_expires_at>? AND status IN ('sending','ambiguous')", (*values(now), now, tenant, record.canvas_id, key, owner, lease_generation, now)).rowcount
    if changed != 1: raise StaleNotificationLeaseError(f"notification {key!r} is not held by {owner!r}")
    return _required(conn, key, tenant)


def _fenced_row(conn: sqlite3.Connection, *, clock: Clock, logical_key: str, lease_owner: str, generation: int, tenant_id: str) -> sqlite3.Row:
    tenant, key, owner, now = _tenant(tenant_id), _text(logical_key, "logical_key"), _text(lease_owner, "lease_owner"), clock()
    row = conn.execute("SELECT * FROM notification_outbox WHERE tenant_id=? AND logical_key=? AND lease_owner=? AND lease_generation=? AND lease_expires_at>? AND status='sending'", (tenant, key, owner, generation, now)).fetchone()
    if row is None: raise StaleNotificationLeaseError(f"notification {key!r} is not held by {owner!r}")
    return row


def _required(conn: sqlite3.Connection, key: str, tenant_id: str) -> NotificationOutboxRecord:
    entry = get(conn, logical_key=key, tenant_id=tenant_id)
    if entry is None: raise RuntimeError(f"notification {key!r} missing immediately after write")
    return entry


def _row(row: sqlite3.Row) -> NotificationOutboxRecord:
    metadata = json.loads(row["closure_metadata_json"])
    if not isinstance(metadata, dict): raise RuntimeError("notification closure metadata is not an object")
    return NotificationOutboxRecord(row["logical_key"], row["canvas_id"], metadata, NotificationStatus(row["status"]), row["attempt_count"], row["next_retry_at"], row["lease_owner"], row["lease_expires_at"], row["lease_generation"], row["reconciliation_deadline"], row["message_id"], FailureCategory(row["failure_category"]) if row["failure_category"] else None, row["created_at"], row["updated_at"], row["sent_at"], row["tenant_id"])


def _text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip(): raise ValueError(f"{name} must be non-empty")
    return value


def _metadata(value: Mapping[str, object]) -> str:
    if not isinstance(value, Mapping) or not value or any(not isinstance(key, str) for key in value): raise ValueError("closure_metadata must be a non-empty string-keyed mapping")
    try: return json.dumps(dict(value), allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError) as exc: raise ValueError("closure_metadata must be JSON-serializable") from exc
