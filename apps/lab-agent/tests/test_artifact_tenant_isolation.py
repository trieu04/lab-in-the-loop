"""Tenant isolation for the versioned artifact persistence graph."""

from __future__ import annotations

import pytest

from lab_agent import durable_browser
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.state_store import StateStore
from lab_agent.tenant import CanvasAccessDeniedError, TenantContext
from tests.fakes import FakeMCP

PROVENANCE = ArtifactProvenance(provider="claude", model_name="claude-x", trigger_id="t1")
CANVAS = "canvas"
OPAQUE_ID = "shared-opaque-id"


def _context(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id, {CANVAS}, "example.test")


def _artifact(store: ArtifactStore, *, key: str) -> str:
    document = store.get_or_create_artifact(
        canvas_id=CANVAS,
        idempotency_key=key,
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload={"tenant": store.tenant_id},
        provenance=PROVENANCE,
    )
    return document.opaque_id


def test_identical_idempotency_and_widget_ids_are_tenant_isolated(store: StateStore) -> None:
    first = ArtifactStore(store.conn, tenant_context=_context("tenant-a"))
    second = ArtifactStore(store.conn, tenant_context=_context("tenant-b"))

    first_id = _artifact(first, key="shared-key")
    second_id = _artifact(second, key="shared-key")
    first.map_widget(first_id, canvas_id=CANVAS, widget_id="shared-widget")
    second.map_widget(second_id, canvas_id=CANVAS, widget_id="shared-widget")

    assert first.get_artifact_by_widget(canvas_id=CANVAS, widget_id="shared-widget") is not None
    assert second.get_artifact_by_widget(canvas_id=CANVAS, widget_id="shared-widget") is not None
    assert first_id != second_id


def test_identical_opaque_ids_can_coexist_across_tenants(store: StateStore, monkeypatch) -> None:
    monkeypatch.setattr("lab_agent.artifact_store.secrets.token_urlsafe", lambda _size: OPAQUE_ID)
    first = ArtifactStore(store.conn, tenant_context=_context("tenant-a"))
    second = ArtifactStore(store.conn, tenant_context=_context("tenant-b"))

    first_id = _artifact(first, key="key-a")
    second_id = _artifact(second, key="key-b")

    assert first_id == second_id == OPAQUE_ID
    assert first.get_artifact(first_id, canvas_id=CANVAS).tenant_id == "tenant-a"
    assert second.get_artifact(second_id, canvas_id=CANVAS).tenant_id == "tenant-b"


def test_public_capability_lookup_is_bound_to_its_store_tenant(store: StateStore) -> None:
    first = ArtifactStore(store.conn, tenant_context=_context("tenant-a"))
    second = ArtifactStore(store.conn, tenant_context=_context("tenant-b"))
    first_id = _artifact(first, key="key-a")
    second_id = _artifact(second, key="key-b")
    first_token = first.issue_token(first_id, canvas_id=CANVAS)
    second_token = second.issue_token(second_id, canvas_id=CANVAS)

    first_document = first.get_authorized_artifact(first_id, token=first_token)
    second_document = second.get_authorized_artifact(second_id, token=second_token)

    assert first_document is not None and first_document.tenant_id == "tenant-a"
    assert second_document is not None and second_document.tenant_id == "tenant-b"
    assert first.get_authorized_artifact(second_id, token=second_token) is None
    assert second.get_authorized_artifact(first_id, token=first_token) is None


def test_bound_store_rejects_unallowlisted_canvas(store: StateStore) -> None:
    artifacts = ArtifactStore(store.conn, tenant_context=_context("tenant-a"))

    with pytest.raises(CanvasAccessDeniedError):
        artifacts.create_artifact(
            canvas_id="other-canvas",
            artifact_type=ArtifactType.SETUP,
            state=DecisionState.DRAFT,
            payload={},
            provenance=PROVENANCE,
        )


