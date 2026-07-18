"""Unit tests for lab_agent.nodes: the canvas write surface, fail-closed.

``MCPClient.call_tool`` does not raise on a tool-level failure -- a failed
``create_note``/``create_connector`` still returns normally, as a
``{"error": ...}`` JSON string (see ``mcp_client.py``). These tests prove
``create_node``/``connect`` never turn that (or any other untrustworthy
response shape) into a silently-empty widget id -- they raise
:class:`lab_agent.nodes.MCPToolError` instead, so a caller can never mistake
a failed write for a phantom success.

Two fault sources are exercised, matching the two real MCP failure modes:
- ``FakeMCP.fail_next`` / ``fail_next_as_error_payload`` (see ``tests/fakes.py``)
  for the two shapes a real tool-level failure can take: a raised transport
  exception, and a non-raising ``{"error": ...}`` response.
- ``_RawMCP`` (local to this file) for response shapes ``FakeMCP`` has no
  reason to ever produce on its own: malformed JSON, a non-object response,
  and a missing/empty ``id``.
"""

from __future__ import annotations

from typing import Any

import pytest

from lab_agent import nodes
from lab_agent.models.states import DecisionState
from tests.fakes import FakeMCP


class _RawMCP:
    """Returns one fixed raw string for every ``call_tool``, regardless of
    tool name or arguments -- for response shapes ``FakeMCP`` never produces
    naturally (malformed JSON, non-object JSON, missing/empty id)."""

    def __init__(self, raw: str) -> None:
        self._raw = raw
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self.calls.append((name, arguments))
        return self._raw


# ── create_node: success path ────────────────────────────────────────────


async def test_create_node_returns_new_widget_id_on_success():
    mcp = FakeMCP()
    widget_id = await nodes.create_node(mcp, "c", "[EXP:Setup v001] idea", "body text", 0.0, 0.0)
    assert widget_id == "note1"
    assert mcp.notes[widget_id]["title"] == "[EXP:Setup v001] idea"
    assert mcp.notes[widget_id]["text"] == "body text"


async def test_create_node_appends_status_line_when_state_given():
    mcp = FakeMCP()
    widget_id = await nodes.create_node(
        mcp, "c", "[EXP:Setup v001] idea", "body text", 0.0, 0.0, state=DecisionState.DRAFT
    )
    assert mcp.notes[widget_id]["text"] == "body text\n\nStatus: DRAFT"


# ── connect: success path and empty-endpoint short-circuit ──────────────


async def test_connect_returns_new_connector_id_on_success():
    mcp = FakeMCP()
    connector_id = await nodes.connect(mcp, "c", "src1", "dst1")
    assert connector_id == "conn1"
    assert mcp.connectors == [("src1", "dst1")]


async def test_connect_short_circuits_to_empty_string_without_calling_mcp_when_src_missing():
    mcp = FakeMCP()
    connector_id = await nodes.connect(mcp, "c", "", "dst1")
    assert connector_id == ""
    assert mcp.connectors == []  # no call was made at all


async def test_connect_short_circuits_to_empty_string_without_calling_mcp_when_dst_missing():
    mcp = FakeMCP()
    connector_id = await nodes.connect(mcp, "c", "src1", "")
    assert connector_id == ""
    assert mcp.connectors == []


# ── create_node / connect: fail closed on a non-raising error payload ───


async def test_create_node_raises_on_error_payload_never_returns_empty_id():
    mcp = FakeMCP()
    mcp.fail_next_as_error_payload("create_note")
    with pytest.raises(nodes.MCPToolError, match="create_note failed"):
        await nodes.create_node(mcp, "c", "[EXP:Setup v001] idea", "body", 0.0, 0.0)
    assert mcp.notes == {}  # no widget was recorded despite the "successful" call


async def test_connect_raises_on_error_payload_never_returns_empty_id():
    mcp = FakeMCP()
    mcp.fail_next_as_error_payload("create_connector")
    with pytest.raises(nodes.MCPToolError, match="create_connector failed"):
        await nodes.connect(mcp, "c", "src1", "dst1")
    assert mcp.connectors == []  # no phantom edge recorded


# ── create_node / connect: fail closed on a raised transport exception ──


async def test_create_node_propagates_transport_exception():
    mcp = FakeMCP()
    mcp.fail_next("create_note")
    with pytest.raises(RuntimeError, match="simulated failure"):
        await nodes.create_node(mcp, "c", "[EXP:Setup v001] idea", "body", 0.0, 0.0)


async def test_connect_propagates_transport_exception():
    mcp = FakeMCP()
    mcp.fail_next("create_connector")
    with pytest.raises(RuntimeError, match="simulated failure"):
        await nodes.connect(mcp, "c", "src1", "dst1")


# ── create_node / connect: fail closed on untrustworthy response shapes ──


async def test_create_node_raises_on_malformed_json():
    mcp = _RawMCP("not json{")
    with pytest.raises(nodes.MCPToolError, match="malformed JSON"):
        await nodes.create_node(mcp, "c", "[EXP:Setup v001] idea", "body", 0.0, 0.0)  # type: ignore[arg-type]


async def test_create_node_raises_on_non_object_response():
    mcp = _RawMCP('["not", "an", "object"]')
    with pytest.raises(nodes.MCPToolError, match="non-object response"):
        await nodes.create_node(mcp, "c", "[EXP:Setup v001] idea", "body", 0.0, 0.0)  # type: ignore[arg-type]


async def test_create_node_raises_on_missing_id():
    mcp = _RawMCP("{}")
    with pytest.raises(nodes.MCPToolError, match="missing a non-empty 'id'"):
        await nodes.create_node(mcp, "c", "[EXP:Setup v001] idea", "body", 0.0, 0.0)  # type: ignore[arg-type]


async def test_create_node_raises_on_empty_id():
    mcp = _RawMCP('{"id": ""}')
    with pytest.raises(nodes.MCPToolError, match="missing a non-empty 'id'"):
        await nodes.create_node(mcp, "c", "[EXP:Setup v001] idea", "body", 0.0, 0.0)  # type: ignore[arg-type]


async def test_connect_raises_on_malformed_json():
    mcp = _RawMCP("not json{")
    with pytest.raises(nodes.MCPToolError, match="malformed JSON"):
        await nodes.connect(mcp, "c", "src1", "dst1")  # type: ignore[arg-type]


async def test_connect_raises_on_missing_id():
    mcp = _RawMCP("{}")
    with pytest.raises(nodes.MCPToolError, match="missing a non-empty 'id'"):
        await nodes.connect(mcp, "c", "src1", "dst1")  # type: ignore[arg-type]


# ── read_note_text: stays lenient (a read path, not a write path) ───────


async def test_read_note_text_returns_text_on_success():
    mcp = FakeMCP()
    mcp.seed_widget("note1", "Note", text="hello")
    assert await nodes.read_note_text(mcp, "c", "note1") == "hello"


async def test_read_note_text_returns_empty_string_on_error_payload_never_raises():
    mcp = FakeMCP()
    mcp.fail_next_as_error_payload("get_note")
    assert await nodes.read_note_text(mcp, "c", "note1") == ""


async def test_read_note_text_returns_empty_string_on_malformed_json_never_raises():
    mcp = _RawMCP("not json{")
    assert await nodes.read_note_text(mcp, "c", "note1") == ""  # type: ignore[arg-type]


async def test_read_note_text_returns_empty_string_when_note_not_found():
    mcp = FakeMCP()
    assert await nodes.read_note_text(mcp, "c", "ghost") == ""
