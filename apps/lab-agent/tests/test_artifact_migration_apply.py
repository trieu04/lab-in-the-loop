"""Apply-path tests for the legacy-Note -> Browser artifact migration.

``--apply`` mirrors each legacy generated Note into a capability-protected
Browser artifact plus equivalent connectors, leaving the original Note and its
connectors untouched, and converges on one artifact/Browser/connector each when
rerun (repairing a stale capability URL rather than duplicating it). Driven
against a live-recompute :class:`FakeMCP` + a real on-disk ``StateStore``.
"""

from __future__ import annotations

import pytest

from lab_agent import artifact_migration as migration
from lab_agent.artifact_store import ArtifactStore
from lab_agent.config import Settings
from lab_agent.models.artifact import ArtifactType
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP

BASE = "https://lab.test"


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


def _canvas() -> FakeMCP:
    """One legacy setup Note with an inbound idea and an outbound robot."""
    mcp = FakeMCP(live=True)
    mcp.seed_widget("idea1", "Note", text="{idea: A+B}")
    mcp.seed_widget("setup1", "Note", title="[EXP:Setup v001] A+B", text="mix A and B thoroughly")
    mcp.seed_widget("robot1", "Note", title="Robot_arm")
    mcp.seed_connector("idea1", "setup1")
    mcp.seed_connector("setup1", "robot1")
    return mcp


async def test_apply_mirrors_structured_payload_and_leaves_original_note(store):
    mcp = _canvas()
    summary = await migration.run_migration(
        mcp, store, Settings(artifact_public_base_url=BASE), canvas_id="c", apply=True
    )

    assert summary.failed == 0
    item = summary.items[0]
    assert item.note_id == "setup1" and item.status == "mirrored"

    # The original Note is untouched (mirror-first, no deletion/edit).
    assert mcp.notes["setup1"]["widget_type"] == "Note"
    assert mcp.notes["setup1"]["text"] == "mix A and B thoroughly"

    # A Browser mirror carrying the exact legacy marker title.
    browser = mcp.notes[item.widget_id]
    assert browser["widget_type"] == "Browser"
    assert browser["title"].startswith("[EXP:Setup v001] A+B")

    # Canonical structured payload + truthful import provenance.
    doc = ArtifactStore(store.conn).get_artifact_by_widget(canvas_id="c", widget_id=item.widget_id)
    assert doc is not None and doc.artifact_type == ArtifactType.SETUP
    assert doc.payload["source_note_id"] == "setup1"
    assert doc.payload["text"] == "mix A and B thoroughly"  # exact legacy text
    assert doc.payload["title"].startswith("[EXP:Setup v001] A+B")
    assert doc.payload["artifact_type"] == "setup"
    assert doc.payload["round"] == 1  # parsed from the vNNN marker
    assert doc.provenance.provider == "migration"
    assert doc.provenance.trigger_id == "migrate:setup1"
    assert doc.provenance.source_widget_id == "setup1"


async def test_inbound_and_outbound_connectors_cloned_once_originals_unchanged(store):
    mcp = _canvas()
    summary = await migration.run_migration(
        mcp, store, Settings(artifact_public_base_url=BASE), canvas_id="c", apply=True
    )
    bid = summary.items[0].widget_id

    # Originals intact.
    assert ("idea1", "setup1") in mcp.connectors
    assert ("setup1", "robot1") in mcp.connectors
    # Equivalent source->Browser and Browser->dest, each drawn exactly once.
    assert mcp.connectors.count(("idea1", bid)) == 1
    assert mcp.connectors.count((bid, "robot1")) == 1
    assert summary.items[0].connectors_mirrored == 2
    # No self-loop and no note<->mirror edge invented.
    assert (bid, bid) not in mcp.connectors
    assert ("setup1", bid) not in mcp.connectors and (bid, "setup1") not in mcp.connectors


async def test_mixed_canvas_preserves_browser_neighbor_edge(store):
    mcp = FakeMCP(live=True)
    mcp.seed_widget(
        "result-browser",
        "Browser",
        title="[EXP:Result v001]",
        url=f"{BASE}/artifacts/existing?token=secret",
    )
    mcp.seed_widget("closed-note", "Note", title="[EXP:Closed] after v001", text="done")
    mcp.seed_connector("result-browser", "closed-note")

    summary = await migration.run_migration(
        mcp, store, Settings(artifact_public_base_url=BASE), canvas_id="c", apply=True
    )

    assert summary.failed == 0
    closed_mirror = summary.items[0].widget_id
    assert ("result-browser", "closed-note") in mcp.connectors
    assert ("result-browser", closed_mirror) in mcp.connectors


async def test_full_chain_rerun_builds_mirror_path_without_connector_growth(store):
    mcp = FakeMCP(live=True)
    mcp.seed_widget("idea", "Note", text="{idea: A+B}")
    mcp.seed_widget("setup", "Note", title="[EXP:Setup v001]", text="mix")
    mcp.seed_widget("robot", "Note", title="Robot_arm")
    mcp.seed_widget("result", "Note", title="[EXP:Result v001]", text="reduced")
    mcp.seed_widget("closed", "Note", title="[EXP:Closed] after v001", text="done")
    for edge in (("idea", "setup"), ("setup", "robot"), ("robot", "result"), ("result", "closed")):
        mcp.seed_connector(*edge)
    settings = Settings(artifact_public_base_url=BASE)

    await migration.run_migration(mcp, store, settings, canvas_id="c", apply=True)
    first_count = len(mcp.connectors)
    mirrors = {
        item["title"].split()[0]: item["id"]
        for item in mcp.notes.values()
        if item["widget_type"] == "Browser"
    }
    assert (mirrors["[EXP:Setup"], "robot") in mcp.connectors
    assert ("robot", mirrors["[EXP:Result"]) in mcp.connectors
    assert (mirrors["[EXP:Result"], mirrors["[EXP:Closed]"]) in mcp.connectors

    await migration.run_migration(mcp, store, settings, canvas_id="c", apply=True)
    assert len(mcp.connectors) == first_count


async def test_second_apply_converges_and_repairs_token_url(store):
    mcp = _canvas()
    settings = Settings(artifact_public_base_url=BASE)
    await migration.run_migration(mcp, store, settings, canvas_id="c", apply=True)

    bid = next(w["id"] for w in mcp.notes.values() if w["widget_type"] == "Browser")
    url1 = mcp.notes[bid]["url"]
    connectors1 = len(mcp.connectors)

    await migration.run_migration(mcp, store, settings, canvas_id="c", apply=True)

    browsers = [w for w in mcp.notes.values() if w["widget_type"] == "Browser"]
    assert len(browsers) == 1 and browsers[0]["id"] == bid  # one mirror, repaired in place
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
    assert len(mcp.connectors) == connectors1  # no duplicate connectors
    url2 = mcp.notes[bid]["url"]
    assert url2 != url1  # capability URL repaired with a fresh token
    assert url2.startswith(f"{BASE}/artifacts/") and "?token=" in url2
