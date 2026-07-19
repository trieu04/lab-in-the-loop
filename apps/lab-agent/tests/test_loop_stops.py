"""Harness stop-policy tests: the :class:`StopTracker` reasons and the governed
``run_loop`` closing path (distinct reason rendered + audited, no further writes).

The ``store``/``clock`` fixtures (tests/conftest) give a manually-advanced clock
so wall-time is deterministic without real sleeps.
"""

from __future__ import annotations

from lab_agent.adapters.base import AdapterResponse
from lab_agent.config import Settings
from lab_agent.loop_governance import make_stop_tracker
from lab_agent.model_gateway import GovernedAdapter, build_context
from lab_agent.models.governance import CostEstimate, StopReason, Usage, UsageStatus
from lab_agent.orchestrator import run_loop
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup

SETUP = grounded_setup()
RESULT = {"summary": "A+B reduced marker 30%", "metrics": ["reduction=0.30"]}
LOOP = {
    "loop_connector_id": "c5", "setup_id": "setup1", "result_id": "result1",
    "robot_id": "robot1", "idea_id": "idea1", "ragcluster_id": "rag1", "round": 1,
}
SEED = {"idea1": "{idea: A+B}", "setup1": "Round: 1\nmix A and B", "result1": "marker reduced"}


class OvershootAdapter(ScriptedAdapter):
    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        return AdapterResponse(
            text=response.text, parsed=response.parsed, tool_calls=response.tool_calls,
            usage=Usage("openai", "gpt-4o-mini", total_tokens=2_000),
        )


