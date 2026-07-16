"""Grounding, validation, and execution ordering regressions."""

from __future__ import annotations

import pytest

from lab_agent.approval_service import RobotExecutionAuthorizationError
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.integrations.in_silico import DeterministicInSilicoAdapter, MockFailureMode
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentSetup
from lab_agent.models.states import DecisionState
from lab_agent.orchestrator_robot import run_on_robot
from lab_agent.orchestrator_validation import run_in_silico_validation
from lab_agent.state_store import StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter

SETTINGS = Settings(artifact_public_base_url="https://lab.test")


def _setup() -> ExperimentSetup:
    return ExperimentSetup(
        rationale="Grounded proposal.",
        conditions=["temperature=25C"],
        steps=["measure baseline"],
        expected_readouts=["signal"],
        hypothesis="Signal remains measurable.",
        success_criteria=["record signal"],
    )


def _persist(store: StateStore) -> None:
    artifact = ArtifactStore(store.conn).create_artifact(
        canvas_id="canvas",
        idempotency_key="setup",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.APPROVED_FOR_IN_SILICO,
        payload=_setup().model_dump(),
        provenance=ArtifactProvenance(provider="harness", source_widget_id="idea"),
        round=1,
    )
    ArtifactStore(store.conn).map_widget(artifact.opaque_id, canvas_id="canvas", widget_id="setup")


@pytest.fixture
def store(tmp_path):
    state = StateStore(tmp_path / "state.db")
    yield state
    state.close()


async def test_canonical_setup_reaches_validator_when_pending_bucket_is_empty_no_fallback(
    store: StateStore,
) -> None:
    """Explicitly present empty setups_needing_validation is authoritative.

    Must not fall back to setups bucket. The new key presence takes precedence.
    """
    _persist(store)
    mcp = FakeMCP(workflow={"setups_needing_validation": [], "setups": [{"widget_id": "setup"}]})

    counts = await process_once(mcp, ScriptedAdapter({}), SETTINGS, store, "runtime", "canvas")

    assert counts["validations"] == 0  # empty bucket is authoritative, no fallback


async def test_canonical_setup_reaches_validator_when_pending_bucket_absent_falls_back_to_setups(
    store: StateStore,
) -> None:
    """When setups_needing_validation key is absent, fall back to setups bucket.

    Ensures backward compatibility when the new key is not present.
    """
    _persist(store)
    mcp = FakeMCP(workflow={"setups": [{"widget_id": "setup"}]})

    counts = await process_once(mcp, ScriptedAdapter({}), SETTINGS, store, "runtime", "canvas")

    assert counts["validations"] == 1
    assert len(store.list_validation_results("canvas")) == 1


async def test_failed_validation_never_reaches_robot_execution(store: StateStore) -> None:
    _persist(store)
    mcp = FakeMCP()

    with pytest.raises(RuntimeError):
        await run_in_silico_validation(
            mcp,
            SETTINGS,
            store,
            DeterministicInSilicoAdapter(MockFailureMode.TIMEOUT),
            "canvas",
            "setup",
            1,
        )
    with pytest.raises(RobotExecutionAuthorizationError):
        await run_on_robot(
            mcp,
            ScriptedAdapter({"ExperimentResult": {"summary": "must not write", "metrics": []}}),
            SETTINGS,
            store,
            canvas_id="canvas",
            setup_id="setup",
            setup_text="ignored",
            robot_id="robot",
            round_index=1,
        )
    assert not any(note["title"].startswith("[EXP:Result") for note in mcp.notes.values())
