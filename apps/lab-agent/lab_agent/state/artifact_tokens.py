"""Tenant-bound hashed bearer capabilities for Browser artifact views."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3

from lab_agent.state.artifacts import require_owned_artifact
from lab_agent.state.models import Clock

_TOKEN_BYTES = 32
_DUMMY_HASH = hashlib.sha256(b"lab_agent.artifact_tokens.dummy").hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _replace_active_token(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, opaque_id: str, canvas_id: str
) -> str:
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        require_owned_artifact(conn, tenant_id=tenant_id, opaque_id=opaque_id, canvas_id=canvas_id)
        conn.execute(
            "UPDATE artifact_tokens SET status='rotated', revoked_at=? "
            "WHERE tenant_id=? AND opaque_id=? AND status='active'",
            (now, tenant_id, opaque_id),
        )
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        conn.execute(
            "INSERT INTO artifact_tokens (tenant_id, token_hash, opaque_id, canvas_id, status, created_at, revoked_at) "
            "VALUES (?, ?, ?, ?, 'active', ?, NULL)",
            (tenant_id, _hash_token(token), opaque_id, canvas_id, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return token


def issue_token(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, opaque_id: str, canvas_id: str
) -> str:
    return _replace_active_token(
        conn, clock=clock, tenant_id=tenant_id, opaque_id=opaque_id, canvas_id=canvas_id
    )


def verify_token(
    conn: sqlite3.Connection, *, tenant_id: str, opaque_id: str, canvas_id: str, token: str
) -> bool:
    row = conn.execute(
        "SELECT token_hash FROM artifact_tokens "
        "WHERE tenant_id=? AND opaque_id=? AND canvas_id=? AND status='active'",
        (tenant_id, opaque_id, canvas_id),
    ).fetchone()
    expected_hash = row["token_hash"] if row is not None else _DUMMY_HASH
    return hmac.compare_digest(_hash_token(token), expected_hash) and row is not None


def verify_token_any_canvas(
    conn: sqlite3.Connection, *, tenant_id: str, opaque_id: str, token: str
) -> tuple[str, str] | None:
    """Resolve only an active capability owned by ``tenant_id``.

    The public URL deliberately omits tenant and canvas. The artifact service is
    tenant-bound, so this query resolves both only after validating the token.
    """
    row = conn.execute(
        "SELECT tenant_id, canvas_id, token_hash FROM artifact_tokens "
        "WHERE tenant_id=? AND opaque_id=? AND status='active'",
        (tenant_id, opaque_id),
    ).fetchone()
    expected_hash = row["token_hash"] if row is not None else _DUMMY_HASH
    if row is None or not hmac.compare_digest(_hash_token(token), expected_hash):
        return None
    return row["tenant_id"], row["canvas_id"]


def rotate_token(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, opaque_id: str, canvas_id: str
) -> str:
    return _replace_active_token(
        conn, clock=clock, tenant_id=tenant_id, opaque_id=opaque_id, canvas_id=canvas_id
    )


def revoke_token(
    conn: sqlite3.Connection, *, clock: Clock, tenant_id: str, opaque_id: str, canvas_id: str
) -> None:
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        require_owned_artifact(conn, tenant_id=tenant_id, opaque_id=opaque_id, canvas_id=canvas_id)
        conn.execute(
            "UPDATE artifact_tokens SET status='revoked', revoked_at=? "
            "WHERE tenant_id=? AND opaque_id=? AND status='active'",
            (now, tenant_id, opaque_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


__all__ = ["issue_token", "revoke_token", "rotate_token", "verify_token", "verify_token_any_canvas"]
