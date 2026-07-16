"""Dry-run inventory tests for the legacy-Note -> Browser artifact migration.

Default (no ``--apply``) runs are read-only: they inventory the legacy generated
Setup/Result/Closed *Note* widgets and make zero Browser/connector/DB writes.
Browser candidates (already migrated) and idea/human Notes are excluded. Driven
against a live-recompute :class:`FakeMCP` + a real on-disk ``StateStore``.
"""

from __future__ import annotations

import pytest

from lab_agent import artifact_migration as migration
from lab_agent.config import Settings
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP

BASE = "https://lab.test"


@pytest.fixture
def store(tmp_path):
    s = StateStore(tmp_path / "state.db")
    yield s
    s.close()


def _canvas() -> FakeMCP:
    """A canvas with legacy Note setup/result/closed, an idea, a human Note, and
    an already-migrated Browser setup -- the last three must be excluded."""
    mcp = FakeMCP(live=True)
    mcp.seed_widget("idea1", "Note", text="{idea: A+B}")
    mcp.seed_widget("human1", "Note", title="Notes", text="operator scratchpad")
    mcp.seed_widget("setup1", "Note", title="[EXP:Setup v001] A+B", text="mix A and B")
    mcp.seed_widget("robot1", "Note", title="Robot_arm")
    mcp.seed_widget("result1", "Note", title="[EXP:Result v001]", text="marker reduced")
    mcp.seed_widget("closed1", "Note", title="[EXP:Closed] after v001", text="done")
    mcp.seed_widget(
        "bmirror", "Browser", title="[EXP:Setup v002] later #deadbeef0000",
        url=f"{BASE}/artifacts/z?token=secret",
    )
    for src, dst in (("idea1", "setup1"), ("setup1", "robot1"), ("robot1", "result1"), ("result1", "closed1")):
        mcp.seed_connector(src, dst)
    return mcp


async def test_dry_run_inventories_setup_result_closed_and_writes_nothing(store):
    mcp = _canvas()
    browsers_before = {w["id"] for w in mcp.notes.values() if w["widget_type"] == "Browser"}
    connectors_before = list(mcp.connectors)

    summary = await migration.run_migration(
        mcp, store, Settings(artifact_public_base_url=BASE), canvas_id="c", apply=False
    )

    inv = {item.note_id: item for item in summary.items}
    assert set(inv) == {"setup1", "result1", "closed1"}  # the three legacy generated Notes
    assert inv["setup1"].artifact_type == "setup" and inv["setup1"].status == "inventoried"
    assert inv["result1"].artifact_type == "result"
    assert inv["closed1"].artifact_type == "closed"
    assert (inv["setup1"].inbound, inv["setup1"].outbound) == (1, 1)  # idea in, robot out
    assert inv["closed1"].outbound == 0  # terminal node has no outbound edge
    assert all(item.widget_id == "" for item in summary.items)  # no mirror widget created

    # Zero Browser/connector writes on the canvas.
    assert {w["id"] for w in mcp.notes.values() if w["widget_type"] == "Browser"} == browsers_before
    assert mcp.connectors == connectors_before
    # Zero ArtifactStore/SQLite mutations.
    assert store.conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM artifact_widgets").fetchone()[0] == 0
    assert store.conn.execute("SELECT COUNT(*) FROM side_effect_intents").fetchone()[0] == 0
    assert store.list_audit_events("c") == []


async def test_browser_idea_and_human_notes_excluded_from_inventory(store):
    mcp = _canvas()
    summary = await migration.run_migration(
        mcp, store, Settings(artifact_public_base_url=BASE), canvas_id="c", apply=False
    )
    ids = {item.note_id for item in summary.items}
    assert "bmirror" not in ids  # already-migrated Browser candidate excluded
    assert "idea1" not in ids  # user-authored idea Note excluded
    assert "human1" not in ids  # unmarked human Note excluded
    assert ids == {"setup1", "result1", "closed1"}
