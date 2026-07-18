"""Crash/restart recovery for the generated-artifact Browser write path.

A prior process's ``create_browser`` can land on the canvas before the local
mapping/intent/connector are recorded and before the ``loop:*`` attempt
completes. Recovery must converge on that *existing* Browser (found via the tag
bucket), repair its now-stale capability URL with ``update_browser``, and never
create a duplicate -- even across a second restart. Companion modules:
``test_durable_recovery_integration.py`` (legacy Note primitive) and
``test_durable_browser_error_payloads.py`` (non-raising MCP error payloads).
"""

from __future__ import annotations

from lab_agent import nodes
from lab_agent.canvas_probe import tagged_title
from lab_agent.config import Settings
from lab_agent.recovery import idempotency_key
from lab_agent.state.models import IntentStatus
from lab_agent.state_store import AttemptStatus, StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter


def _settings(**kw):
    return Settings(artifact_public_base_url="https://lab.test", **kw)  # type: ignore[call-arg]


def _browser_key(canvas_id: str, artifact_kind: str, discriminator: str) -> str:
    return idempotency_key(canvas_id, "create_browser", f"browser/{artifact_kind}/{discriminator}")


async def test_closed_browser_crash_before_connector_recovers_and_repairs_url_across_restart(tmp_path):
    """A prior process's ``create_browser`` for the terminal ``[EXP:Closed]``
    artifact landed on the canvas, but the process crashed before the local
    Browser mapping/intent were reconciled, before the result -> closed
    connector was drawn, and before the ``loop:*`` workflow_attempt was marked
    completed.

    Recovery must be connector-independent (via the ``closeds`` tag bucket),
    must not create a duplicate Browser, must repair the existing widget's
    stale token URL via ``update_browser``, reconcile to the same widget id,
    and complete the attempt exactly once even across a further restart."""
    canvas_id = "c"
    result_id, setup_id, robot_id, idea_id = "result1", "setup1", "robot1", "idea1"
    round_index = 1
    title = f"{nodes.CLOSED} after v{round_index:03d}"
    discriminator = f"closed/result:{result_id}/round:{round_index}"
    browser_key = _browser_key(canvas_id, "closed", discriminator)
    prior_title = tagged_title(title, browser_key)

    loop = {
        "loop_connector_id": "loopconn1", "setup_id": setup_id, "result_id": result_id,
        "robot_id": robot_id, "idea_id": idea_id, "ragcluster_id": "rag1", "round": round_index,
    }
    workflow = {
        "ideas_needing_setup": [], "setups_needing_run": [], "loops": [loop],
        # The ``closeds`` bucket is how ``canvas_probe.probe_browser_by_tag``
        # finds the prior process's already-landed Browser, independent of the
        # (not yet drawn) result -> closed connector.
        "closeds": [{"widget_id": "closed-prior", "widget_type": "Browser", "title": prior_title}],
    }
    mcp = FakeMCP(workflow=workflow)
    mcp.seed_widget(idea_id, "Note", text="{idea: try X}")
    mcp.seed_widget(setup_id, "Note", title="[EXP:Setup v001] try X", text="mix A and B")
    mcp.seed_widget(robot_id, "Note", title="Robot_arm")
    mcp.seed_widget(result_id, "Note", title="[EXP:Result v001]", text="marker reduced")
    # The prior (crashed) process's Browser widget: landed on the canvas
    # pointing at a now-stale capability URL, never mapped/connected locally.
    mcp.seed_widget("closed-prior", "Browser", title=prior_title, url="https://lab.test/artifacts/stale?token=old")

    adapter = ScriptedAdapter({"LoopDecision": {"proceed": False, "reason": "converged", "next_focus": ""}})
    settings = _settings()
    trigger_id = "loop:loopconn1"

    # "Restart" #1: a fresh StateStore recovers the crash.
    store1 = StateStore(tmp_path / "state.db")
    try:
        counts = await process_once(mcp, adapter, settings, store1, "rt1", canvas_id)  # type: ignore[arg-type]
        assert counts["loops"] == 1

        closed = [w for w in mcp.notes.values() if w["title"].startswith(nodes.CLOSED)]
        assert len(closed) == 1  # recovered the prior Browser, no duplicate created
        assert closed[0]["id"] == "closed-prior"
        assert closed[0]["widget_type"] == "Browser"
        # The stale capability URL was repaired to a fresh opaque-id/token URL.
        assert closed[0]["url"].startswith("https://lab.test/artifacts/")
        assert "stale" not in closed[0]["url"]

        closed_edges = [c for c in mcp.connectors if c == (result_id, "closed-prior")]
        assert len(closed_edges) == 1  # exactly one result -> closed connector

        intent = store1.get_intent(browser_key)
        assert intent is not None
        assert intent.status == IntentStatus.RECONCILED
        assert intent.external_id == "closed-prior"

        attempt = store1.get_attempt(canvas_id, trigger_id)
        assert attempt is not None
        assert attempt.status == AttemptStatus.COMPLETED
    finally:
        store1.close()

    # "Restart" #2: reopened from the same on-disk file, must not re-run the
    # already-completed attempt -- no second Browser, no second connector.
    store2 = StateStore(tmp_path / "state.db")
    try:
        counts = await process_once(mcp, adapter, settings, store2, "rt1", canvas_id)  # type: ignore[arg-type]
        assert counts["loops"] == 0  # attempt already completed -> not due, work() not re-run

        closed = [w for w in mcp.notes.values() if w["title"].startswith(nodes.CLOSED)]
        assert len(closed) == 1  # completed attempt only once: still no duplicate
        closed_edges = [c for c in mcp.connectors if c == (result_id, "closed-prior")]
        assert len(closed_edges) == 1  # still exactly one connector
    finally:
        store2.close()
