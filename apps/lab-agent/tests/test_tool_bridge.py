"""Tests for the read-tool allowlist and tool-call execution bridge."""

from __future__ import annotations

from lab_agent.adapters.base import ToolCall, ToolSpec
from lab_agent.tool_bridge import _bare_name, execute_tool_calls, select_read_tools
from tests.fakes import FakeMCP


def _spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, description="", parameters={"type": "object"})


def test_select_read_tools_keeps_only_read_subset():
    tools = [_spec("get_note"), _spec("create_note"), _spec("check_ragcluster_connections")]
    kept = {t.name for t in select_read_tools(tools)}
    assert kept == {"get_note", "check_ragcluster_connections"}


def test_bare_name_strips_namespacing():
    assert _bare_name("mcp__canvus__get_note") == "get_note"
    assert _bare_name("get_note") == "get_note"


def test_select_read_tools_handles_namespaced_names():
    tools = [_spec("mcp__canvus__get_note"), _spec("mcp__canvus__create_connector")]
    kept = {t.name for t in select_read_tools(tools)}
    assert kept == {"mcp__canvus__get_note"}


async def test_execute_tool_calls_runs_allowed_and_blocks_writes():
    mcp = FakeMCP()
    calls = [
        ToolCall(id="1", name="check_ragcluster_connections", arguments={"canvas_id": "c"}),
        ToolCall(id="2", name="create_note", arguments={"canvas_id": "c", "text": "x"}),
    ]
    messages = await execute_tool_calls(mcp, calls)  # type: ignore[arg-type]
    assert [m["tool_call_id"] for m in messages] == ["1", "2"]
    assert "clusters" in messages[0]["content"]
    assert "not permitted" in messages[1]["content"]
    # The blocked write must not have created a note.
    assert mcp.notes == {}
