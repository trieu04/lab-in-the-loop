"""Audited/rendered locality-denial closure regressions."""

from __future__ import annotations

from lab_agent.adapters.base import AdapterResponse
from lab_agent.config import Settings
from lab_agent.model_gateway import GovernedAdapter, build_context
from lab_agent.models.governance import StopReason
from lab_agent.orchestrator import run_loop
from lab_agent.watch import process_once
from tests.fakes import FakeMCP
from tests.test_model_gateway import SpyAdapter

LOOP = {
    "loop_connector_id": "connector-1",
    "setup_id": "setup-1",
    "result_id": "result-1",
    "robot_id": "robot-1",
    "round": 1,
}


def _settings() -> Settings:
    return Settings(
        artifact_public_base_url="https://lab.test",
        openai_api_key="key",
        provider_endpoints={"openai": "https://openai.example"},
        provider_data_classifications={"openai": ["internal"]},
    )


def _governed(store, inner: SpyAdapter, classification: str) -> GovernedAdapter:
    return GovernedAdapter(
        build_context(
            store,
            _settings(),
            "canvas",
            {"openai": inner},
            [{"data_classification": classification}],
        )
    )


async def test_loop_locality_denial_closes_once_without_provider_or_follow_on_writes(store) -> None:
    mcp = FakeMCP(
        note_text={"setup-1": "Round: 1\nsetup", "result-1": "Round: 1\nresult"}
    )
    inner = SpyAdapter([AdapterResponse(parsed={"proceed": True, "reason": "continue"})])
    governed = _governed(store, inner, "restricted")

    summary = await run_loop(
        mcp,
        governed,
        _settings(),
        store,
        canvas_id="canvas",
        loop=dict(LOOP),
        gov=governed.context,
    )

    assert summary.stopped_reason == StopReason.LOCALITY_DENIAL.value
    assert inner.calls == 0
    generated = [row for key, row in mcp.notes.items() if key not in {"setup-1", "result-1"}]
    assert len(generated) == 1
    assert mcp.connectors == [("result-1", generated[0]["id"])]
    stopped = [event for event in store.list_audit_events("canvas") if event.event == "loop_stopped"]
    assert [event.payload["reason"] for event in stopped] == ["locality_denial"]


async def test_robot_trigger_locality_denial_writes_only_predecessor_closure(store) -> None:
    workflow = {
        "ideas_needing_setup": [],
        "setups_needing_run": [
            {
                "widget_id": "setup-1",
                "robot_id": "robot-1",
                "title": "[EXP:Setup v001]",
                "data_classification": "restricted",
            }
        ],
        "loops": [],
    }
    mcp = FakeMCP(note_text={"setup-1": "Round: 1\nsetup"}, workflow=workflow)
    inner = SpyAdapter([AdapterResponse(parsed={"summary": "must not run", "metrics": []})])
    governed = _governed(store, inner, "internal")

    await process_once(
        mcp, governed, _settings(), store, "runtime", "canvas", governed.context
    )

    assert inner.calls == 0
    generated = [row for key, row in mcp.notes.items() if key != "setup-1"]
    assert len(generated) == 1
    assert mcp.connectors == [("setup-1", generated[0]["id"])]
    assert not [row for row in generated if row["title"].startswith("[EXP:Result")]
