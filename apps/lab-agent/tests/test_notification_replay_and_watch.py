"""Watcher and replay regressions for terminal notification delivery."""

from __future__ import annotations

from pydantic import SecretStr

from lab_agent import loop_governance
from lab_agent.config import Settings
from lab_agent.loop_governance import close_with_reason
from lab_agent.model_gateway import GovernedAdapter, build_context
from lab_agent.models.governance import StopReason
from lab_agent.notification_outbox import NotificationOutbox
from lab_agent.notification_smtp import SMTPConfiguration
from lab_agent.notifications import NotificationEnvelope, SendResult
from lab_agent.orchestrator import run_loop
from lab_agent.orchestrator_support import LoopSummary
from lab_agent.state import notification_outbox as notification_state
from lab_agent.state_store import StateStore
from lab_agent.watch import process_once
from tests.fakes import FakeMCP, ScriptedAdapter


class AcceptedSink:
    def __init__(self) -> None:
        self.envelopes: list[NotificationEnvelope] = []

    def send(self, envelope: NotificationEnvelope) -> SendResult:
        self.envelopes.append(envelope)
        return SendResult.accepted()


class LeaseCheckingSink(AcceptedSink):
    def __init__(self, store: StateStore) -> None:
        super().__init__()
        self.store = store

    def send(self, envelope: NotificationEnvelope) -> SendResult:
        assert self.store.get_canvas_lease(envelope.canvas_id) is None
        return super().send(envelope)


def _smtp() -> SMTPConfiguration:
    return SMTPConfiguration(
        enabled=True, host="smtp.example.test", port=587, username="mailer",
        password=SecretStr("secret"), sender="lab-agent@example.test",
        recipients=("operator@example.test",), sender_allowlist=("lab-agent@example.test",),
        recipient_allowlist=("operator@example.test",),
    )


def _settings() -> Settings:
    return Settings(
        artifact_public_base_url="https://lab.test", loop_min_rounds=1, notification_smtp=_smtp(),
        pricing_version="test", model_pricing={"gpt-4o-mini": {"input_per_1k": 1.0, "output_per_1k": 1.0}},
    )


LOOP = {"loop_connector_id": "loop-1", "setup_id": "setup-1", "result_id": "result-1", "round": 1}
SEED = {"setup-1": "mix safely", "result-1": "signal measured"}


async def test_watcher_runs_loop_before_draining_accepted_notification(tmp_path) -> None:
    store = StateStore(tmp_path / "state.db")
    try:
        sink = LeaseCheckingSink(store)
        settings = _settings()
        mcp = FakeMCP(note_text=SEED, workflow={"loops": [LOOP]})
        outbox = NotificationOutbox(store, settings.notification_smtp, "notifier", sink=sink)
        inner = ScriptedAdapter({"LoopDecision": {"proceed": False, "reason": "complete"}})
        gov = build_context(store, settings, "canvas", {"openai": inner}, [{"classification": "public"}])

        counts = await process_once(mcp, GovernedAdapter(gov), settings, store, "watcher", "canvas", gov=gov, notifications=outbox)

        assert counts["loops"] == 1
        assert len(sink.envelopes) == 1
        assert store.list_notification_records()[0].status.value == "sent"
    finally:
        store.close()


async def test_terminal_replay_retries_enqueue_after_prior_enqueue_failure(tmp_path, monkeypatch) -> None:
    store = StateStore(tmp_path / "state.db")
    try:
        settings = _settings()
        mcp = FakeMCP(note_text=SEED)
        event = await close_with_reason(
            mcp, settings, store, LoopSummary(rounds=1, result_ids=["result-1"]),
            canvas_id="canvas", trigger_id="loop:loop-1", result_id="result-1",
            round_index=1, reason=StopReason.MAX_ROUNDS,
        )
        store.conn.execute("DELETE FROM notification_outbox")
        assert event.closure_id

        await run_loop(mcp, ScriptedAdapter({}), settings, store, canvas_id="canvas", loop=LOOP)

        records = store.list_notification_records()
        assert len(records) == 1
        assert records[0].closure_metadata["closure_id"] == event.closure_id
    finally:
        store.close()


