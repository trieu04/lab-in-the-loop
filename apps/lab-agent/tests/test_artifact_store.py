"""``ArtifactStore`` document lifecycle: create, get, append-version history,
large payloads, and canvas scope checks.

Shared ``_Clock``/``_Rng``/``store`` fixtures live in ``tests/conftest.py``.
Token and widget-mapping behavior live in ``test_artifact_tokens.py`` and
``test_artifact_widgets.py``.
"""

from __future__ import annotations

import pytest

from lab_agent.artifact_store import ArtifactCanvasScopeError, ArtifactStore
from lab_agent.models.artifact import ArtifactMetadata, ArtifactProvenance, ArtifactType
from lab_agent.models.states import DecisionState
from lab_agent.state_store import StateStore

PROVENANCE = ArtifactProvenance(provider="claude", model_name="claude-x", trigger_id="t1")


@pytest.fixture
def artifact_store(store: StateStore, clock) -> ArtifactStore:
    return ArtifactStore(store.conn, clock=clock)


# ── migration ────────────────────────────────────────────────────────────


def test_migration_creates_artifact_tables(store: StateStore) -> None:
    tables = {row["name"] for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"artifacts", "artifact_versions", "artifact_tokens", "artifact_widgets"} <= tables


def test_migration_is_idempotent_on_reopen(tmp_path, clock, rng) -> None:
    db_path = tmp_path / "state.db"
    first = StateStore(db_path, clock=clock, rng=rng)
    first.close()
    second = StateStore(db_path, clock=clock, rng=rng)
    try:
        row = second.conn.execute(
            "SELECT checksum FROM schema_migrations WHERE version=2"
        ).fetchone()
        assert row is not None and len(row["checksum"]) == 64
        tables = {r["name"] for r in second.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "artifacts" in tables
    finally:
        second.close()


# ── create / get ─────────────────────────────────────────────────────────


def test_create_artifact_returns_version_one(artifact_store: ArtifactStore) -> None:
    doc = artifact_store.create_artifact(
        canvas_id="c1",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.DRAFT,
        payload={"rationale": "because"},
        provenance=PROVENANCE,
    )
    assert doc.current_version == 1
    assert doc.canvas_id == "c1"
    assert doc.artifact_type == ArtifactType.SETUP
    assert doc.state == DecisionState.DRAFT
    assert doc.payload == {"rationale": "because"}
    assert doc.widget_id is None
    assert len(doc.content_hash) == 64
    assert doc.opaque_id  # store-generated, non-empty


def test_create_artifact_generates_unique_opaque_ids(artifact_store: ArtifactStore) -> None:
    first = artifact_store.create_artifact(
        canvas_id="c1", artifact_type=ArtifactType.SETUP, state=DecisionState.DRAFT,
        payload={"a": 1}, provenance=PROVENANCE,
    )
    second = artifact_store.create_artifact(
        canvas_id="c1", artifact_type=ArtifactType.SETUP, state=DecisionState.DRAFT,
        payload={"a": 2}, provenance=PROVENANCE,
    )
    assert first.opaque_id != second.opaque_id


def test_get_artifact_round_trips_metadata_and_provenance(artifact_store: ArtifactStore) -> None:
    metadata = ArtifactMetadata(tags=["round1"], extra={"note": "ok"})
    created = artifact_store.create_artifact(
        canvas_id="c1", artifact_type=ArtifactType.RESULT, state=DecisionState.RUNNING,
        payload={"summary": "done"}, provenance=PROVENANCE, metadata=metadata, round=3,
    )
    fetched = artifact_store.get_artifact(created.opaque_id, canvas_id="c1")
    assert fetched is not None
    assert fetched.metadata.tags == ["round1"]
    assert fetched.metadata.extra == {"note": "ok"}
    assert fetched.provenance.provider == "claude"
    assert fetched.round == 3


def test_get_artifact_unknown_id_returns_none(artifact_store: ArtifactStore) -> None:
    assert artifact_store.get_artifact("does-not-exist", canvas_id="c1") is None


# ── append_version: no destructive overwrite ──────────────────────────────


def test_append_version_bumps_pointer_and_preserves_history(artifact_store: ArtifactStore) -> None:
    created = artifact_store.create_artifact(
        canvas_id="c1", artifact_type=ArtifactType.SETUP, state=DecisionState.DRAFT,
        payload={"step": 1}, provenance=PROVENANCE,
    )
    updated = artifact_store.append_version(
        created.opaque_id, canvas_id="c1", state=DecisionState.NEEDS_REVIEW,
        payload={"step": 2}, provenance=PROVENANCE,
    )
    assert updated.current_version == 2
    assert updated.payload == {"step": 2}
    assert updated.state == DecisionState.NEEDS_REVIEW

    versions = artifact_store.list_versions(created.opaque_id, canvas_id="c1")
    assert [v.version for v in versions] == [1, 2]
    # Version 1's payload must remain exactly as first written -- append-only.
    assert versions[0].payload == {"step": 1}
    assert versions[1].payload == {"step": 2}
    assert versions[0].content_hash != versions[1].content_hash


def test_append_version_defaults_round_to_previous_when_not_given(artifact_store: ArtifactStore) -> None:
    created = artifact_store.create_artifact(
        canvas_id="c1", artifact_type=ArtifactType.SETUP, state=DecisionState.DRAFT,
        payload={}, provenance=PROVENANCE, round=2,
    )
    updated = artifact_store.append_version(
        created.opaque_id, canvas_id="c1", state=DecisionState.NEEDS_REVIEW, payload={}, provenance=PROVENANCE,
    )
    assert updated.round == 2


def test_append_version_wrong_canvas_raises_scope_error(artifact_store: ArtifactStore) -> None:
    created = artifact_store.create_artifact(
        canvas_id="c1", artifact_type=ArtifactType.SETUP, state=DecisionState.DRAFT,
        payload={}, provenance=PROVENANCE,
    )
    with pytest.raises(ArtifactCanvasScopeError):
        artifact_store.append_version(
            created.opaque_id, canvas_id="c2", state=DecisionState.NEEDS_REVIEW, payload={}, provenance=PROVENANCE
        )


# ── large payloads ─────────────────────────────────────────────────────────


def test_large_payload_round_trips_without_truncation(artifact_store: ArtifactStore) -> None:
    big_text = "x" * 500_000
    payload = {"body": big_text, "items": [f"item-{i}" for i in range(2000)]}
    created = artifact_store.create_artifact(
        canvas_id="c1", artifact_type=ArtifactType.ANALYSIS, state=DecisionState.ANALYSIS_COMPLETE,
        payload=payload, provenance=PROVENANCE,
    )
    fetched = artifact_store.get_artifact(created.opaque_id, canvas_id="c1")
    assert fetched is not None
    assert fetched.payload == payload
    assert len(fetched.payload["body"]) == 500_000


# ── cross-canvas denial ──────────────────────────────────────────────────


def test_get_artifact_cross_canvas_returns_none(artifact_store: ArtifactStore) -> None:
    created = artifact_store.create_artifact(
        canvas_id="c1", artifact_type=ArtifactType.SETUP, state=DecisionState.DRAFT,
        payload={}, provenance=PROVENANCE,
    )
    assert artifact_store.get_artifact(created.opaque_id, canvas_id="c2") is None


def test_list_versions_cross_canvas_returns_empty(artifact_store: ArtifactStore) -> None:
    created = artifact_store.create_artifact(
        canvas_id="c1", artifact_type=ArtifactType.SETUP, state=DecisionState.DRAFT,
        payload={}, provenance=PROVENANCE,
    )
    assert artifact_store.list_versions(created.opaque_id, canvas_id="c2") == []
