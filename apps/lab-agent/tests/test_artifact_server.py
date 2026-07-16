"""Authorized-path tests for the artifact ASGI service
(``lab_agent.artifact_server.create_artifact_app``): successful rendering,
same-origin static assets, health check, security headers/CSP, ETag/304
caching, and a large payload rendering in full.

Unauthorized/uniform-404 cases live in ``test_artifact_server_unauthorized.py``.
Lock-serialized concurrency lives in ``test_artifact_server_concurrency.py``.
Shared ``clock``/``rng``/``store`` fixtures live in ``tests/conftest.py``.
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


def _mint(artifact_store: ArtifactStore, *, payload: dict | None = None) -> tuple[str, str]:
    doc = artifact_store.create_artifact(
        canvas_id="c1",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload=payload if payload is not None else {"title": "Setup A", "rationale": "because"},
        provenance=PROVENANCE,
    )
    token = artifact_store.issue_token(doc.opaque_id, canvas_id="c1")
    return doc.opaque_id, token


def test_artifact_view_renders_html_for_valid_token(artifact_store: ArtifactStore, client: TestClient) -> None:
    opaque_id, token = _mint(artifact_store)

    response = client.get(f"/artifacts/{opaque_id}", params={"token": token})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Setup A" in response.text
    assert response.headers["ETag"]


def test_static_css_served_with_correct_content_type(client: TestClient) -> None:
    response = client.get("/assets/artifact-view.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert len(response.content) > 0


def test_static_js_served_with_correct_content_type(client: TestClient) -> None:
    response = client.get("/assets/artifact-tabs.js")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert len(response.content) > 0


def test_healthz_returns_ok_with_no_sensitive_data(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok"}
    assert "db" not in response.text.lower()
    assert "token" not in response.text.lower()


def test_security_headers_present_on_success_response(
    artifact_store: ArtifactStore, client: TestClient
) -> None:
    opaque_id, token = _mint(artifact_store)

    response = client.get(f"/artifacts/{opaque_id}", params={"token": token})

    assert response.headers["Cache-Control"] == "private, no-cache"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    csp = response.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "frame-ancestors" not in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp


def test_etag_matching_if_none_match_returns_304(artifact_store: ArtifactStore, client: TestClient) -> None:
    opaque_id, token = _mint(artifact_store)
    first = client.get(f"/artifacts/{opaque_id}", params={"token": token})
    etag = first.headers["ETag"]

    second = client.get(
        f"/artifacts/{opaque_id}", params={"token": token}, headers={"If-None-Match": etag}
    )

    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["ETag"] == etag


def test_etag_mismatched_if_none_match_returns_full_200(
    artifact_store: ArtifactStore, client: TestClient
) -> None:
    opaque_id, token = _mint(artifact_store)

    response = client.get(
        f"/artifacts/{opaque_id}", params={"token": token}, headers={"If-None-Match": '"stale"'}
    )

    assert response.status_code == 200
    assert len(response.content) > 0


def test_etag_changes_when_rendered_state_changes(
    artifact_store: ArtifactStore, client: TestClient
) -> None:
    opaque_id, token = _mint(artifact_store)
    first = client.get(f"/artifacts/{opaque_id}", params={"token": token})

    artifact_store.append_version(
        opaque_id,
        canvas_id="c1",
        state=DecisionState.NEEDS_REVIEW,
        payload={"title": "Setup A", "rationale": "because"},
        provenance=PROVENANCE,
    )
    second = client.get(
        f"/artifacts/{opaque_id}",
        params={"token": token},
        headers={"If-None-Match": first.headers["ETag"]},
    )

    assert second.status_code == 200
    assert second.headers["ETag"] != first.headers["ETag"]
    assert DecisionState.NEEDS_REVIEW.value in second.text


def test_large_payload_renders_in_full_without_truncation(
    artifact_store: ArtifactStore, client: TestClient
) -> None:
    large_summary = "x" * 200_000
    opaque_id, token = _mint(artifact_store, payload={"title": "Big", "summary": large_summary})

    response = client.get(f"/artifacts/{opaque_id}", params={"token": token})

    assert response.status_code == 200
    assert large_summary in response.text
