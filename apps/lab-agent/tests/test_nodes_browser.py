"""Unit tests for the Browser write surface in ``lab_agent.nodes``.

``create_artifact_widget``/``update_artifact_widget`` share the fail-closed
``_call_checked``/``_require_id`` path proven exhaustively for ``create_node``
in ``test_nodes.py`` (malformed JSON, non-object, missing id). Here we cover the
Browser-specific behaviour: the widget type/size/title/url payload, the 120-char
title cap, and that a tool-level error payload still raises instead of yielding
a phantom empty id.
"""

from __future__ import annotations

import pytest

from lab_agent import nodes
from tests.fakes import FakeMCP

_URL = "https://lab.test/artifacts/abc123?token=secrettoken"


async def test_create_artifact_widget_returns_browser_id_with_url_and_marker():
    mcp = FakeMCP()
    wid = await nodes.create_artifact_widget(mcp, "c", "[EXP:Setup v001] idea #a1b2c3d4e5f6", _URL, 0.0, 420.0)
    assert wid == "browser1"
    widget = mcp.notes[wid]
    assert widget["widget_type"] == "Browser"  # not a Note
    assert widget["title"] == "[EXP:Setup v001] idea #a1b2c3d4e5f6"
    assert widget["url"] == _URL


async def test_create_artifact_widget_truncates_title_to_120_chars():
    mcp = FakeMCP()
    wid = await nodes.create_artifact_widget(mcp, "c", "x" * 200, _URL, 0.0, 0.0)
    assert len(mcp.notes[wid]["title"]) == 120


async def test_create_artifact_widget_raises_on_error_payload_never_phantom_widget():
    mcp = FakeMCP()
    mcp.fail_next_as_error_payload("create_browser")
    with pytest.raises(nodes.MCPToolError, match="create_browser failed"):
        await nodes.create_artifact_widget(mcp, "c", "[EXP:Setup v001]", _URL, 0.0, 0.0)
    assert [w for w in mcp.notes.values() if w["widget_type"] == "Browser"] == []


async def test_update_artifact_widget_repairs_url_and_title_in_place():
    mcp = FakeMCP()
    mcp.seed_widget("browser1", "Browser", title="[EXP:Setup v001] old", url="https://lab.test/artifacts/x?token=old")
    wid = await nodes.update_artifact_widget(
        mcp, "c", "browser1", url=_URL, title="[EXP:Setup v001] idea #a1b2c3d4e5f6", x=0.0, y=420.0
    )
    assert wid == "browser1"
    assert mcp.notes["browser1"]["url"] == _URL  # stale token URL repaired
    assert mcp.notes["browser1"]["title"] == "[EXP:Setup v001] idea #a1b2c3d4e5f6"


async def test_update_artifact_widget_raises_on_error_payload():
    mcp = FakeMCP()
    mcp.fail_next_as_error_payload("update_browser")
    with pytest.raises(nodes.MCPToolError, match="update_browser failed"):
        await nodes.update_artifact_widget(mcp, "c", "browser1", url=_URL, title="[EXP:Setup v001]")
