"""Crash/restart/recovery boundary tests for the legacy Note durable primitive.

``durable_writes.write_setup_node_durable``/``connect_durable`` remain the
preserved Note-creation path for legacy/mixed-migration canvases (generated
artifacts are now Browser widgets -- see ``test_durable_browser_recovery.py`` and
``test_durable_browser_error_payloads.py``). These exercise the Note primitive
directly against a real on-disk ``StateStore`` and ``FakeMCP``: a "restart" is
the canvas persisting untouched while the local ledger is re-opened. Every test
targets a point in the intent-before-write timeline (execute failed / write
landed but local reconcile didn't) and asserts recovery never duplicates a note
or connector, and never records a phantom-successful attempt.

Companion module ``test_durable_operations_integration.py`` covers the
operational surface (leases, quarantine/reset, backup/restore, audit chain).
"""

from __future__ import annotations

from lab_agent import durable_writes
from lab_agent.canvas_probe import tagged_title
from lab_agent.intent_audit import connect_durable
from lab_agent.recovery import idempotency_key
from lab_agent.state.models import IntentStatus
from lab_agent.state_store import StateStore
from tests.fakes import FakeMCP

# ── Boundary: crash before the canvas write lands (execute() itself fails) ──


async def test_retry_after_execute_failure_does_not_duplicate_the_note(tmp_path):
    """``fail_next`` simulates a crash mid ``create_note`` call: the write
    must be retriable and, once it succeeds, must not have created two notes."""
    store = StateStore(tmp_path / "state.db")
    mcp = FakeMCP()
    try:
        mcp.seed_widget("idea1", "Note", text="idea")
        mcp.fail_next("create_note")

        try:
            await durable_writes.write_setup_node_durable(
                mcp, store, canvas_id="c", title="[EXP:Setup v001] idea", body="Round: 1\nmix",
                round_index=1, predecessor_id="idea1", edge_kind="idea_setup",
            )
            raise AssertionError("expected the simulated create_note failure to propagate")
        except RuntimeError:
            pass
        assert mcp.notes.get("idea1") is not None and len(mcp.notes) == 1  # nothing else created

        setup_id = await durable_writes.write_setup_node_durable(
            mcp, store, canvas_id="c", title="[EXP:Setup v001] idea", body="Round: 1\nmix",
            round_index=1, predecessor_id="idea1", edge_kind="idea_setup",
        )
        assert setup_id
        setup_notes = [w for w in mcp.notes.values() if w["title"].startswith("[EXP:Setup")]
        assert len(setup_notes) == 1  # retry recovered cleanly, no duplicate
    finally:
        store.close()


# ── Boundary: crash after the canvas write lands, before local reconcile ────


async def test_restart_after_note_write_before_reconcile_recovers_without_duplicate(tmp_path):
    """A prior process's ``create_note`` landed on the canvas but the process
    died before marking the local intent reconciled (no local intent row at
    all, in the worst case). The next attempt must find the tagged widget via
    the live probe and reuse its id, never create a second note."""
    store = StateStore(tmp_path / "state.db")
    title = "[EXP:Setup v001] idea"
    body = "Round: 1\nmix"
    key = idempotency_key("c", "create_note_setup", "setup/predecessor:idea1/round:1")
    prior_title = tagged_title(title, key)
    # ``probe_note_by_tag`` reads the ``setups`` bucket from
    # ``scan_experiment_workflow`` -- a static fixture here, matching how
    # ``tests/test_canvas_probe.py`` exercises the same probe, and avoiding a
    # dependency on the real detector's bucket-classification rules (which
    # this test is not about).
    mcp = FakeMCP(workflow={"setups": [{"widget_id": "setup-prior", "title": prior_title}]})
    try:
        mcp.seed_widget("idea1", "Note", text="idea")
        mcp.seed_widget("setup-prior", "Note", title=prior_title, text=body)

        setup_id = await durable_writes.write_setup_node_durable(
            mcp, store, canvas_id="c", title=title, body=body,
            round_index=1, predecessor_id="idea1", edge_kind="idea_setup",
        )

        assert setup_id == "setup-prior"  # recovered the pre-existing widget
        setup_notes = [w for w in mcp.notes.values() if w["title"].startswith("[EXP:Setup")]
        assert len(setup_notes) == 1  # no duplicate created
        intent = store.get_intent(key)
        assert intent is not None
        assert intent.status == IntentStatus.RECONCILED
        assert intent.external_id == "setup-prior"
    finally:
        store.close()


async def test_restart_after_connector_write_before_reconcile_recovers_without_duplicate(tmp_path):
    """Same boundary as above, for a connector: it already exists on the
    canvas (drawn by a prior, crashed process); recovery must reuse it."""
    store = StateStore(tmp_path / "state.db")
    mcp = FakeMCP()
    try:
        mcp.seed_widget("idea1", "Note", text="idea")
        mcp.seed_widget("setup1", "Note", title="[EXP:Setup v001]")
        mcp.seed_connector("idea1", "setup1")  # prior process's edge

        connector_id = await connect_durable(
            mcp, store, canvas_id="c", src_id="idea1", dst_id="setup1", edge_kind="idea_setup", round_index=1,
        )

        assert connector_id == "conn0"  # recovered the existing connector id
        assert mcp.connectors == [("idea1", "setup1")]  # no duplicate connector drawn
        edge = store.get_edge("c", connector_id)
        assert edge is not None
        assert edge.kind == "idea_setup"
    finally:
        store.close()