def _governed_context(store, settings, adapter):
    settings.provider_endpoints = {"openai": "https://openai.example"}
    settings.model_pricing = {"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}}
    settings.pricing_version = "test-v1"
    return build_context(store, settings, "c", {"openai": adapter}, [{"classification": "internal"}])


def _ctx(store, settings):
    return _governed_context(store, settings, object())


def test_stop_tracker_no_progress_streak(store):
    settings = Settings(no_progress_rounds=2)  # type: ignore[call-arg]
    tracker = make_stop_tracker(_ctx(store, settings))
    tracker.observe_result("same result")
    assert tracker.check(rounds=1) is None  # streak 1
    tracker.observe_result("same result")
    assert tracker.check(rounds=1) is StopReason.NO_PROGRESS  # streak 2


def test_stop_tracker_resets_streak_on_change(store):
    settings = Settings(no_progress_rounds=2)  # type: ignore[call-arg]
    tracker = make_stop_tracker(_ctx(store, settings))
    tracker.observe_result("first")
    tracker.observe_result("second")  # different -> streak back to 1
    assert tracker.check(rounds=1) is None


def test_stop_tracker_wall_time(store, clock):
    settings = Settings(wall_time_budget_seconds=50.0)  # type: ignore[call-arg]
    tracker = make_stop_tracker(_ctx(store, settings))
    tracker.observe_result("x")
    clock.advance(60.0)
    assert tracker.check(rounds=1) is StopReason.WALL_TIME


def test_stop_tracker_budget_exhaustion(store):
    settings = Settings(run_token_budget=10)  # type: ignore[call-arg]
    ctx = _ctx(store, settings)
    tracker = make_stop_tracker(ctx)
    reservation = ctx.budget.reserve(10, CostEstimate(0.0, "v1", UsageStatus.EXACT))
    ctx.budget.commit(reservation, Usage("openai", "m", total_tokens=10), CostEstimate(0.0, "v1", UsageStatus.EXACT))
    tracker.observe_result("x")
    assert tracker.check(rounds=0) is StopReason.TOKEN_BUDGET


async def test_run_loop_stops_on_no_progress_with_distinct_reason(store):
    settings = Settings(artifact_public_base_url="https://lab.test", no_progress_rounds=1)  # type: ignore[call-arg]
    mcp = FakeMCP(note_text=dict(SEED))
    inner = ScriptedAdapter(
        {"ExperimentSetup": SETUP, "ExperimentResult": RESULT, "LoopDecision": {"proceed": True, "reason": "go"}}
    )
    gov = _governed_context(store, settings, inner)
    summary = await run_loop(
        mcp, GovernedAdapter(gov), settings, store, canvas_id="c", loop=dict(LOOP), gov=gov
    )

    assert summary.stopped_reason == StopReason.NO_PROGRESS.value
    assert summary.closed_id  # a closed node was written with the distinct reason
    assert inner.schema_calls == []  # stopped at loop top -> no model/canvas writes after stop
    stopped = [e for e in store.list_audit_events("c") if e.event == "loop_stopped"]
    assert len(stopped) == 1
    assert stopped[0].payload["reason"] == "no_progress"
    closed = [w for w in mcp.notes.values() if str(w["title"]).startswith("[EXP:Closed")]
    assert len(closed) == 1


async def test_run_loop_closes_at_max_rounds_before_next_write(store):
    settings = Settings(artifact_public_base_url="https://lab.test", loop_max_rounds=1)  # type: ignore[call-arg]
    mcp = FakeMCP(note_text=dict(SEED))
    inner = ScriptedAdapter({"LoopDecision": {"proceed": True, "reason": "go"}})
    gov = _governed_context(store, settings, inner)
    summary = await run_loop(mcp, GovernedAdapter(gov), settings, store, canvas_id="c", loop=dict(LOOP), gov=gov)
    assert summary.stopped_reason == StopReason.MAX_ROUNDS.value
    assert inner.schema_calls == ["LoopDecision"]
    assert len([w for w in mcp.notes.values() if str(w["title"]).startswith("[EXP:Closed")]) == 1


async def test_post_commit_token_overshoot_closes_before_next_canvas_write(store):
    settings = Settings(
        artifact_public_base_url="https://lab.test", run_token_budget=1_000, model_max_output_tokens=1
    )  # type: ignore[call-arg]
    mcp = FakeMCP(note_text=dict(SEED))
    inner = OvershootAdapter({"LoopDecision": {"proceed": True, "reason": "go"}})
    gov = _governed_context(store, settings, inner)
    summary = await run_loop(mcp, GovernedAdapter(gov), settings, store, canvas_id="c", loop=dict(LOOP), gov=gov)
    assert summary.stopped_reason == StopReason.TOKEN_BUDGET.value
    assert len(mcp.notes) == len(SEED) + 1  # closure only; no next setup/result write


async def test_run_loop_closes_on_reservation_denial_without_provider_call(store):
    settings = Settings(artifact_public_base_url="https://lab.test", run_token_budget=1)  # type: ignore[call-arg]
    mcp = FakeMCP(note_text=dict(SEED))
    inner = ScriptedAdapter({"LoopDecision": {"proceed": True, "reason": "go"}})
    gov = _governed_context(store, settings, inner)
    summary = await run_loop(mcp, GovernedAdapter(gov), settings, store, canvas_id="c", loop=dict(LOOP), gov=gov)
    assert summary.stopped_reason == StopReason.RESERVATION_DENIAL.value
    assert inner.schema_calls == []


async def test_run_loop_without_gov_is_unchanged(store):
    """The ungoverned path ignores stop policy entirely (backward compatibility)."""
    settings = Settings(artifact_public_base_url="https://lab.test", loop_max_rounds=1)  # type: ignore[call-arg]
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter(
        {"ExperimentSetup": SETUP, "ExperimentResult": RESULT, "LoopDecision": {"proceed": True, "reason": "go"}}
    )
    summary = await run_loop(mcp, adapter, settings, store, canvas_id="c", loop=dict(LOOP))
    assert "backstop" in summary.stopped_reason  # legacy round backstop, not a StopReason
    assert not [e for e in store.list_audit_events("c") if e.event == "loop_stopped"]
