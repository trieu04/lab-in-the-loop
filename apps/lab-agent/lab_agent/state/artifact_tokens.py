"""``artifact_tokens``: hashed bearer-capability tokens gating Browser reads.

Browser widgets cannot send custom auth headers (phase-03 plan, "Security
Considerations"), so the capability URL's query-string token is the only
credential a Canvus client can present. Only the token's SHA-256 hash is ever
persisted; the raw token exists in process memory only long enough to be
returned once from :func:`issue_token`/:func:`rotate_token`.

:func:`verify_token` is the adversary-facing path -- it must return a plain
``bool`` and behave identically (no distinguishing error) for an unknown
token, a wrong-canvas token, a revoked/rotated token, or a guessed token, so a
capability URL leak, a cross-canvas replay, and a brute-force guess are all
indistinguishable failures to the caller.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3

from lab_agent.state.artifacts import require_owned_artifact
from lab_agent.state.models import Clock

_TOKEN_BYTES = 32

# Comparison target when no real token row exists, so an unknown opaque id,
# an artifact with no active token, and a wrong/guessed token all still pay
# for one hmac.compare_digest -- no early-return shortcuts an adversary could
# time against.
_DUMMY_HASH = hashlib.sha256(b"lab_agent.artifact_tokens.dummy").hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _replace_active_token(conn: sqlite3.Connection, *, clock: Clock, opaque_id: str, canvas_id: str) -> str:
    """Atomically supersede any existing active token and mint a fresh one.

    Shared by :func:`issue_token` and :func:`rotate_token`: the
    ``artifact_tokens`` partial unique index (``WHERE status='active'``)
    allows at most one active row per artifact, so a second issue must
    rotate the first out rather than race it to insert a duplicate.
    """
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        require_owned_artifact(conn, opaque_id=opaque_id, canvas_id=canvas_id)
        conn.execute(
            "UPDATE artifact_tokens SET status='rotated', revoked_at=? WHERE opaque_id=? AND status='active'",
            (now, opaque_id),
        )
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        token_hash = _hash_token(token)
        conn.execute(
            "INSERT INTO artifact_tokens (token_hash, opaque_id, canvas_id, status, created_at, revoked_at) "
            "VALUES (?, ?, ?, 'active', ?, NULL)",
            (token_hash, opaque_id, canvas_id, now),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return token


def issue_token(conn: sqlite3.Connection, *, clock: Clock, opaque_id: str, canvas_id: str) -> str:
    """Mint and persist a new active token; returns the raw token exactly
    once. Safe to call when a token is already active -- see
    :func:`_replace_active_token`.

    Raises :class:`~lab_agent.state.artifacts.ArtifactNotFoundError`/
    :class:`~lab_agent.state.artifacts.ArtifactCanvasScopeError` if
    ``opaque_id``/``canvas_id`` do not match an existing artifact -- an
    internal (orchestrator-only) precondition, never adversary-reachable.
    """
    return _replace_active_token(conn, clock=clock, opaque_id=opaque_id, canvas_id=canvas_id)


def verify_token(conn: sqlite3.Connection, *, opaque_id: str, canvas_id: str, token: str) -> bool:
    """Constant-time check that ``token`` is the current active token for
    ``opaque_id`` within ``canvas_id``. Returns ``False`` uniformly for an
    unknown/wrong-canvas/revoked/rotated/guessed token -- never raises."""
    row = conn.execute(
        "SELECT token_hash FROM artifact_tokens WHERE opaque_id=? AND canvas_id=? AND status='active'",
        (opaque_id, canvas_id),
    ).fetchone()
    expected_hash = row["token_hash"] if row is not None else _DUMMY_HASH
    return hmac.compare_digest(_hash_token(token), expected_hash) and row is not None


def verify_token_any_canvas(conn: sqlite3.Connection, *, opaque_id: str, token: str) -> str | None:
    """Canvas-agnostic adversary-facing check for server routes that only
    carry ``opaque_id`` + ``token`` (the Browser capability URL has no
    ``canvas_id``). Returns the artifact's ``canvas_id`` if ``token`` is the
    current active token for ``opaque_id``, else ``None`` -- uniformly, for
    an unknown id, a revoked/rotated token, or a guessed token alike."""
    row = conn.execute(
        "SELECT canvas_id, token_hash FROM artifact_tokens WHERE opaque_id=? AND status='active'",
        (opaque_id,),
    ).fetchone()
    expected_hash = row["token_hash"] if row is not None else _DUMMY_HASH
    matches = hmac.compare_digest(_hash_token(token), expected_hash)
    return row["canvas_id"] if (row is not None and matches) else None


def rotate_token(conn: sqlite3.Connection, *, clock: Clock, opaque_id: str, canvas_id: str) -> str:
    """Atomically revoke the current active token and mint a replacement.

    Raises the same scope errors as :func:`issue_token`.
    """
    return _replace_active_token(conn, clock=clock, opaque_id=opaque_id, canvas_id=canvas_id)


def revoke_token(conn: sqlite3.Connection, *, clock: Clock, opaque_id: str, canvas_id: str) -> None:
    """Revoke the current active token(s) for ``opaque_id`` without issuing a
    replacement. Raises the same scope errors as :func:`issue_token`."""
    now = clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        require_owned_artifact(conn, opaque_id=opaque_id, canvas_id=canvas_id)
        conn.execute(
            "UPDATE artifact_tokens SET status='revoked', revoked_at=? WHERE opaque_id=? AND status='active'",
            (now, opaque_id),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


__all__ = [
    "issue_token",
    "revoke_token",
    "rotate_token",
    "verify_token",
    "verify_token_any_canvas",
]
