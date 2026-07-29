"""Harness stop-policy tests: the :class:`StopTracker` reasons and the governed
``run_loop`` closing path (distinct reason rendered + audited, no further writes).

The ``store``/``clock`` fixtures (tests/conftest) give a manually-advanced clock
so wall-time is deterministic without real sleeps.
"""

from __future__ import annotations

from lab_agent.adapters.base import AdapterResponse
from lab_agent.config import Settings
from lab_agent.loop_governance import governance_stop_reason, make_stop_tracker
from lab_agent.model_gateway import GovernedAdapter, LocalityDeniedError, build_context
from lab_agent.models.governance import Budget, CostEstimate, StopReason, Usage, UsageStatus
from lab_agent.orchestrator import run_loop
from lab_agent.policy import BudgetExceededError
from lab_agent.policy.stop import TERMINAL_STOP_PRIORITY, StopPolicy
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup

SETUP = grounded_setup()
RESULT = {"summary": "A+B reduced marker 30%", "metrics": ["reduction=0.30"]}
LOOP = {
    "loop_connector_id": "c5", "setup_id": "setup1", "result_id": "result1",
    "robot_id": "robot1", "idea_id": "idea1", "ragcluster_id": "rag1", "round": 1,
}
SEED = {"idea1": "{idea: A+B}", "setup1": "Round: 1\nmix A and B", "result1": "marker reduced"}


def _settings(**values: object) -> Settings:
    """Build deterministic test settings without developer-local dotenv values."""
    return Settings(_env_file=None, _env_prefix="__TEST_NO_ENV__", **values)  # type: ignore[arg-type]


class OvershootAdapter(ScriptedAdapter):
    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        return AdapterResponse(
            text=response.text, parsed=response.parsed, tool_calls=response.tool_calls,
            usage=Usage("openai", "gpt-4o-mini", total_tokens=2_000),
        )


