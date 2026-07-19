"""Regressions for fail-closed trigger and evidence locality metadata."""

from __future__ import annotations

import json

import pytest

from lab_agent.adapters.base import AdapterResponse, ToolCall
from lab_agent.config import Settings
from lab_agent.evidence import EvidenceLedger
from lab_agent.model_gateway import GovernedAdapter, LocalityDeniedError, build_context
from lab_agent.models.governance import DataClassification
from lab_agent.policy.locality import LocalityPolicy
from lab_agent.tool_bridge import execute_tool_calls
from lab_agent.watch import process_once
from tests.fakes import FakeMCP
from tests.test_model_gateway import MESSAGES, SpyAdapter


def _settings() -> Settings:
    return Settings(
        artifact_public_base_url="https://lab.test",
        openai_api_key="key",
        model_pricing={"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}},
        pricing_version="test-v1",
        provider_endpoints={"openai": "https://openai.example"},
        provider_data_classifications={"openai": ["internal"]},
    )


def test_unknown_is_denied_even_when_allowlist_names_unknown() -> None:
    policy = LocalityPolicy(
        {"openai": ["internal", "unknown"]},
        {"openai": "https://openai.example"},
    )

    authorization = policy.authorize("openai", [DataClassification.UNKNOWN])

    assert authorization.allowed is False
    assert "unknown" in authorization.reason


async def test_trigger_classification_replaces_prior_call_metadata(store) -> None:
    inner = SpyAdapter([AdapterResponse(text="must not run")])
    context = build_context(
        store,
        _settings(),
        "canvas",
        {"openai": inner},
        [{"data_classification": "internal"}],
    )
    context.set_source_metadata([{"data_classification": "restricted"}])

    with pytest.raises(LocalityDeniedError):
        await GovernedAdapter(context).generate(MESSAGES)

    assert inner.calls == 0
    assert context.classifications == [DataClassification.RESTRICTED]


class ClassifiedToolMCP(FakeMCP):
    async def call_tool(self, name: str, arguments: dict[str, object]) -> str:
        if name == "get_note":
            return json.dumps(
                {"data_classification": "restricted", "text": "sensitive evidence"}
            )
        return await super().call_tool(name, arguments)


async def test_retrieved_evidence_classification_denies_later_provider_turn(store) -> None:
    call = ToolCall(id="tool-1", name="get_note", arguments={"note_id": "note-1"})
    inner = SpyAdapter(
        [AdapterResponse(text="read evidence", tool_calls=[call]), AdapterResponse(text="leak")]
    )
    context = build_context(
        store,
        _settings(),
        "canvas",
        {"openai": inner},
        [{"data_classification": "internal"}],
    )
    governed = GovernedAdapter(context)
    messages = list(MESSAGES)
    first = await governed.generate(messages)
    messages.append(
        {
            "role": "assistant",
            "content": first.text,
            "tool_calls": [tool.as_message_dict() for tool in first.tool_calls],
        }
    )
    ledger = EvidenceLedger()
    messages.extend(await execute_tool_calls(ClassifiedToolMCP(), first.tool_calls, ledger))

    with pytest.raises(LocalityDeniedError):
        await governed.generate(messages)

    record = ledger.record_for(ledger.audit_summary()[0]["source_id"])
    assert record is not None
    assert record.data_classification is DataClassification.RESTRICTED
    assert inner.calls == 1


async def test_watch_applies_trigger_classification_before_first_turn(store) -> None:
    workflow = {
        "ideas_needing_setup": [
            {
                "widget_id": "idea-1",
                "ragcluster_id": "rag-1",
                "data_classification": "restricted",
            }
        ],
        "setups_needing_run": [],
        "loops": [],
    }
    mcp = FakeMCP(note_text={"idea-1": "{idea: sensitive}"}, workflow=workflow)
    inner = SpyAdapter([AdapterResponse(text="must not run")])
    context = build_context(
        store,
        _settings(),
        "canvas",
        {"openai": inner},
        [{"data_classification": "internal"}],
    )

    await process_once(
        mcp,
        GovernedAdapter(context),
        _settings(),
        store,
        "runtime",
        "canvas",
        context,
    )

    assert inner.calls == 0
    generated = [row for key, row in mcp.notes.items() if key != "idea-1"]
    assert len(generated) == 1
    assert generated[0]["title"].startswith("[EXP:Closed]")
    assert mcp.connectors == [("idea-1", generated[0]["id"])]
    denied = [event for event in store.list_audit_events("canvas") if event.event == "locality_denied"]
    assert denied[0].payload["classifications"] == ["restricted"]


async def test_watch_missing_trigger_metadata_stays_unknown_and_denies(store) -> None:
    workflow = {
        "ideas_needing_setup": [{"widget_id": "idea-1", "ragcluster_id": "rag-1"}],
        "setups_needing_run": [],
        "loops": [],
    }
    mcp = FakeMCP(note_text={"idea-1": "{idea: unclassified}"}, workflow=workflow)
    inner = SpyAdapter([AdapterResponse(text="must not run")])
    context = build_context(
        store, _settings(), "canvas", {"openai": inner}, [{"classification": "internal"}]
    )

    await process_once(
        mcp, GovernedAdapter(context), _settings(), store, "runtime", "canvas", context
    )

    assert inner.calls == 0
    assert len([row for key, row in mcp.notes.items() if key != "idea-1"]) == 1
    denied = [event for event in store.list_audit_events("canvas") if event.event == "locality_denied"]
    assert denied[0].payload["classifications"] == ["unknown"]
