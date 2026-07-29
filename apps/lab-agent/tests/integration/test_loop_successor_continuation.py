"""Real-SQLite coverage for gated, restart-safe loop successors."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import SecretStr

from lab_agent.approval_service import ApprovalSubmission, submit_approval
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.governance_context import build_context
from lab_agent.integrations.identity import (
    DevelopmentIdentity,
    StaticDevelopmentIdentityProvider,
    hash_development_credential,
)
from lab_agent.models.artifact import ArtifactProvenance, ArtifactType
from lab_agent.models.experiment import ExperimentResult, ExperimentSetup
from lab_agent.models.governance import StopReason
from lab_agent.models.states import DecisionState
from lab_agent.models.validation import ApprovalDecision, ApprovalRole, hash_validation_result
from lab_agent.orchestrator import run_loop
from lab_agent.orchestrator_payloads import result_payload, setup_payload
from lab_agent.state_store import StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup

CANVAS = "canvas"
LOOP = {
    "loop_connector_id": "loop",
    "setup_id": "setup",
    "result_id": "result",
    "robot_id": "robot",
    "idea_id": "idea",
    "ragcluster_id": "rag",
    "round": 1,
}
NOW = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
SETUP_DATA = grounded_setup(
    {
        "rationale": "round one",
        "conditions": ["temperature=25C"],
        "expected_readouts": ["signal"],
        "hypothesis": "Signal remains measurable.",
        "success_criteria": ["record signal"],
    }
)
SETUP = ExperimentSetup.model_validate(SETUP_DATA)
RESULT = ExperimentResult(summary="round one result", metrics=["signal=1"])


def _settings(**values: object) -> Settings:
    return Settings(
        _env_file=None,
        _env_prefix="__TEST_NO_ENV__",
        artifact_public_base_url="https://lab.test",
        wet_lab_execution_enabled=True,
        loop_min_rounds=2,
        **values,
    )  # type: ignore[arg-type]


def _seed(store: StateStore, mcp: FakeMCP) -> None:
    artifacts = ArtifactStore(store.conn)
    setup_title, setup_data = setup_payload(
        SETUP, idea_text="test idea", idea_id="idea", round_index=1
    )
    setup = artifacts.create_artifact(
        canvas_id=CANVAS,
        idempotency_key="seed-setup",
        artifact_type=ArtifactType.SETUP,
        state=DecisionState.APPROVED_FOR_IN_SILICO,
        payload={**setup_data, "title": setup_title},
        provenance=ArtifactProvenance(provider="harness", source_widget_id="idea"),
        round=1,
    )
    result_title, result_data = result_payload(RESULT, setup_id="setup", round_index=1)
    result = artifacts.create_artifact(
        canvas_id=CANVAS,
        idempotency_key="seed-result",
        artifact_type=ArtifactType.RESULT,
        state=DecisionState.ANALYSIS_COMPLETE,
        payload={**result_data, "title": result_title},
        provenance=ArtifactProvenance(provider="harness", source_widget_id="setup"),
        round=1,
    )
    artifacts.map_widget(setup.opaque_id, canvas_id=CANVAS, widget_id="setup")
    artifacts.map_widget(result.opaque_id, canvas_id=CANVAS, widget_id="result")
    mcp.seed_widget("idea", "Note", text="{idea: test idea}")
    mcp.seed_widget("setup", "Browser", title=setup_title)
    mcp.seed_widget("robot", "Note", title="Robot_arm")
    mcp.seed_widget("result", "Browser", title=result_title)
    mcp.seed_connector("result", "setup")


def _identity_provider() -> StaticDevelopmentIdentityProvider:
    return StaticDevelopmentIdentityProvider(
        identities=(
            DevelopmentIdentity(
                "scientist", frozenset({ApprovalRole.SCIENTIST}), hash_development_credential("s")
            ),
            DevelopmentIdentity(
                "lead", frozenset({ApprovalRole.LAB_LEAD}), hash_development_credential("l")
            ),
        ),
        clock=lambda: NOW,
    )


async def _approve(store: StateStore) -> None:
    continuation = store.get_loop_continuation(CANVAS, "setup:setup")
    proposal_hash = continuation.staged_proposal_hash if continuation is not None else ""
    results = store.list_validation_results(CANVAS, proposal_hash=proposal_hash or None)
    result = results[-1]
    for role, secret in ((ApprovalRole.SCIENTIST, "s"), (ApprovalRole.LAB_LEAD, "l")):
        await submit_approval(
            canvas_id=CANVAS,
            current_proposal_hash=result.proposal_hash,
            current_validation_result_hash=hash_validation_result(result),
            submission=ApprovalSubmission(
                approval_id=f"{role.value}-round-two",
                required_role=role,
                decision=ApprovalDecision.APPROVE,
                rationale="Reviewed current successor.",
                credential=SecretStr(secret),
            ),
            state_store=store,
            identity_provider=_identity_provider(),
        )


async def test_successor_waits_for_current_validation_and_approvals_across_restart(
    tmp_path,
) -> None:
    path = tmp_path / "state.db"
    store = StateStore(path)
    mcp = FakeMCP(live=False)
    _seed(store, mcp)
    mcp.workflow = {
        "setups_needing_validation": [{"widget_id": "setup", "title": "[EXP:Setup v001]"}],
        "setups_needing_run": [],
    }
    await process_once(mcp, ScriptedAdapter({}), _settings(), store, "initial-validator", CANVAS)
    decision_adapter = ScriptedAdapter(
        {
            "LoopDecision": {"proceed": False, "reason": "too early", "next_focus": "repeat"},
            "ExperimentSetup": {**SETUP_DATA, "rationale": "round two"},
            "ExperimentResult": {"summary": "must not be emitted", "metrics": []},
        }
    )

    staged = await run_loop(
        mcp, decision_adapter, _settings(), store, canvas_id=CANVAS, loop=dict(LOOP)
    )
    assert staged.stopped_reason == "successor_staged"
    assert staged.setup_ids == ["setup", "setup"] and staged.result_ids == ["result"]
    assert decision_adapter.schema_calls == ["LoopDecision", "ExperimentSetup"]
    continuation = store.get_loop_continuation(CANVAS, "setup:setup")
    assert continuation is not None and continuation.round_index == 2
    assert continuation.staged_setup_id == "setup" and continuation.staged_proposal_hash
    store.close()

    restarted = StateStore(path)
    try:
        calls = list(decision_adapter.schema_calls)
        replay = await run_loop(
            mcp, decision_adapter, _settings(), restarted, canvas_id=CANVAS, loop=dict(LOOP)
        )
        assert (
            replay.stopped_reason == "successor_staged" and decision_adapter.schema_calls == calls
        )
        restored = restarted.get_loop_continuation(CANVAS, "setup:setup")
        assert restored is not None and restored.run_id == continuation.run_id
        assert restored.started_at == continuation.started_at and restored.previous_result_signature

        mcp.workflow = {
            "setups_needing_validation": [],
            "setups_needing_run": [],
            "validations": [{"widget_id": "stale-validation"}],
        }
        validated = await process_once(
            mcp, ScriptedAdapter({}), _settings(), restarted, "validator", CANVAS
        )
        assert validated["validations"] == 1
        assert len(restarted.list_validation_results(CANVAS)) == 2
        assert (
            len(
                restarted.list_validation_results(
                    CANVAS, proposal_hash=restored.staged_proposal_hash
                )
            )
            == 1
        )

        pending = {
            "widget_id": "setup",
            "robot_id": "robot",
            "title": "[EXP:Setup v002]",
            "execution_mode": "manual",
            "stale_result_id": "result",
        }
        mcp.workflow = {"setups_needing_validation": [], "setups_needing_run": [pending]}
        result_adapter = ScriptedAdapter(
            {"ExperimentResult": {"summary": "round two", "metrics": ["signal=2"]}}
        )
        assert (await process_once(mcp, result_adapter, _settings(), restarted, "blocked", CANVAS))[
            "runs"
        ] == 0
        assert result_adapter.schema_calls == []

        await _approve(restarted)
        ran = await process_once(mcp, result_adapter, _settings(), restarted, "runner", CANVAS)
        assert ran["runs"] == 1 and result_adapter.schema_calls == ["ExperimentResult"]
        current_result = ArtifactStore(restarted.conn).get_artifact_by_widget(
            canvas_id=CANVAS, widget_id="result"
        )
        assert current_result is not None and current_result.round == 2
        continuation = restarted.get_loop_continuation(CANVAS, "setup:setup")
        assert continuation is not None and continuation.staged_result_id == "result"
    finally:
        restarted.close()


async def test_unusable_stale_result_creates_and_connects_replacement(tmp_path) -> None:
    store = StateStore(tmp_path / "replacement.db")
    mcp = FakeMCP(live=False)
    _seed(store, mcp)
    try:
        mcp.workflow = {
            "setups_needing_validation": [{"widget_id": "setup"}],
            "setups_needing_run": [],
        }
        await process_once(mcp, ScriptedAdapter({}), _settings(), store, "validator", CANVAS)
        await _approve(store)
        pending = {
            "widget_id": "setup",
            "robot_id": "robot",
            "title": "[EXP:Setup v002]",
            "execution_mode": "auto",
            "stale_result_id": "missing-result",
        }
        mcp.workflow = {"setups_needing_validation": [], "setups_needing_run": [pending]}
        adapter = ScriptedAdapter(
            {"ExperimentResult": {"summary": "replacement", "metrics": ["ok"]}}
        )
        assert (await process_once(mcp, adapter, _settings(), store, "runner", CANVAS))["runs"] == 1
        replacement = next(
            wid for wid, item in mcp.notes.items() if item["title"].startswith("[EXP:Result v002]")
        )
        assert replacement != "missing-result" and (replacement, "setup") in mcp.connectors
    finally:
        store.close()


async def test_round_qualified_terminal_replay_preserves_governed_stop(tmp_path) -> None:
    path = tmp_path / "state.db"
    store = StateStore(path)
    mcp = FakeMCP(live=False)
    _seed(store, mcp)
    settings = _settings(no_progress_rounds=1)
    adapter = ScriptedAdapter({"LoopDecision": {"proceed": True, "reason": "unused"}})
    gov = build_context(
        store, settings, CANVAS, {"local": adapter}, [{"classification": "internal"}]
    )
    first = await run_loop(
        mcp, adapter, settings, store, canvas_id=CANVAS, loop=dict(LOOP), gov=gov
    )
    assert first.stopped_reason == StopReason.NO_PROGRESS.value and first.closed_id
    store.close()

    restarted = StateStore(path)
    try:
        calls = list(adapter.schema_calls)
        replay = await run_loop(
            mcp, adapter, settings, restarted, canvas_id=CANVAS, loop=dict(LOOP), gov=gov
        )
        assert (
            replay.closed_id == first.closed_id
            and replay.stopped_reason == StopReason.NO_PROGRESS.value
        )
        assert adapter.schema_calls == calls
        continuation = restarted.get_loop_continuation(CANVAS, "setup:setup")
        assert continuation is not None
        assert (
            restarted.find_terminal_event(CANVAS, f"loop-terminal:{continuation.run_id}")
            is not None
        )
    finally:
        restarted.close()
