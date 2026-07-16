"""``ArtifactStore.get_authorized_artifact``: the server-facing lookup for the
``/artifacts/{opaque_id}?token=...`` capability URL, which carries no
``canvas_id``.

Shared ``_Clock``/``_Rng``/``store`` fixtures live in ``tests/conftest.py``.
Token issue/verify/revoke/rotate mechanics live in ``test_artifact_tokens.py``.
"""

from __future__ import annotations

import pytest

from lab_agent.artifact_store import ArtifactStore
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


def test_get_authorized_artifact_succeeds_with_valid_token(artifact_store: ArtifactStore, opaque_id: str) -> None:
    token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    # No canvas_id is supplied at all -- this API resolves it internally.
    doc = artifact_store.get_authorized_artifact(opaque_id, token=token)
    assert doc is not None
    assert doc.opaque_id == opaque_id
    assert doc.canvas_id == "c1"


def test_get_authorized_artifact_signature_takes_no_canvas_id(artifact_store: ArtifactStore, opaque_id: str) -> None:
    token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    # Calling with exactly (opaque_id, token) -- as the HTTP route would,
    # since the capability URL carries no canvas_id -- must be sufficient.
    assert artifact_store.get_authorized_artifact(opaque_id, token=token) is not None


def test_get_authorized_artifact_unknown_opaque_id_returns_none(artifact_store: ArtifactStore) -> None:
    assert artifact_store.get_authorized_artifact("does-not-exist", token="whatever") is None


def test_get_authorized_artifact_tampered_token_returns_none(artifact_store: ArtifactStore, opaque_id: str) -> None:
    token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    tampered = ("a" if token[0] != "a" else "b") + token[1:]
    assert artifact_store.get_authorized_artifact(opaque_id, token=tampered) is None


def test_get_authorized_artifact_revoked_token_returns_none(artifact_store: ArtifactStore, opaque_id: str) -> None:
    token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    artifact_store.revoke_token(opaque_id, canvas_id="c1")
    assert artifact_store.get_authorized_artifact(opaque_id, token=token) is None


def test_get_authorized_artifact_rotated_token_returns_none(artifact_store: ArtifactStore, opaque_id: str) -> None:
    old_token = artifact_store.issue_token(opaque_id, canvas_id="c1")
    new_token = artifact_store.rotate_token(opaque_id, canvas_id="c1")
    assert artifact_store.get_authorized_artifact(opaque_id, token=old_token) is None
    assert artifact_store.get_authorized_artifact(opaque_id, token=new_token) is not None


def test_get_authorized_artifact_no_token_ever_issued_returns_none(
    artifact_store: ArtifactStore, opaque_id: str
) -> None:
    assert artifact_store.get_authorized_artifact(opaque_id, token="whatever") is None