def _governed_context(store, settings, adapter):
    settings.provider_endpoints = {"openai": "https://api.openai.com"}
    settings.model_pricing = {"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}}
    settings.pricing_version = "test-v1"
    return build_context(store, settings, "c", {"openai": adapter}, [{"classification": "internal"}])


def _ctx(store, settings):
    return _governed_context(store, settings, object())


def test_stop_policy_preserves_terminal_reason_priority() -> None:
    assert TERMINAL_STOP_PRIORITY == (
        StopReason.TOKEN_BUDGET, StopReason.COST_BUDGET, StopReason.MAX_ROUNDS,
        StopReason.WALL_TIME, StopReason.NO_PROGRESS, StopReason.LOCALITY_DENIAL,
        StopReason.RESERVATION_DENIAL, StopReason.MODEL_DECISION,
    )
    policy = StopPolicy(max_rounds=1, wall_time_budget_seconds=1.0, no_progress_rounds=1)
    both_budgets = Budget(token_limit=1, cost_limit_usd=1.0, tokens_committed=1, cost_committed_usd=1.0)
    assert policy.evaluate(rounds=1, elapsed_seconds=1.0, no_progress_streak=1, budget=both_budgets) is StopReason.TOKEN_BUDGET
    assert policy.evaluate(rounds=1, elapsed_seconds=1.0, no_progress_streak=1, budget=Budget(cost_limit_usd=1.0, cost_committed_usd=1.0)) is StopReason.COST_BUDGET
    assert policy.evaluate(rounds=1, elapsed_seconds=1.0, no_progress_streak=1) is StopReason.MAX_ROUNDS
    assert policy.evaluate(rounds=0, elapsed_seconds=1.0, no_progress_streak=1) is StopReason.WALL_TIME
    assert policy.evaluate(rounds=0, elapsed_seconds=0.0, no_progress_streak=1) is StopReason.NO_PROGRESS


def test_governance_denials_remain_distinct_terminal_reasons() -> None:
    assert governance_stop_reason(LocalityDeniedError("blocked")) is StopReason.LOCALITY_DENIAL
    assert governance_stop_reason(BudgetExceededError("hold denied")) is StopReason.RESERVATION_DENIAL


def test_stop_tracker_no_progress_streak(store):
    settings = _settings(no_progress_rounds=2)  # type: ignore[call-arg]
    tracker = make_stop_tracker(_ctx(store, settings))
    tracker.observe_result("same result")
    assert tracker.check(rounds=1) is None  # streak 1
    tracker.observe_result("same result")
    assert tracker.check(rounds=1) is StopReason.NO_PROGRESS  # streak 2


def test_stop_tracker_resets_streak_on_change(store):
    settings = _settings(no_progress_rounds=2)  # type: ignore[call-arg]
    tracker = make_stop_tracker(_ctx(store, settings))
    tracker.observe_result("first")
    tracker.observe_result("second")  # different -> streak back to 1
    assert tracker.check(rounds=1) is None


def test_stop_tracker_wall_time(store, clock):
    settings = _settings(wall_time_budget_seconds=50.0)  # type: ignore[call-arg]
    tracker = make_stop_tracker(_ctx(store, settings))
    tracker.observe_result("x")
    clock.advance(60.0)
    assert tracker.check(rounds=1) is StopReason.WALL_TIME


def test_stop_tracker_budget_exhaustion(store):
    settings = _settings(run_token_budget=10)  # type: ignore[call-arg]
    ctx = _ctx(store, settings)
    tracker = make_stop_tracker(ctx)
    reservation = ctx.budget.reserve(10, CostEstimate(0.0, "v1", UsageStatus.EXACT))
    ctx.budget.commit(reservation, Usage("openai", "m", total_tokens=10), CostEstimate(0.0, "v1", UsageStatus.EXACT))
    tracker.observe_result("x")
    assert tracker.check(rounds=0) is StopReason.TOKEN_BUDGET


async def test_run_loop_stops_on_no_progress_with_distinct_reason(store):
    settings = _settings(artifact_public_base_url="https://lab.test", no_progress_rounds=1)  # type: ignore[call-arg]
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


async def test_early_model_stop_stages_successor_without_terminal_audit(store):
    settings = _settings(artifact_public_base_url="https://lab.test", loop_min_rounds=2, loop_max_rounds=3)  # type: ignore[call-arg]
    inner = ScriptedAdapter({
        "ExperimentSetup": SETUP, "ExperimentResult": RESULT,
        "LoopDecision": {"proceed": False, "reason": "early"},
    })
    summary = await run_loop(
        FakeMCP(note_text=dict(SEED)), inner, settings, store, canvas_id="c", loop=dict(LOOP)
    )
    assert summary.rounds == 1 and summary.stopped_reason == "successor_staged"
    assert not summary.closed_id and summary.result_ids == ["result1"]
    assert inner.schema_calls == ["LoopDecision", "ExperimentSetup"]
    assert not [event for event in store.list_audit_events("c") if event.event == "loop_stopped"]


async def test_run_loop_closes_at_max_rounds_before_next_write(store):
    settings = _settings(artifact_public_base_url="https://lab.test", loop_max_rounds=1, loop_min_rounds=1)  # type: ignore[call-arg]
    mcp = FakeMCP(note_text=dict(SEED))
    inner = ScriptedAdapter({"LoopDecision": {"proceed": True, "reason": "go"}})
    gov = _governed_context(store, settings, inner)
    summary = await run_loop(mcp, GovernedAdapter(gov), settings, store, canvas_id="c", loop=dict(LOOP), gov=gov)
    assert summary.stopped_reason == StopReason.MAX_ROUNDS.value
    assert inner.schema_calls == ["LoopDecision"]
    assert len([w for w in mcp.notes.values() if str(w["title"]).startswith("[EXP:Closed")]) == 1


async def test_post_commit_token_overshoot_closes_before_next_canvas_write(store):
    settings = _settings(
        artifact_public_base_url="https://lab.test", run_token_budget=1_000, model_max_output_tokens=1
    )  # type: ignore[call-arg]
    mcp = FakeMCP(note_text=dict(SEED))
    inner = OvershootAdapter({"LoopDecision": {"proceed": True, "reason": "go"}})
    gov = _governed_context(store, settings, inner)
    summary = await run_loop(mcp, GovernedAdapter(gov), settings, store, canvas_id="c", loop=dict(LOOP), gov=gov)
    assert summary.stopped_reason == StopReason.TOKEN_BUDGET.value
    assert len(mcp.notes) == len(SEED) + 1  # closure only; no next setup/result write


async def test_run_loop_closes_on_reservation_denial_without_provider_call(store):
    settings = _settings(artifact_public_base_url="https://lab.test", run_token_budget=1)  # type: ignore[call-arg]
    mcp = FakeMCP(note_text=dict(SEED))
    inner = ScriptedAdapter({"LoopDecision": {"proceed": True, "reason": "go"}})
    gov = _governed_context(store, settings, inner)
    summary = await run_loop(mcp, GovernedAdapter(gov), settings, store, canvas_id="c", loop=dict(LOOP), gov=gov)
    assert summary.stopped_reason == StopReason.RESERVATION_DENIAL.value
    assert inner.schema_calls == []


async def test_run_loop_without_gov_closes_at_the_legacy_round_backstop(store):
    """The ungoverned boundary still enforces its legacy round cap."""
    settings = _settings(artifact_public_base_url="https://lab.test", loop_max_rounds=1, loop_min_rounds=1)  # type: ignore[call-arg]
    mcp = FakeMCP(note_text=dict(SEED))
    adapter = ScriptedAdapter(
        {"ExperimentSetup": SETUP, "ExperimentResult": RESULT, "LoopDecision": {"proceed": True, "reason": "go"}}
    )
    summary = await run_loop(mcp, adapter, settings, store, canvas_id="c", loop=dict(LOOP))
    assert summary.stopped_reason == StopReason.MAX_ROUNDS.value
    assert len([e for e in store.list_audit_events("c") if e.event == "loop_stopped"]) == 1
