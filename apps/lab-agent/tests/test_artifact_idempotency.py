"""Restart-idempotent canonical artifact creation."""

from __future__ import annotations

import sqlite3

import pytest

from lab_agent.artifact_store import (
    ArtifactIdempotencyConflictError,
    ArtifactStore,
)
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.state_store import StateStore

PROVENANCE = ArtifactProvenance(provider="claude", trigger_id="trigger-1")


@pytest.fixture
def artifacts(store: StateStore, clock) -> ArtifactStore:
    return ArtifactStore(store.conn, clock=clock)


def _get_or_create(
    artifacts: ArtifactStore,
    *,
    canvas_id: str = "canvas-1",
    key: str = "setup:idea-1",
    artifact_type: ArtifactType = ArtifactType.SETUP,
    payload: dict | None = None,
):
    return artifacts.get_or_create_artifact(
        canvas_id=canvas_id,
        idempotency_key=key,
        artifact_type=artifact_type,
        state=DecisionState.DRAFT,
        payload=payload or {"value": "first"},
        provenance=PROVENANCE,
    )


def test_replay_returns_original_without_appending(artifacts: ArtifactStore) -> None:
    first = _get_or_create(artifacts)
    replay = _get_or_create(artifacts, payload={"value": "replayed"})

    assert replay.opaque_id == first.opaque_id
    assert replay.current_version == 1
    assert replay.payload == {"value": "first"}
    assert len(artifacts.list_versions(first.opaque_id, canvas_id="canvas-1")) == 1
    count = artifacts.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
    assert count == 1


def test_replay_after_reopen_returns_same_artifact(tmp_path, clock, rng) -> None:
    path = tmp_path / "state.db"
    first_state = StateStore(path, clock=clock, rng=rng)
    first = _get_or_create(ArtifactStore(first_state.conn, clock=clock))
    first_state.close()

    second_state = StateStore(path, clock=clock, rng=rng)
    try:
        replay = _get_or_create(ArtifactStore(second_state.conn, clock=clock))
        assert replay.opaque_id == first.opaque_id
        assert replay.current_version == 1
    finally:
        second_state.close()


def test_same_key_is_scoped_per_canvas(artifacts: ArtifactStore) -> None:
    first = _get_or_create(artifacts, canvas_id="canvas-1")
    second = _get_or_create(artifacts, canvas_id="canvas-2")

    assert first.opaque_id != second.opaque_id
    assert artifacts.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 2


def test_reusing_key_for_different_type_fails_loud(artifacts: ArtifactStore) -> None:
    _get_or_create(artifacts, artifact_type=ArtifactType.SETUP)

    with pytest.raises(ArtifactIdempotencyConflictError):
        _get_or_create(artifacts, artifact_type=ArtifactType.RESULT)


def test_unique_index_rejects_duplicate_explicit_key(artifacts: ArtifactStore) -> None:
    artifacts.create_artifact(
        canvas_id="canvas-1",
        idempotency_key="fixed-key",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload={},
        provenance=PROVENANCE,
    )

    with pytest.raises(sqlite3.IntegrityError):
        artifacts.create_artifact(
            canvas_id="canvas-1",
            idempotency_key="fixed-key",
            artifact_type=ArtifactType.SETUP,
            state=DecisionState.DRAFT,
            payload={},
            provenance=PROVENANCE,
        )


def test_create_without_key_remains_unique(artifacts: ArtifactStore) -> None:
    first = artifacts.create_artifact(
        canvas_id="canvas-1",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload={},
        provenance=PROVENANCE,
    )
    second = artifacts.create_artifact(
        canvas_id="canvas-1",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload={},
        provenance=PROVENANCE,
    )

    assert first.opaque_id != second.opaque_id
