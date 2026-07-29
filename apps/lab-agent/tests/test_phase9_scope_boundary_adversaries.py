"""Boundary adversaries for artifact serving, notification drains, and watches."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from lab_agent.artifact_server import create_artifact_app
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.notifications import NotificationEnvelope
from lab_agent.state_store import StateStore
from lab_agent.tenant import CanvasAccessDeniedError, TenantContext
from lab_agent.watch import process_canvases_once

TENANT = "tenant-a"
DOMAIN = "research.example"
A = "canvas-a"
B = "canvas-b"


def _context(*canvases: str) -> TenantContext:
    return TenantContext(TENANT, canvases, DOMAIN)


def test_artifact_service_rejects_mismatched_runtime_scope(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db", tenant_context=_context(A))
    try:
        artifacts = ArtifactStore(store.conn, tenant_context=_context(A))
        with pytest.raises(ValueError, match="scope"):
            create_artifact_app(artifacts, expected_scope=_context(B))
    finally:
        store.close()


def test_artifact_service_returns_uniform_404_for_same_tenant_disallowed_canvas(tmp_path) -> None:
    path = tmp_path / "state.db"
    writer = StateStore(path, tenant_context=_context(A, B))
    try:
        artifacts = ArtifactStore(writer.conn, tenant_context=_context(A, B))
        document = artifacts.create_artifact(
            canvas_id=B, artifact_type=ArtifactType.SETUP, state=DecisionState.DRAFT,
            payload={"title": "private"},
            provenance=ArtifactProvenance(provider="test", model_name="test", trigger_id="t"),
        )
        token = artifacts.issue_token(document.opaque_id, canvas_id=B)
    finally:
        writer.close()

    reader = StateStore(path, tenant_context=_context(A))
    try:
        app = create_artifact_app(
            ArtifactStore(reader.conn, tenant_context=_context(A)), expected_scope=_context(A)
        )
        response = TestClient(app).get(f"/artifacts/{document.opaque_id}", params={"token": token})
        assert (response.status_code, response.text) == (404, "Not found")
    finally:
        reader.close()


def test_bound_notification_due_selection_excludes_disallowed_canvas(tmp_path) -> None:
    path = tmp_path / "state.db"
    writer = StateStore(path, tenant_context=_context(A, B))
    try:
        writer.enqueue_notification(NotificationEnvelope(
            canvas_id=B, trigger_id="trigger", closure_id="close", round_index=1,
            reason="budget", tenant_id=TENANT,
        ))
    finally:
        writer.close()

    reader = StateStore(path, tenant_context=_context(A))
    try:
        reader.expire_stale_notifications()
        assert reader.list_due_notification_keys(limit=10) == []
    finally:
        reader.close()


@pytest.mark.asyncio
async def test_watch_rejects_context_that_does_not_match_bound_store(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db", tenant_context=_context(A))
    try:
        with pytest.raises(CanvasAccessDeniedError, match="context"):
            await process_canvases_once(
                object(), lambda _canvas: object(), Settings(), store, "runtime", [A],
                tenant_context=_context(B),
            )
    finally:
        store.close()
