"""Regressions for actual-usage budget overshoot handling."""

from __future__ import annotations

import pytest

from lab_agent.adapters.base import AdapterResponse, Usage
from lab_agent.config import Settings
from lab_agent.model_gateway import (
    GovernedAdapter,
    PostResponseBudgetExceededError,
    build_context,
    governed_generate,
)
from lab_agent.models.governance import StopReason, TaskStage
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter, grounded_setup
from tests.test_model_gateway import MESSAGES, PRICING, SpyAdapter


@pytest.mark.parametrize(
    ("limits", "expected"),
    [
        ({"run_token_budget": 1_000}, StopReason.TOKEN_BUDGET),
        ({"run_cost_budget_usd": 1.0}, StopReason.COST_BUDGET),
    ],
)
async def test_actual_usage_overshoot_is_raised_before_response_returns(
    store, limits: dict[str, object], expected: StopReason
) -> None:
    usage = Usage(
        "openai",
        "gpt-4o-mini",
        prompt_tokens=1_000,
        completion_tokens=1_000,
        total_tokens=2_000,
    )
    adapter = SpyAdapter([AdapterResponse(parsed={"ok": True}, usage=usage)])
    settings = Settings(
        openai_api_key="key",
        model_pricing=PRICING,
        pricing_version="v1",
        provider_endpoints={"openai": "https://openai.example"},
        model_max_output_tokens=1,
        **limits,
    )
    context = build_context(
        store,
        settings,
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )

    with pytest.raises(PostResponseBudgetExceededError) as raised:
        await governed_generate(context, stage=TaskStage.SETUP, messages=MESSAGES)

    assert raised.value.reason is expected
    assert adapter.calls == 1
    assert context.budget.run.tokens_committed == 2_000
    assert store.list_incomplete_intents("canvas") == []


class OvershootAdapter(ScriptedAdapter):
    async def generate(self, *args, **kwargs):
        response = await super().generate(*args, **kwargs)
        return AdapterResponse(
            text=response.text,
            parsed=response.parsed,
            tool_calls=response.tool_calls,
            usage=Usage("openai", "gpt-4o-mini", total_tokens=2_000),
        )


def _workflow_settings() -> Settings:
    return Settings(
        artifact_public_base_url="https://lab.test",
        run_token_budget=1_000,
        model_pricing=PRICING,
        pricing_version="v1",
        model_max_output_tokens=1,
        provider_endpoints={"openai": "https://openai.example"},
    )


def _governed(store, settings: Settings, adapter: ScriptedAdapter) -> GovernedAdapter:
    context = build_context(
        store,
        settings,
        "canvas",
        {"openai": adapter},
        [{"classification": "internal"}],
    )
    return GovernedAdapter(context)


@pytest.mark.parametrize(
    ("workflow", "seed", "structured", "predecessor", "expected_schema_calls"),
    [
        (
            {
                "ideas_needing_setup": [
                    {
                        "widget_id": "idea1",
                        "ragcluster_id": "rag1",
                        "data_classification": "internal",
                    }
                ]
            },
            {"idea1": "{idea: A+B}"},
            {"ExperimentSetup": grounded_setup()},
            "idea1",
            [],
        ),
        (
            {
                "setups_needing_run": [
                    {
                        "widget_id": "setup1",
                        "robot_id": "robot1",
                        "title": "[EXP:Setup v001]",
                        "data_classification": "internal",
                    }
                ]
            },
            {"setup1": "Round: 1\nMix A+B"},
            {"ExperimentResult": {"summary": "mock result", "metrics": ["x=1"]}},
            "setup1",
            ["ExperimentResult"],
        ),
    ],
)
async def test_preloop_overshoot_writes_only_deduplicated_governance_closure(
    store,
    workflow: dict[str, object],
    seed: dict[str, str],
    structured: dict[str, object],
    predecessor: str,
    expected_schema_calls: list[str],
) -> None:
    snapshot = {"ideas_needing_setup": [], "setups_needing_run": [], "loops": [], **workflow}
    mcp = FakeMCP(note_text=seed, workflow=snapshot)
    settings = _workflow_settings()
    inner = OvershootAdapter(structured)
    governed = _governed(store, settings, inner)

    await process_once(mcp, governed, settings, store, "runtime", "canvas", governed.context)

    generated = [row for key, row in mcp.notes.items() if key not in seed]
    assert [row["title"].split(" #", 1)[0] for row in generated] == ["[EXP:Closed] after v001"]
    assert mcp.connectors == [(predecessor, generated[0]["id"])]
    assert inner.schema_calls == expected_schema_calls
    assert len([event for event in store.list_audit_events("canvas") if event.event == "governance_stopped"]) == 1
