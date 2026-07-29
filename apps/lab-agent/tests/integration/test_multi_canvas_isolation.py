"""Tenant/canvas durable isolation with identical local identifiers."""

from __future__ import annotations

import pytest

from lab_agent.artifact_store import ArtifactStore
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.notifications import NotificationEnvelope
from lab_agent.state_store import LeaseHeldByOtherError, StateStore
from lab_agent.tenant import TenantContext

CANVAS = "canvas-shared"


def _context(tenant_id: str) -> TenantContext:
    return TenantContext(tenant_id, [CANVAS], "research.example")


def _notification() -> NotificationEnvelope:
    return NotificationEnvelope(
        canvas_id=CANVAS, trigger_id="trigger", closure_id="closed", round_index=1, reason="stop"
    )


def _seed(store: StateStore) -> tuple[str, str]:
    intent = store.prepare_intent(
        idempotency_key="same-intent", canvas_id=CANVAS, kind="write", input_hash="same-input"
    )
    store.reserve_budget(
        reservation_id="same-budget", intent_key=intent.idempotency_key, canvas_id=CANVAS,
        run_id="same-run", tokens=2, cost_usd=0.1, run_token_limit=None,
        run_cost_limit=None, canvas_token_limit=None, canvas_cost_limit=None,
    )
    artifact = ArtifactStore(store.conn, tenant_context=store.tenant_context).create_artifact(
        canvas_id=CANVAS, idempotency_key="same-artifact", artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT, payload={"title": store.tenant_id},
        provenance=ArtifactProvenance(provider="fake"), round=1,
    )
    notification = store.enqueue_notification(_notification())
    store.append_audit_event(CANVAS, "seed", {"tenant": store.tenant_id})
    return artifact.opaque_id, notification.logical_key


def test_identical_ids_are_isolated_across_tenants_and_excluded_per_writer(tmp_path) -> None:
    path = tmp_path / "state.db"
    tenant_a = StateStore(path, tenant_context=_context("tenant-a"))
    tenant_b = StateStore(path, tenant_context=_context("tenant-b"))
    same_tenant_peer = StateStore(path, tenant_context=_context("tenant-a"))
    try:
        artifact_a, notification_a = _seed(tenant_a)
        artifact_b, notification_b = _seed(tenant_b)

        intent_a = tenant_a.get_intent("same-intent")
        intent_b = tenant_b.get_intent("same-intent")
        assert artifact_a != artifact_b and notification_a != notification_b
        assert intent_a is not None and intent_a.tenant_id == "tenant-a"
        assert intent_b is not None and intent_b.tenant_id == "tenant-b"
        assert tenant_a.budget_totals(CANVAS) == tenant_b.budget_totals(CANVAS) == (0, 0.0, 2, 0.1)
        assert tenant_a.list_audit_events(CANVAS)[-1].payload == {"tenant": "tenant-a"}
        assert tenant_b.list_notification_records()[-1].tenant_id == "tenant-b"
        assert ArtifactStore(tenant_a.conn, tenant_context=tenant_a.tenant_context).get_artifact(
            artifact_b, canvas_id=CANVAS
        ) is None

        tenant_a.acquire_canvas_lease(CANVAS, runtime_instance_id="a", ttl_seconds=60)
        tenant_b.acquire_canvas_lease(CANVAS, runtime_instance_id="b", ttl_seconds=60)
        with pytest.raises(LeaseHeldByOtherError):
            same_tenant_peer.acquire_canvas_lease(CANVAS, runtime_instance_id="other", ttl_seconds=60)
    finally:
        same_tenant_peer.close()
        tenant_b.close()
        tenant_a.close()