async def test_browser_capability_url_hides_bound_tenant(tmp_path) -> None:
    context = _context("tenant-a")
    state = StateStore(tmp_path / "tenant.db", tenant_context=context)
    try:
        mcp = FakeMCP()
        widget_id = await durable_browser.write_artifact_browser_durable(
            mcp,
            state,
            Settings(artifact_public_base_url="https://lab.test"),
            canvas_id=CANVAS,
            artifact_type=ArtifactType.SETUP,
            state=DecisionState.DRAFT,
            title="Setup",
            payload={"title": "Setup", "rendered": "safe text"},
            provenance=PROVENANCE,
            discriminator="browser-tenant-test",
            round_index=1,
            predecessor_id="",
            edge_kind="setup",
        )
        url = mcp.notes[widget_id]["url"]
        opaque_id, token = url.split("/artifacts/", 1)[1].split("?token=", 1)
        document = ArtifactStore(state.conn, tenant_context=context).get_authorized_artifact(
            opaque_id, token=token
        )

        assert "tenant-a" not in url
        assert document is not None and document.tenant_id == "tenant-a"
    finally:
        state.close()


def test_default_tenant_store_retains_legacy_single_canvas_compatibility(store: StateStore) -> None:
    artifacts = ArtifactStore(store.conn)
    opaque_id = _artifact(artifacts, key="default-key")

    document = artifacts.get_artifact(opaque_id, canvas_id=CANVAS)

    assert document is not None
    assert document.tenant_id == "default"


def _sibling_canvas_stores(store: StateStore) -> tuple[ArtifactStore, ArtifactStore, str]:
    broad = ArtifactStore(
        store.conn,
        tenant_context=TenantContext("tenant-a", {"canvas-a", "canvas-b"}, "example.test"),
    )
    narrow = ArtifactStore(
        store.conn,
        tenant_context=TenantContext("tenant-a", {"canvas-a"}, "example.test"),
    )
    opaque_id = broad.get_or_create_artifact(
        canvas_id="canvas-b",
        idempotency_key="canvas-b-key",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload={},
        provenance=PROVENANCE,
    ).opaque_id
    return broad, narrow, opaque_id


def _artifact_graph_rows(store: StateStore) -> tuple[tuple[tuple[object, ...], ...], ...]:
    artifacts = tuple(
        tuple(row)
        for row in store.conn.execute(
            "SELECT tenant_id, opaque_id, canvas_id, idempotency_key, current_version, updated_at "
            "FROM artifacts ORDER BY tenant_id, opaque_id"
        )
    )
    tokens = tuple(
        tuple(row)
        for row in store.conn.execute(
            "SELECT tenant_id, opaque_id, canvas_id, status, created_at, revoked_at "
            "FROM artifact_tokens ORDER BY tenant_id, opaque_id, created_at"
        )
    )
    widgets = tuple(
        tuple(row)
        for row in store.conn.execute(
            "SELECT tenant_id, opaque_id, canvas_id, widget_id, created_at, updated_at "
            "FROM artifact_widgets ORDER BY tenant_id, opaque_id"
        )
    )
    return artifacts, tokens, widgets


@pytest.mark.parametrize("operation", ("issue", "rotate", "revoke", "map"))
def test_sibling_canvas_capability_operations_are_denied_without_mutation(
    store: StateStore, operation: str
) -> None:
    broad, narrow, opaque_id = _sibling_canvas_stores(store)
    if operation in {"rotate", "revoke"}:
        broad.issue_token(opaque_id, canvas_id="canvas-b")
    before = _artifact_graph_rows(store)
    operations = {
        "issue": lambda: narrow.issue_token(opaque_id, canvas_id="canvas-b"),
        "rotate": lambda: narrow.rotate_token(opaque_id, canvas_id="canvas-b"),
        "revoke": lambda: narrow.revoke_token(opaque_id, canvas_id="canvas-b"),
        "map": lambda: narrow.map_widget(opaque_id, canvas_id="canvas-b", widget_id="widget-b"),
    }

    with pytest.raises(CanvasAccessDeniedError):
        operations[operation]()

    assert _artifact_graph_rows(store) == before


def test_sibling_canvas_compatibility_lookup_is_denied_without_mutation(store: StateStore) -> None:
    _broad, narrow, _opaque_id = _sibling_canvas_stores(store)
    before = _artifact_graph_rows(store)

    with pytest.raises(CanvasAccessDeniedError):
        from lab_agent.artifact_compat import find_existing_artifact

        find_existing_artifact(
            narrow,
            canvas_id="canvas-b",
            artifact_type=ArtifactType.SETUP,
            keys=("canvas-b-key",),
        )

    assert _artifact_graph_rows(store) == before
