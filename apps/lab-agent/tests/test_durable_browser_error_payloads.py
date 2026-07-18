"""Fail-closed on a non-raising MCP error payload in the Browser write path.

``MCPClient.call_tool`` does not raise on a tool-level failure -- it returns a
normal ``{"error": ...}`` JSON string (see ``mcp_client.py``). These prove the
generated-artifact Browser path fails closed on that shape end to end: never a
phantom Browser/connector, the intent recorded ``failed`` (never ``reconciled``
with an empty id), the attempt ``failed`` (never ``completed``), and the next
due retry (zero jitter -> immediately due) succeeds exactly once, reusing the
canonical artifact persisted before the Browser mutation.
"""

from __future__ import annotations

from lab_agent.config import Settings
from lab_agent.recovery import idempotency_key
from lab_agent.state.models import IntentStatus
from lab_agent.state_store import AttemptStatus, StateStore
from lab_agent.watch import process_once
from tests.conftest import IDEA_WORKFLOW, SETUP
from tests.fakes import FakeMCP, ScriptedAdapter


def _settings(**kw):
    return Settings(artifact_public_base_url="https://lab.test", **kw)  # type: ignore[call-arg]


def _browser_key(canvas_id: str, artifact_kind: str, discriminator: str) -> str:
    return idempotency_key(canvas_id, "create_browser", f"browser/{artifact_kind}/{discriminator}")


async def test_process_once_retries_after_setup_browser_error_payload_without_duplicate(tmp_path):
    """A ``create_browser`` error payload while writing the setup artifact must
    fail the intent and the attempt -- never a phantom Browser -- and the next
    due retry must succeed exactly once, reusing the canonical artifact
    persisted first so no second artifact and no second Browser are created."""
    store = StateStore(tmp_path / "state.db", rng=lambda: 0.0)  # zero jitter -> immediately due retry
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    settings = _settings()
    canvas_id = "c"
    trigger_id = "idea_setup:idea1"
    key = _browser_key(canvas_id, "setup", "setup/predecessor:idea1/round:1")
    try:
        mcp.fail_next_as_error_payload("create_browser")

        first = await process_once(mcp, adapter, settings, store, "rt1", canvas_id)  # type: ignore[arg-type]
        assert first["setups"] == 0  # failed, not counted as done

        setups = [w for w in mcp.notes.values() if w["widget_type"] == "Browser"]
        assert setups == []  # no phantom Browser widget

        intent = store.get_intent(key)
        assert intent is not None
        assert intent.status == IntentStatus.FAILED  # never reconciled with an empty/phantom id
        assert not intent.external_id

        attempt = store.get_attempt(canvas_id, trigger_id)
        assert attempt is not None
        assert attempt.status == AttemptStatus.FAILED  # not completed

        # Next due retry succeeds exactly once, no duplicate.
        second = await process_once(mcp, adapter, settings, store, "rt1", canvas_id)  # type: ignore[arg-type]
        assert second["setups"] == 1

        setups = [
            w for w in mcp.notes.values()
            if w["widget_type"] == "Browser" and w["title"].startswith("[EXP:Setup")
        ]
        assert len(setups) == 1  # exactly one, no duplicate
        assert setups[0]["url"].startswith("https://lab.test/artifacts/")  # capability URL

        intent = store.get_intent(key)
        assert intent is not None
        assert intent.status == IntentStatus.RECONCILED
        assert intent.external_id == setups[0]["id"]

        attempt = store.get_attempt(canvas_id, trigger_id)
        assert attempt is not None
        assert attempt.status == AttemptStatus.COMPLETED
    finally:
        store.close()


async def test_process_once_retries_after_setup_connector_error_payload_without_duplicate(tmp_path):
    """Same non-raising failure shape, but on ``create_connector`` for the
    idea -> setup edge: the setup Browser itself already landed
    (``create_browser`` succeeded first) and was mapped, but the connector's
    error payload must still fail the attempt as a whole -- no phantom
    connector, no completed attempt -- and the next due retry must reuse the
    already-mapped Browser (repaired in place via ``update_browser``, no second
    ``create_browser``) and draw the connector exactly once."""
    store = StateStore(tmp_path / "state.db", rng=lambda: 0.0)
    mcp = FakeMCP(note_text={"idea1": "{idea: try X}"}, workflow=IDEA_WORKFLOW)
    adapter = ScriptedAdapter({"ExperimentSetup": SETUP})
    settings = _settings()
    canvas_id = "c"
    trigger_id = "idea_setup:idea1"
    setup_id = "browser1"  # FakeMCP's first synthetic create_browser id
    browser_key = _browser_key(canvas_id, "setup", "setup/predecessor:idea1/round:1")
    conn_key = idempotency_key(canvas_id, "create_connector", f"connector/idea_setup/idea1->{setup_id}")
    try:
        mcp.fail_next_as_error_payload("create_connector")

        first = await process_once(mcp, adapter, settings, store, "rt1", canvas_id)  # type: ignore[arg-type]
        assert first["setups"] == 0  # attempt failed overall, not counted as done

        setups = [
            w for w in mcp.notes.values()
            if w["widget_type"] == "Browser" and w["title"].startswith("[EXP:Setup")
        ]
        assert len(setups) == 1  # the Browser write itself succeeded independently
        assert setups[0]["id"] == setup_id
        assert mcp.connectors == []  # no phantom edge

        browser_intent = store.get_intent(browser_key)
        assert browser_intent is not None
        assert browser_intent.status == IntentStatus.RECONCILED
        assert browser_intent.external_id == setup_id

        conn_intent = store.get_intent(conn_key)
        assert conn_intent is not None
        assert conn_intent.status == IntentStatus.FAILED  # never reconciled with an empty/phantom id
        assert not conn_intent.external_id

        attempt = store.get_attempt(canvas_id, trigger_id)
        assert attempt is not None
        assert attempt.status == AttemptStatus.FAILED  # connector step still pending -> not completed

        # Next due retry: the Browser is already mapped (repaired in place, no
        # second create_browser call), only the connector is (re)attempted.
        second = await process_once(mcp, adapter, settings, store, "rt1", canvas_id)  # type: ignore[arg-type]
        assert second["setups"] == 1

        setups = [
            w for w in mcp.notes.values()
            if w["widget_type"] == "Browser" and w["title"].startswith("[EXP:Setup")
        ]
        assert len(setups) == 1  # still no duplicate Browser
        assert mcp.connectors == [("idea1", setup_id)]  # exactly one connector, no duplicate

        conn_intent = store.get_intent(conn_key)
        assert conn_intent is not None
        assert conn_intent.status == IntentStatus.RECONCILED
        assert conn_intent.external_id

        attempt = store.get_attempt(canvas_id, trigger_id)
        assert attempt is not None
        assert attempt.status == AttemptStatus.COMPLETED
    finally:
        store.close()
