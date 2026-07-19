"""Tests for the provider message-conversion + usage-normalization helpers (no network)."""

from __future__ import annotations

from types import SimpleNamespace

from lab_agent.adapters.claude_adapter import _split_system, _to_claude_messages
from lab_agent.adapters.claude_adapter import _usage_from as claude_usage
from lab_agent.adapters.openai_adapter import _to_openai_messages
from lab_agent.adapters.openai_adapter import _usage_from as openai_usage
from lab_agent.models.governance import UsageStatus

TRANSCRIPT = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "hi"},
    {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": "c1", "name": "get_note", "arguments": {"a": 1}}],
    },
    {"role": "tool", "tool_call_id": "c1", "name": "get_note", "content": "{}"},
    {"role": "tool", "tool_call_id": "c2", "name": "get_widget", "content": "{}"},
]


def test_openai_conversion_shapes_tool_calls_and_results():
    msgs = _to_openai_messages(TRANSCRIPT)
    assistant = next(m for m in msgs if m["role"] == "assistant")
    assert assistant["tool_calls"][0]["function"]["name"] == "get_note"
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "c1"


def test_claude_split_system_and_merges_tool_results():
    system, rest = _split_system(TRANSCRIPT)
    assert system == "sys"
    claude = _to_claude_messages(rest)
    # The two consecutive tool results must merge into one user turn.
    tool_turns = [m for m in claude if m["role"] == "user" and m["content"][0]["type"] == "tool_result"]
    assert len(tool_turns) == 1
    assert len(tool_turns[0]["content"]) == 2
    # The assistant turn carries a tool_use block.
    assistant = next(m for m in claude if m["role"] == "assistant")
    assert assistant["content"][0]["type"] == "tool_use"
    assert assistant["content"][0]["name"] == "get_note"


def test_openai_usage_normalized_exact():
    completion = SimpleNamespace(
        id="req_1",
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )
    usage = openai_usage(completion, "gpt-4o-mini")
    assert usage.status is UsageStatus.EXACT
    assert usage.provider == "openai"
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (10, 5, 15)
    assert usage.request_id == "req_1"


def test_openai_usage_totals_summed_when_absent():
    completion = SimpleNamespace(
        id="req_2", usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=0)
    )
    assert openai_usage(completion, "m").total_tokens == 10


def test_openai_usage_unavailable_without_payload():
    usage = openai_usage(SimpleNamespace(id="req_3", usage=None), "gpt-4o-mini")
    assert usage.status is UsageStatus.UNAVAILABLE
    assert usage.total_tokens == 0
    assert usage.request_id == "req_3"


def test_openai_usage_unavailable_when_counts_are_omitted():
    completion = SimpleNamespace(
        id="req_4", usage=SimpleNamespace(prompt_tokens=None, completion_tokens=None, total_tokens=None)
    )
    usage = openai_usage(completion, "gpt-4o-mini")
    assert usage.status is UsageStatus.UNAVAILABLE
    assert usage.total_tokens == 0


def test_claude_usage_normalized_and_summed():
    resp = SimpleNamespace(id="msg_1", usage=SimpleNamespace(input_tokens=12, output_tokens=8))
    usage = claude_usage(resp, "claude-sonnet-4-5")
    assert usage.status is UsageStatus.EXACT
    assert usage.provider == "claude"
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (12, 8, 20)
    assert usage.request_id == "msg_1"


def test_claude_usage_includes_cache_token_categories():
    response = SimpleNamespace(
        id="msg_cached",
        usage=SimpleNamespace(
            input_tokens=10,
            cache_creation_input_tokens=3,
            cache_read_input_tokens=7,
            output_tokens=8,
        ),
    )

    usage = claude_usage(response, "claude-sonnet-4-5")

    assert usage.status is UsageStatus.EXACT
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (20, 8, 28)
    assert usage.request_id == "msg_cached"


def test_claude_usage_unavailable_without_payload():
    usage = claude_usage(SimpleNamespace(id="msg_2", usage=None), "claude-sonnet-4-5")
    assert usage.status is UsageStatus.UNAVAILABLE
    assert usage.total_tokens == 0


def test_claude_usage_unavailable_when_counts_are_omitted():
    resp = SimpleNamespace(id="msg_3", usage=SimpleNamespace(input_tokens=None, output_tokens=None))
    usage = claude_usage(resp, "claude-sonnet-4-5")
    assert usage.status is UsageStatus.UNAVAILABLE
    assert usage.total_tokens == 0
