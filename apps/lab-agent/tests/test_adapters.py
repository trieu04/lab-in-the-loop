"""Tests for the provider message-conversion helpers (no network)."""

from __future__ import annotations

from lab_agent.adapters.claude_adapter import _split_system, _to_claude_messages
from lab_agent.adapters.openai_adapter import _to_openai_messages

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
