"""``ArtifactStore`` capability-token lifecycle: issue, verify, tamper,
revoke, rotate, and cross-canvas denial.

Shared ``_Clock``/``_Rng``/``store`` fixtures live in ``tests/conftest.py``.
"""

from __future__ import annotations

import sqlite3

import pytest

from lab_agent.artifact_store import ArtifactCanvasScopeError, ArtifactStore
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.state_store import StateStore

PROVENANCE = ArtifactProvenance(provider="claude", model_name="claude-x", trigger_id="t1")


@pytest.fixture
def artifact_store(store: StateStore, clock) -> ArtifactStore:
    return ArtifactStore(store.conn, clock=clock)


@pytest.fixture
def opaque_id(artifact_store: ArtifactStore) -> str:
    doc = artifact_store.create_artifact(
        canvas_id="c1",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload={"k": "v"},
        provenance=PROVENANCE,
    )
    return doc.opaque_id


# ── issue / verify ───────────────────────────────────────────────────────


def test_issue_then_verify_succeeds(artifact_store: ArtifactStore, opaque_id: str) -> None:
    token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    assert isinstance(token, str) and len(token) > 0
    assert artifact_store.verify_token(opaque_id, canvas_id="c1", token=token) is True


def test_issue_does_not_expose_hash_in_domain_output(artifact_store: ArtifactStore, opaque_id: str) -> None:
    token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    doc = artifact_store.get_artifact(opaque_id, canvas_id="c1")
    assert doc is not None
    # ArtifactDocument has no token/hash field at all -- tokens are a
    # separate concern from the domain document.
    assert "token" not in doc.model_dump()
    assert token not in str(doc.model_dump())


def test_verify_unknown_token_returns_false(artifact_store: ArtifactStore, opaque_id: str) -> None:
    artifact_store.issue_token(opaque_id, canvas_id="c1")
    assert artifact_store.verify_token(opaque_id, canvas_id="c1", token="totally-made-up") is False


def test_verify_tampered_token_returns_false(artifact_store: ArtifactStore, opaque_id: str) -> None:
    token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    tampered = ("a" if token[0] != "a" else "b") + token[1:]
    assert artifact_store.verify_token(opaque_id, canvas_id="c1", token=tampered) is False


def test_verify_unknown_opaque_id_returns_false(artifact_store: ArtifactStore) -> None:
    assert artifact_store.verify_token("does-not-exist", canvas_id="c1", token="whatever") is False


# ── revoke ───────────────────────────────────────────────────────────────


def test_revoke_invalidates_token(artifact_store: ArtifactStore, opaque_id: str) -> None:
    token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    artifact_store.revoke_token(opaque_id, canvas_id="c1")
    assert artifact_store.verify_token(opaque_id, canvas_id="c1", token=token) is False


def test_revoke_is_safe_to_call_when_no_active_token(artifact_store: ArtifactStore, opaque_id: str) -> None:
    # No token issued yet -- revoke touches zero rows, must not raise.
    artifact_store.revoke_token(opaque_id, canvas_id="c1")


# ── rotate ───────────────────────────────────────────────────────────────


def test_rotate_invalidates_old_token_and_activates_new_one(artifact_store: ArtifactStore, opaque_id: str) -> None:
    old_token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    new_token = artifact_store.rotate_token(opaque_id, canvas_id="c1")
    assert new_token != old_token
    assert artifact_store.verify_token(opaque_id, canvas_id="c1", token=old_token) is False
    assert artifact_store.verify_token(opaque_id, canvas_id="c1", token=new_token) is True


def test_rotate_without_prior_token_still_issues_a_working_token(
    artifact_store: ArtifactStore, opaque_id: str
) -> None:
    token = artifact_store.rotate_token(opaque_id, canvas_id="c1")
    assert artifact_store.verify_token(opaque_id, canvas_id="c1", token=token) is True


# ── cross-canvas ─────────────────────────────────────────────────────────


def test_issue_token_wrong_canvas_raises_scope_error(artifact_store: ArtifactStore, opaque_id: str) -> None:
    with pytest.raises(ArtifactCanvasScopeError):
        artifact_store.issue_token(opaque_id, canvas_id="c2")


def test_rotate_token_wrong_canvas_raises_scope_error(artifact_store: ArtifactStore, opaque_id: str) -> None:
    artifact_store.issue_token(opaque_id, canvas_id="c1")
    with pytest.raises(ArtifactCanvasScopeError):
        artifact_store.rotate_token(opaque_id, canvas_id="c2")


def test_revoke_token_wrong_canvas_raises_scope_error(artifact_store: ArtifactStore, opaque_id: str) -> None:
    artifact_store.issue_token(opaque_id, canvas_id="c1")
    with pytest.raises(ArtifactCanvasScopeError):
        artifact_store.revoke_token(opaque_id, canvas_id="c2")


def test_verify_token_wrong_canvas_returns_false_not_raise(artifact_store: ArtifactStore, opaque_id: str) -> None:
    token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    # verify_token is adversary-facing: a cross-canvas replay must be a plain
    # `False`, indistinguishable from an unknown/guessed/revoked token.
    assert artifact_store.verify_token(opaque_id, canvas_id="c2", token=token) is False


# ── one-active-token invariant ────────────────────────────────────────────


def test_second_issue_invalidates_the_first(artifact_store: ArtifactStore, opaque_id: str) -> None:
    first = artifact_store.issue_token(opaque_id, canvas_id="c1")
    second = artifact_store.issue_token(opaque_id, canvas_id="c1")
    assert second != first
    assert artifact_store.verify_token(opaque_id, canvas_id="c1", token=first) is False
    assert artifact_store.verify_token(opaque_id, canvas_id="c1", token=second) is True


def test_at_most_one_active_token_row_per_artifact(artifact_store: ArtifactStore, opaque_id: str) -> None:
    artifact_store.issue_token(opaque_id, canvas_id="c1")
    artifact_store.issue_token(opaque_id, canvas_id="c1")
    artifact_store.issue_token(opaque_id, canvas_id="c1")
    count = artifact_store.conn.execute(
        "SELECT COUNT(*) AS n FROM artifact_tokens WHERE opaque_id=? AND status='active'", (opaque_id,)
    ).fetchone()["n"]
    assert count == 1


def test_partial_unique_index_rejects_a_second_active_row_bypassing_the_store(
    artifact_store: ArtifactStore, opaque_id: str
) -> None:
    # DB-level backstop: even a direct INSERT that skips issue_token's
    # rotate-first step must be rejected by the partial unique index.
    artifact_store.issue_token(opaque_id, canvas_id="c1")
    with pytest.raises(sqlite3.IntegrityError):
        artifact_store.conn.execute(
            "INSERT INTO artifact_tokens (token_hash, opaque_id, canvas_id, status, created_at, revoked_at) "
            "VALUES ('deadbeef', ?, 'c1', 'active', 0.0, NULL)",
            (opaque_id,),
        )
