"""Persistence and recovery tests for in-silico validation Browser artifacts."""

from __future__ import annotations

import pytest

from lab_agent import nodes
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.integrations.in_silico import DeterministicInSilicoAdapter
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.orchestrator_validation import run_in_silico_validation
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP


def _setup() -> ExperimentSetup:
    return ExperimentSetup(
        rationale="Test proposal.",
        conditions=["temperature=25C"],
        steps=["measure baseline"],
        expected_readouts=["signal"],
        hypothesis="Signal remains measurable.",
        success_criteria=["record signal"],
    )


def _persist(store: StateStore) -> None:
    artifacts = ArtifactStore(store.conn)
    doc = artifacts.create_artifact(
        canvas_id="c",
        idempotency_key="setup",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.APPROVED_FOR_IN_SILICO,
        payload=_setup().model_dump(),
        provenance=ArtifactProvenance(provider="harness"),
        round=1,
    )
    artifacts.map_widget(doc.opaque_id, canvas_id="c", widget_id="setup")


async def test_validation_persists_before_browser_failure_and_recovers_after_restart(
    tmp_path,
) -> None:
    database = tmp_path / "state.db"
    settings = Settings(artifact_public_base_url="https://lab.test")
    mcp = FakeMCP(workflow={"validations": []})
    first = StateStore(database)
    try:
        _persist(first)
        mcp.fail_next_as_error_payload("create_browser")
        with pytest.raises(nodes.MCPToolError):
            await run_in_silico_validation(
                mcp, settings, first, DeterministicInSilicoAdapter(), "c", "setup", 1
            )
        assert (
            first.conn.execute(
                "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'in_silico'"
            ).fetchone()[0]
            == 1
        )
        assert first.conn.execute("SELECT COUNT(*) FROM in_silico_results").fetchone()[0] == 1
    finally:
        first.close()

    second = StateStore(database)
    try:
        outcome = await run_in_silico_validation(
            mcp, settings, second, DeterministicInSilicoAdapter(), "c", "setup", 1
        )
        assert outcome.validation_artifact_widget_id
        assert (
            second.conn.execute(
                "SELECT COUNT(*) FROM artifacts WHERE artifact_type = 'in_silico'"
            ).fetchone()[0]
            == 1
        )
        assert second.conn.execute("SELECT COUNT(*) FROM in_silico_results").fetchone()[0] == 1
        assert mcp.notes[outcome.validation_artifact_widget_id]["title"].startswith(
            "[EXP:Validation]"
        )
    finally:
        second.close()
