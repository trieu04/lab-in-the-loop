"""Tests for lab_agent.canvas_probe: fail-closed crash-recovery probes.

These exercise the probes against ``FakeMCP``'s ``check_widget_connections``
handler (mirrors canvus-mcp's real tool shape, see ``tests/fakes.py``) and its
``fail_next`` fault hook, so the "MCP call fails -> probe raises, never
silently treated as not-found" contract is proven end-to-end, not just
asserted from ``_parse`` alone.
"""

from __future__ import annotations

import json

import pytest

from lab_agent import canvas_probe
from tests.fakes import FakeMCP


def test_tag_suffix_is_first_12_hex_chars():
    assert canvas_probe.tag_suffix("a1b2c3d4e5f6789xyz") == "a1b2c3d4e5f6"


def test_tagged_title_appends_tag_and_fits_120_char_cap():
    tagged = canvas_probe.tagged_title("x" * 200, "a1b2c3d4e5f6789")
    assert len(tagged) == 120
    assert tagged.endswith(" #a1b2c3d4e5f6")


def test_parse_raises_on_error_payload():
    with pytest.raises(RuntimeError, match="boom"):
        canvas_probe._parse(json.dumps({"error": "boom"}), tool="scan_experiment_workflow")


def test_parse_passes_through_payload_without_error_key():
    payload = canvas_probe._parse(json.dumps({"found": True}), tool="check_widget_connections")
    assert payload == {"found": True}


async def test_probe_note_by_tag_finds_matching_setup():
    workflow = {"setups": [{"widget_id": "setup1", "title": "[EXP:Setup v001] #a1b2c3d4e5f6"}]}
    mcp = FakeMCP(workflow=workflow)
    found = await canvas_probe.probe_note_by_tag(mcp, canvas_id="c", bucket="setups", idempotency_key="a1b2c3d4e5f6xyz")
    assert found == "setup1"


async def test_probe_note_by_tag_returns_none_when_absent():
    mcp = FakeMCP(workflow={"setups": []})
    found = await canvas_probe.probe_note_by_tag(mcp, canvas_id="c", bucket="setups", idempotency_key="a1b2c3d4e5f6xyz")
    assert found is None


async def test_probe_note_by_tag_fails_closed_on_mcp_error():
    mcp = FakeMCP()
    mcp.fail_next("scan_experiment_workflow")
    with pytest.raises(RuntimeError):
        await canvas_probe.probe_note_by_tag(mcp, canvas_id="c", bucket="setups", idempotency_key="key")


async def test_probe_connector_finds_existing_outgoing_edge():
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="idea")
    mcp.seed_widget("setup1", "Note", title="[EXP:Setup v001]")
    mcp.seed_connector("idea1", "setup1")
    found = await canvas_probe.probe_connector(mcp, canvas_id="c", src_id="idea1", dst_id="setup1")
    assert found == "conn0"


async def test_probe_connector_returns_none_when_no_matching_edge():
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="idea")
    found = await canvas_probe.probe_connector(mcp, canvas_id="c", src_id="idea1", dst_id="setup1")
    assert found is None


async def test_probe_connector_returns_none_when_source_widget_missing():
    mcp = FakeMCP()
    found = await canvas_probe.probe_connector(mcp, canvas_id="c", src_id="ghost", dst_id="setup1")
    assert found is None


async def test_probe_connector_fails_closed_on_mcp_error():
    mcp = FakeMCP()
    mcp.seed_widget("idea1", "Note", text="idea")
    mcp.fail_next("check_widget_connections")
    with pytest.raises(RuntimeError):
        await canvas_probe.probe_connector(mcp, canvas_id="c", src_id="idea1", dst_id="setup1")


async def test_probe_note_by_tag_finds_matching_closed_note():
    """Closed notes are recovered the same connector-independent way as
    setup/result notes -- via their tag in the ``closeds`` bucket, not via
    the (possibly not-yet-drawn) outgoing connector from the result note."""
    workflow = {"closeds": [{"widget_id": "closed1", "title": "[EXP:Closed] after v001 #a1b2c3d4e5f6"}]}
    mcp = FakeMCP(workflow=workflow)
    found = await canvas_probe.probe_note_by_tag(mcp, canvas_id="c", bucket="closeds", idempotency_key="a1b2c3d4e5f6xyz")
    assert found == "closed1"


async def test_probe_note_by_tag_returns_none_when_no_closed_note_tagged():
    mcp = FakeMCP(workflow={"closeds": []})
    found = await canvas_probe.probe_note_by_tag(mcp, canvas_id="c", bucket="closeds", idempotency_key="a1b2c3d4e5f6xyz")
    assert found is None


# ── probe_browser_by_tag: Browser-filtered crash recovery ────────────────


async def test_probe_browser_by_tag_finds_matching_browser():
    workflow = {"setups": [{"widget_id": "b1", "widget_type": "Browser", "title": "[EXP:Setup v001] #a1b2c3d4e5f6"}]}
    mcp = FakeMCP(workflow=workflow)
    found = await canvas_probe.probe_browser_by_tag(
        mcp, canvas_id="c", bucket="setups", idempotency_key="a1b2c3d4e5f6xyz"
    )
    assert found == "b1"


async def test_probe_browser_by_tag_ignores_legacy_note_with_same_tag():
    """A legacy Note carrying the same tag must NOT be matched by the Browser
    probe -- widget_type is the discriminator so a mixed-migration canvas never
    confuses the two."""
    workflow = {"setups": [{"widget_id": "n1", "widget_type": "Note", "title": "[EXP:Setup v001] #a1b2c3d4e5f6"}]}
    mcp = FakeMCP(workflow=workflow)
    found = await canvas_probe.probe_browser_by_tag(
        mcp, canvas_id="c", bucket="setups", idempotency_key="a1b2c3d4e5f6xyz"
    )
    assert found is None


async def test_probe_browser_by_tag_returns_none_when_absent():
    mcp = FakeMCP(workflow={"setups": []})
    found = await canvas_probe.probe_browser_by_tag(
        mcp, canvas_id="c", bucket="setups", idempotency_key="a1b2c3d4e5f6xyz"
    )
    assert found is None


async def test_probe_browser_by_tag_fails_closed_on_mcp_error():
    mcp = FakeMCP()
    mcp.fail_next("scan_experiment_workflow")
    with pytest.raises(RuntimeError):
        await canvas_probe.probe_browser_by_tag(mcp, canvas_id="c", bucket="setups", idempotency_key="key")


async def test_fake_check_widget_connections_matches_production_not_found_shape():
    mcp = FakeMCP()
    raw = await mcp.call_tool("check_widget_connections", {"canvas_id": "c", "widget_id": "ghost"})
    assert json.loads(raw) == {"canvas_id": "c", "widget_id": "ghost", "found": False}


async def test_fake_check_widget_connections_reports_ragcluster_flag():
    mcp = FakeMCP()
    mcp.seed_widget("rag1", "Image", title="RAGCluster_lung")
    raw = await mcp.call_tool("check_widget_connections", {"canvas_id": "c", "widget_id": "rag1"})
    payload = json.loads(raw)
    assert payload["found"] is True
    assert payload["is_ragcluster"] is True
    assert payload["incoming_count"] == 0
    assert payload["outgoing_count"] == 0
