"""``ArtifactStore`` Browser-widget mapping: restart-safe idempotency,
canvas-scoped uniqueness conflicts, and cross-canvas denial.

Shared ``_Clock``/``_Rng``/``store`` fixtures live in ``tests/conftest.py``.
"""

from __future__ import annotations

import pytest

from lab_agent.artifact_store import ArtifactCanvasScopeError, ArtifactStore
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.state.artifact_widgets import ArtifactWidgetConflictError
from lab_agent.state_store import StateStore

PROVENANCE = ArtifactProvenance(provider="claude", model_name="claude-x", trigger_id="t1")


@pytest.fixture
def artifact_store(store: StateStore, clock) -> ArtifactStore:
    return ArtifactStore(store.conn, clock=clock)


def _make_artifact(artifact_store: ArtifactStore, *, canvas_id: str = "c1") -> str:
    doc = artifact_store.create_artifact(
        canvas_id=canvas_id,
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload={},
        provenance=PROVENANCE,
    )
    return doc.opaque_id


# ── map_widget: happy path + restart idempotency ──────────────────────────


def test_map_widget_is_reflected_in_get_artifact(artifact_store: ArtifactStore) -> None:
    opaque_id = _make_artifact(artifact_store)
    artifact_store.map_widget(opaque_id, canvas_id="c1", widget_id="w1")
    doc = artifact_store.get_artifact(opaque_id, canvas_id="c1")
    assert doc is not None
    assert doc.widget_id == "w1"


def test_map_widget_same_pair_twice_is_idempotent(artifact_store: ArtifactStore) -> None:
    opaque_id = _make_artifact(artifact_store)
    artifact_store.map_widget(opaque_id, canvas_id="c1", widget_id="w1")
    # Simulates a runtime restart re-reconciling the same mapping -- must not raise.
    artifact_store.map_widget(opaque_id, canvas_id="c1", widget_id="w1")
    doc = artifact_store.get_artifact(opaque_id, canvas_id="c1")
    assert doc is not None
    assert doc.widget_id == "w1"


def test_get_artifact_by_widget_finds_the_mapped_artifact(artifact_store: ArtifactStore) -> None:
    opaque_id = _make_artifact(artifact_store)
    artifact_store.map_widget(opaque_id, canvas_id="c1", widget_id="w1")
    doc = artifact_store.get_artifact_by_widget(canvas_id="c1", widget_id="w1")
    assert doc is not None
    assert doc.opaque_id == opaque_id


def test_get_artifact_by_widget_unknown_widget_returns_none(artifact_store: ArtifactStore) -> None:
    assert artifact_store.get_artifact_by_widget(canvas_id="c1", widget_id="no-such-widget") is None


# ── conflicts ──────────────────────────────────────────────────────────────


def test_remapping_artifact_to_a_different_widget_conflicts(artifact_store: ArtifactStore) -> None:
    opaque_id = _make_artifact(artifact_store)
    artifact_store.map_widget(opaque_id, canvas_id="c1", widget_id="w1")
    with pytest.raises(ArtifactWidgetConflictError):
        artifact_store.map_widget(opaque_id, canvas_id="c1", widget_id="w2")


def test_two_artifacts_claiming_same_widget_in_same_canvas_conflicts(artifact_store: ArtifactStore) -> None:
    first = _make_artifact(artifact_store)
    second = _make_artifact(artifact_store)
    artifact_store.map_widget(first, canvas_id="c1", widget_id="w1")
    with pytest.raises(ArtifactWidgetConflictError):
        artifact_store.map_widget(second, canvas_id="c1", widget_id="w1")


def test_same_widget_id_in_different_canvases_does_not_conflict(artifact_store: ArtifactStore) -> None:
    first = _make_artifact(artifact_store, canvas_id="c1")
    second = _make_artifact(artifact_store, canvas_id="c2")
    artifact_store.map_widget(first, canvas_id="c1", widget_id="w1")
    # Uniqueness is canvas-scoped -- the same widget_id string in a different
    # canvas is a distinct widget and must not collide.
    artifact_store.map_widget(second, canvas_id="c2", widget_id="w1")
    doc1 = artifact_store.get_artifact_by_widget(canvas_id="c1", widget_id="w1")
    doc2 = artifact_store.get_artifact_by_widget(canvas_id="c2", widget_id="w1")
    assert doc1 is not None and doc1.opaque_id == first
    assert doc2 is not None and doc2.opaque_id == second


# ── cross-canvas scope ─────────────────────────────────────────────────────


def test_map_widget_wrong_canvas_raises_scope_error(artifact_store: ArtifactStore) -> None:
    opaque_id = _make_artifact(artifact_store, canvas_id="c1")
    with pytest.raises(ArtifactCanvasScopeError):
        artifact_store.map_widget(opaque_id, canvas_id="c2", widget_id="w1")


def test_get_artifact_by_widget_cross_canvas_returns_none(artifact_store: ArtifactStore) -> None:
    opaque_id = _make_artifact(artifact_store, canvas_id="c1")
    artifact_store.map_widget(opaque_id, canvas_id="c1", widget_id="w1")
    # The mapping row is canvas-scoped by construction, so looking it up under
    # a different canvas simply finds nothing.
    assert artifact_store.get_artifact_by_widget(canvas_id="c2", widget_id="w1") is None
