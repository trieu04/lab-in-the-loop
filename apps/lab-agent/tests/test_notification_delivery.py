"""Integration coverage for terminal notification queueing and delivery."""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from pydantic import SecretStr, ValidationError

from lab_agent.config import Settings
from lab_agent.loop_governance import close_with_reason
from lab_agent.models.governance import StopReason
from lab_agent.notification_outbox import NotificationOutbox
from lab_agent.notification_smtp import SMTPConfiguration, SMTPNotificationSink
from lab_agent.notifications import NotificationEnvelope, SendResult
from lab_agent.orchestrator_support import LoopSummary
from tests.fakes import FakeMCP


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, _env_prefix="__TEST_NO_ENV__", artifact_public_base_url="https://lab.test")


class AcceptedSink:
    """Captures envelopes at the channel boundary without SMTP transport."""

    def __init__(self) -> None:
        self.envelopes: list[NotificationEnvelope] = []

    def send(self, envelope: NotificationEnvelope) -> SendResult:
        self.envelopes.append(envelope)
        return SendResult.accepted()


class AmbiguousSink:
    def send(self, envelope: NotificationEnvelope) -> SendResult:
        del envelope
        return SendResult.ambiguous()


class CapturingSMTP:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    def ehlo(self) -> None:
        return None

    def has_extn(self, name: str) -> bool:
        return name == "starttls"

    def starttls(self, *, context: object) -> None:
        del context

    def login(self, user: str, password: str) -> None:
        del user, password

    def send_message(self, message: EmailMessage) -> dict[str, tuple[int, bytes]]:
        self.messages.append(message)
        return {}

    def quit(self) -> None:
        return None


def _smtp() -> SMTPConfiguration:
    return SMTPConfiguration(
        enabled=True,
        host="smtp.example.test",
        port=587,
        username="mailer",
        password=SecretStr("secret"),
        sender="lab-agent@example.test",
        recipients=("operator@example.test",),
        sender_allowlist=("lab-agent@example.test",),
        recipient_allowlist=("operator@example.test",),
    )


async def test_terminal_closure_queues_once_then_drains_after_closure(store, settings, monkeypatch) -> None:
    settings.notification_smtp = _smtp()
    sink = AcceptedSink()
    monkeypatch.setattr("lab_agent.notification_outbox.SMTPNotificationSink", lambda _: sink)
    summary = LoopSummary(rounds=1, result_ids=["result-1"])
    mcp = FakeMCP()

    first = await close_with_reason(
        mcp, settings, store, summary, canvas_id="canvas", trigger_id="loop:1",
        result_id="result-1", round_index=1, reason=StopReason.MAX_ROUNDS,
    )
    second = await close_with_reason(
        mcp, settings, store, summary, canvas_id="canvas", trigger_id="loop:1",
        result_id="result-1", round_index=1, reason=StopReason.MAX_ROUNDS,
    )

    queued = store.list_notification_records()
    assert first == second
    assert len(queued) == 1
    assert sink.envelopes == []
    counts = NotificationOutbox(store, settings.notification_smtp, "worker").drain()
    assert counts.sent == 1
    assert len(sink.envelopes) == 1
    assert sink.envelopes[0].closure_id == first.closure_id
    assert store.list_notification_records()[0].status.value == "sent"


async def test_disabled_notifications_do_not_create_outbox_row(store, settings) -> None:
    summary = LoopSummary(rounds=1, result_ids=["result-1"])

    await close_with_reason(
        FakeMCP(), settings, store, summary, canvas_id="canvas", trigger_id="loop:1",
        result_id="result-1", round_index=1, reason=StopReason.MAX_ROUNDS,
    )

    assert store.list_notification_records() == []


def test_notification_outbox_does_not_open_smtp_while_disabled(store) -> None:
    outbox = NotificationOutbox(store, SMTPConfiguration(), "worker")

    assert outbox.drain().total == 0


def test_notification_drain_is_bounded_per_watcher_cycle(store, monkeypatch) -> None:
    for index in range(2):
        store.enqueue_notification(NotificationEnvelope(
            canvas_id="canvas", trigger_id=f"loop:{index}", closure_id=f"closed-{index}",
            round_index=index + 1, reason="max_rounds",
        ))
    sink = AcceptedSink()
    outbox = NotificationOutbox(store, _smtp(), "worker", batch_size=1, sink=sink)
    monkeypatch.setattr(
        store, "list_notification_records", lambda **_: pytest.fail("unbounded queue read")
    )

    assert outbox.drain().sent == 1
    assert len(sink.envelopes) == 1
    assert outbox.drain().sent == 1
    assert len(sink.envelopes) == 2


def test_enabled_smtp_config_fails_at_settings_boundary() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, notification_smtp={"enabled": True})


@pytest.mark.parametrize(
    "metadata", [
        '{"trigger_id":"loop:1","closure_id":"closed","reason":"max_rounds"}',
        '{"trigger_id":"loop:1","closure_id":"closed","round_index":"1","reason":"max_rounds"}',
    ],
)
def test_invalid_persisted_metadata_quarantines_without_sending(store, metadata: str) -> None:
    envelope = NotificationEnvelope(
        canvas_id="canvas", trigger_id="loop:1", closure_id="closed", round_index=1, reason="max_rounds"
    )
    record = store.enqueue_notification(envelope)
    store.conn.execute("UPDATE notification_outbox SET closure_metadata_json=? WHERE logical_key=?", (metadata, record.logical_key))
    sink = AcceptedSink()

    counts = NotificationOutbox(store, _smtp(), "worker", sink=sink).drain()

    assert counts.quarantined == 1
    assert sink.envelopes == []
    assert store.get_notification(record.logical_key).failure_category.value == "idempotency_conflict"


def test_ambiguous_delivery_uses_configured_reconciliation_window(store, clock) -> None:
    envelope = NotificationEnvelope(
        canvas_id="canvas", trigger_id="loop:1", closure_id="closed", round_index=1, reason="max_rounds"
    )
    record = store.enqueue_notification(envelope)

    assert NotificationOutbox(store, _smtp(), "worker", reconciliation_seconds=90, sink=AmbiguousSink()).drain().ambiguous == 1
    assert store.get_notification(record.logical_key).reconciliation_deadline == clock() + 90


def test_stored_message_id_matches_outbound_smtp_header(store) -> None:
    envelope = NotificationEnvelope(
        canvas_id="canvas", trigger_id="loop:1", closure_id="closed", round_index=1, reason="max_rounds"
    )
    record = store.enqueue_notification(envelope)
    assert record.message_id == envelope.message_id
    client = CapturingSMTP()
    sink = SMTPNotificationSink(_smtp(), smtp_factory=lambda *_args, **_kwargs: client)

    assert NotificationOutbox(store, _smtp(), "worker", sink=sink).drain().sent == 1
    assert client.messages[0]["Message-ID"] == record.message_id