async def test_watcher_retries_terminal_enqueue_without_reopening_closure(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "state.db"

    def clock() -> float:
        return 100.0

    settings = _settings()
    mcp = FakeMCP(note_text=SEED, workflow={"loops": [LOOP]})
    first_adapter = ScriptedAdapter({"LoopDecision": {"proceed": False, "reason": "complete"}})
    first = StateStore(path, clock=clock, rng=lambda: 0.0)
    original_enqueue = loop_governance.enqueue_terminal_notification
    try:
        gov = build_context(
            first, settings, "canvas", {"openai": first_adapter}, [{"classification": "public"}]
        )
        monkeypatch.setattr(loop_governance, "enqueue_terminal_notification", lambda *_: False)

        counts = await process_once(
            mcp, GovernedAdapter(gov), settings, first, "watcher-1", "canvas", gov=gov
        )

        attempt = first.get_attempt("canvas", "loop:loop-1:round:1")
        assert counts["loops"] == 0
        assert attempt is not None and attempt.status.value == "failed"
        assert first.list_notification_records() == []
        assert sum(audit.event == "loop_stopped" for audit in first.list_audit_events("canvas")) == 1
        model_call_count = sum(
            audit.event == "model_call" for audit in first.list_audit_events("canvas")
        )
        widget_count, connector_count = len(mcp.notes), len(mcp.connectors)
        first.release_canvas_lease("canvas", runtime_instance_id="watcher-1")
    finally:
        first.close()
        monkeypatch.setattr(loop_governance, "enqueue_terminal_notification", original_enqueue)

    restarted = StateStore(path, clock=clock, rng=lambda: 0.0)
    try:
        sink = AcceptedSink()
        replay_adapter = ScriptedAdapter({})
        gov = build_context(
            restarted, settings, "canvas", {"openai": replay_adapter},
            [{"classification": "public"}],
        )
        outbox = NotificationOutbox(restarted, settings.notification_smtp, "notifier", sink=sink)

        counts = await process_once(
            mcp, GovernedAdapter(gov), settings, restarted, "watcher-2", "canvas",
            gov=gov, notifications=outbox,
        )

        records = restarted.list_notification_records()
        attempt = restarted.get_attempt("canvas", "loop:loop-1:round:1")
        assert counts["loops"] == 1
        assert attempt is not None and attempt.status.value == "completed"
        assert len(records) == 1 and records[0].status.value == "sent"
        assert len(sink.envelopes) == 1
        assert replay_adapter.schema_calls == []
        assert (len(mcp.notes), len(mcp.connectors)) == (widget_count, connector_count)
        audits = restarted.list_audit_events("canvas")
        assert sum(audit.event == "model_call" for audit in audits) == model_call_count
        assert sum(audit.event == "loop_stopped" for audit in audits) == 1
    finally:
        restarted.close()


def test_message_id_is_persisted_atomically_before_a_restart(tmp_path) -> None:
    path = tmp_path / "state.db"
    envelope = NotificationEnvelope(canvas_id="canvas", trigger_id="loop:1", closure_id="closed", round_index=1, reason="max_rounds")
    first = StateStore(path)
    try:
        record = notification_state.enqueue(
            first.conn, clock=first.clock, logical_key=envelope.logical_key, canvas_id=envelope.canvas_id,
            closure_metadata={"trigger_id": envelope.trigger_id, "closure_id": envelope.closure_id, "round_index": envelope.round_index, "reason": envelope.reason}, message_id=envelope.message_id,
        )
        assert record.message_id == envelope.message_id
    finally:
        first.close()
    restarted = StateStore(path)
    try:
        record = restarted.get_notification(envelope.logical_key)
        assert record is not None and record.message_id == envelope.message_id
    finally:
        restarted.close()
