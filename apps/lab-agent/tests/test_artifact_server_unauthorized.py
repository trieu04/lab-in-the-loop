"""Uniform-404 tests for the artifact ASGI service: every unauthorized,
missing, tampered, unknown, or revoked capability case must be
indistinguishable to the caller -- same status, same body, same headers,
and no artifact/canvas/token metadata anywhere in the response.

Shared ``clock``/``rng``/``store`` fixtures live in ``tests/conftest.py``.
Authorized-path tests live in ``test_artifact_server.py``.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from lab_agent.artifact_server import create_artifact_app
from lab_agent.artifact_store import ArtifactStore
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.state_store import StateStore

PROVENANCE = ArtifactProvenance(provider="claude", model_name="claude-x", trigger_id="t1")


@pytest.fixture
def artifact_store(store: StateStore, clock) -> ArtifactStore:
    return ArtifactStore(store.conn, clock=clock)


@pytest.fixture
def client(artifact_store: ArtifactStore) -> TestClient:
    return TestClient(create_artifact_app(artifact_store))


@pytest.fixture
def minted(artifact_store: ArtifactStore) -> tuple[str, str]:
    doc = artifact_store.create_artifact(
        canvas_id="secret-canvas-42",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload={"title": "Setup A", "rationale": "because"},
        provenance=PROVENANCE,
    )
    token = artifact_store.issue_token(doc.opaque_id, canvas_id="secret-canvas-42")
    return doc.opaque_id, token


def _assert_uniform_404(response) -> None:
    assert response.status_code == 404
    assert response.text == "Not found"
    assert response.headers["Cache-Control"] == "private, no-cache"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_missing_token_returns_uniform_404(client: TestClient, minted: tuple[str, str]) -> None:
    opaque_id, _token = minted
    _assert_uniform_404(client.get(f"/artifacts/{opaque_id}"))


def test_unknown_opaque_id_returns_uniform_404(client: TestClient) -> None:
    _assert_uniform_404(client.get("/artifacts/does-not-exist", params={"token": "whatever"}))


def test_tampered_token_returns_uniform_404(client: TestClient, minted: tuple[str, str]) -> None:
    opaque_id, token = minted
    tampered = ("a" if token[0] != "a" else "b") + token[1:]
    _assert_uniform_404(client.get(f"/artifacts/{opaque_id}", params={"token": tampered}))


def test_revoked_token_returns_uniform_404(
    artifact_store: ArtifactStore, client: TestClient, minted: tuple[str, str]
) -> None:
    opaque_id, token = minted
    artifact_store.revoke_token(opaque_id, canvas_id="secret-canvas-42")
    _assert_uniform_404(client.get(f"/artifacts/{opaque_id}", params={"token": token}))


def test_rotated_out_token_returns_uniform_404(
    artifact_store: ArtifactStore, client: TestClient, minted: tuple[str, str]
) -> None:
    opaque_id, old_token = minted
    artifact_store.rotate_token(opaque_id, canvas_id="secret-canvas-42")
    _assert_uniform_404(client.get(f"/artifacts/{opaque_id}", params={"token": old_token}))


def test_every_failure_case_produces_byte_identical_responses(
    client: TestClient, minted: tuple[str, str]
) -> None:
    opaque_id, token = minted
    tampered = ("a" if token[0] != "a" else "b") + token[1:]
    responses = [
        client.get(f"/artifacts/{opaque_id}"),
        client.get(f"/artifacts/{opaque_id}", params={"token": tampered}),
        client.get("/artifacts/totally-unknown-id", params={"token": token}),
    ]
    bodies = {r.content for r in responses}
    statuses = {r.status_code for r in responses}
    assert bodies == {b"Not found"}
    assert statuses == {404}


def test_404_body_never_leaks_canvas_or_capability_metadata(
    client: TestClient, minted: tuple[str, str]
) -> None:
    opaque_id, token = minted
    tampered = ("a" if token[0] != "a" else "b") + token[1:]

    response = client.get(f"/artifacts/{opaque_id}", params={"token": tampered})

    assert "secret-canvas-42" not in response.text
    assert opaque_id not in response.text
    assert token not in response.text
    assert tampered not in response.text
