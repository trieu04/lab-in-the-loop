"""Phase 5 loop closures triggered by real governed model paths."""

from __future__ import annotations

from typing import Any

from lab_agent.adapters.base import AdapterResponse, Usage
from lab_agent.config import Settings
from lab_agent.model_gateway import GovernedAdapter, build_context
from lab_agent.models.governance import StopReason
from lab_agent.orchestrator import run_loop
from tests.fakes import FakeMCP, ScriptedAdapter

LOOP = {
    "loop_connector_id": "loop-1",
    "setup_id": "setup1",
    "result_id": "result1",
    "robot_id": "robot1",
    "idea_id": "idea1",
    "ragcluster_id": "rag1",
    "round": 1,
}
SEED = {
    "idea1": "{idea: A+B}",
    "setup1": "Round: 1\nMix A+B",
    "result1": "Round: 1\nA+B reduced marker",
}
PRICING = {"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}}


class CostOvershootAdapter(ScriptedAdapter):
    async def generate(self, *args: Any, **kwargs: Any) -> AdapterResponse:
        response = await super().generate(*args, **kwargs)
        return AdapterResponse(
            text=response.text,
            parsed=response.parsed,
            tool_calls=response.tool_calls,
            usage=Usage("openai", "gpt-4o-mini", prompt_tokens=1_000, completion_tokens=1_000),
        )


class ResultReadingAdvancesClockMCP(FakeMCP):
    def __init__(self, *args: Any, clock: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._clock = clock

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "get_note" and arguments.get("note_id") == "result1":
            self._clock.advance(11.0)
        return await super().call_tool(name, arguments)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "artifact_public_base_url": "https://lab.test",
        "openai_api_key": "test-key",
        "model_pricing": PRICING,
        "pricing_version": "v1",
        "model_max_output_tokens": 1,
        "provider_endpoints": {"openai": "https://openai.example"},
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _governed(store: Any, settings: Settings, adapter: ScriptedAdapter) -> GovernedAdapter:
    context = build_context(
        store,
        settings,
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )
    return GovernedAdapter(context)


def _assert_single_closure(store: Any, mcp: FakeMCP) -> None:
    generated = [widget for widget_id, widget in mcp.notes.items() if widget_id not in SEED]
    assert len(generated) == 1
    assert str(generated[0]["title"]).startswith("[EXP:Closed]")
    assert mcp.connectors == [("result1", generated[0]["id"])]
    assert len(store.list_edges("canvas")) == 1
    stopped = [event for event in store.list_audit_events("canvas") if event.event == "loop_stopped"]
    assert len(stopped) == 1


async def test_cost_overshoot_closes_once_before_next_canvas_write(store) -> None:
    settings = _settings(run_cost_budget_usd=1.0)
    inner = CostOvershootAdapter({"LoopDecision": {"proceed": True, "reason": "continue"}})
    governed = _governed(store, settings, inner)
    mcp = FakeMCP(note_text=dict(SEED))

    summary = await run_loop(
        mcp, governed, settings, store, canvas_id="canvas", loop=dict(LOOP), gov=governed.context
    )

    assert summary.stopped_reason == StopReason.COST_BUDGET.value
    assert inner.schema_calls == ["LoopDecision"]
    _assert_single_closure(store, mcp)
    assert [event.payload["reason"] for event in store.list_audit_events("canvas") if event.event == "loop_stopped"] == ["cost_budget"]


async def test_wall_time_closes_before_provider_schema_call_or_follow_on_write(store, clock) -> None:
    settings = _settings(wall_time_budget_seconds=10.0)
    inner = ScriptedAdapter({"LoopDecision": {"proceed": True, "reason": "continue"}})
    governed = _governed(store, settings, inner)
    mcp = ResultReadingAdvancesClockMCP(note_text=dict(SEED), clock=clock)

    summary = await run_loop(
        mcp, governed, settings, store, canvas_id="canvas", loop=dict(LOOP), gov=governed.context
    )

    assert summary.stopped_reason == StopReason.WALL_TIME.value
    assert inner.schema_calls == []
    _assert_single_closure(store, mcp)
    assert [event.payload["reason"] for event in store.list_audit_events("canvas") if event.event == "loop_stopped"] == ["wall_time"]
