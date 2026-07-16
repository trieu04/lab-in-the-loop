"""Contract tests for metadata-only SMTP terminal notifications."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage

import pytest
from pydantic import SecretStr, ValidationError

from lab_agent.notification_smtp import (
    SMTPConfiguration,
    SMTPNotificationSink,
    SMTPTransportSecurity,
)
from lab_agent.notifications import (
    NotificationEnvelope,
    NotificationFailureCategory,
    SendDisposition,
)


class FakeSMTP:
    def __init__(self, *, starttls: bool = True, send_error: Exception | None = None) -> None:
        self.starttls_available = starttls
        self.send_error = send_error
        self.messages: list[EmailMessage] = []
        self.started_tls = False
        self.quit_called = False

    def ehlo(self) -> None:
        return None

    def has_extn(self, name: str) -> bool:
        return name.lower() == "starttls" and self.starttls_available

    def starttls(self, *, context: object) -> None:
        assert context is not None
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        assert username == "mailer"
        assert password == "smtp-secret"

    def send_message(self, message: EmailMessage) -> dict[str, tuple[int, bytes]]:
        if self.send_error is not None:
            raise self.send_error
        self.messages.append(message)
        return {}

    def quit(self) -> None:
        self.quit_called = True


def _configuration(**changes: object) -> SMTPConfiguration:
    values: dict[str, object] = {
        "enabled": True,
        "host": "smtp.example.test",
        "port": 587,
        "security": SMTPTransportSecurity.STARTTLS,
        "username": "mailer",
        "password": SecretStr("smtp-secret"),
        "sender": "lab-agent@example.test",
        "recipients": ("operator@example.test",),
        "sender_allowlist": ("lab-agent@example.test",),
        "recipient_allowlist": ("operator@example.test",),
    }
    values.update(changes)
    return SMTPConfiguration.model_validate(values)


def _envelope(**changes: object) -> NotificationEnvelope:
    values: dict[str, object] = {
        "canvas_id": "canvas-1",
        "trigger_id": "loop:result-1",
        "closure_id": "closed-1",
        "round_index": 2,
        "reason": "max_rounds",
    }
    values.update(changes)
    return NotificationEnvelope.model_validate(values)


def _sink(client: FakeSMTP, configuration: SMTPConfiguration | None = None) -> SMTPNotificationSink:
    def factory(host: str, port: int, *, timeout: float) -> FakeSMTP:
        del host, port, timeout
        return client

    def ssl_factory(host: str, port: int, *, timeout: float, context: object) -> FakeSMTP:
        del host, port, timeout, context
        return client

    return SMTPNotificationSink(configuration or _configuration(), smtp_factory=factory, smtp_ssl_factory=ssl_factory)


def test_envelope_key_and_message_id_are_deterministic_and_content_free() -> None:
    first = _envelope()
    second = _envelope()

    assert first.logical_key == second.logical_key
    assert first.message_id == second.message_id
    assert first.logical_key != _envelope(reason="no_progress").logical_key
    assert first.message_id == f"<{first.logical_key}@lab-agent.local>"
    assert "body" not in NotificationEnvelope.model_fields


def test_smtp_message_is_metadata_only_and_secret_repr_is_redacted() -> None:
    client = FakeSMTP()
    configuration = _configuration()
    result = _sink(client, configuration).send(_envelope())

    assert result.disposition is SendDisposition.ACCEPTED
    assert "smtp-secret" not in repr(configuration)
    rendered = client.messages[0].as_string()
    assert "smtp-secret" not in rendered
    assert "Lab Agent terminal closure notification" in rendered
    assert "Message-ID: <" in rendered


def test_invalid_addresses_and_metadata_newlines_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _configuration(recipients=("operator@example.test\r\nBcc: victim@example.test",))
    with pytest.raises(ValidationError):
        _envelope(trigger_id="loop\r\nBcc: victim@example.test")


def test_disabled_smtp_never_opens_a_connection() -> None:
    client = FakeSMTP()
    configuration = _configuration(enabled=False)

    result = _sink(client, configuration).send(_envelope())

    assert result.disposition is SendDisposition.DISABLED
    assert client.messages == []


def test_starttls_is_required_and_verified_before_delivery() -> None:
    client = FakeSMTP(starttls=False)

    result = _sink(client).send(_envelope())

    assert result.disposition is SendDisposition.REJECTED
    assert result.failure_category is NotificationFailureCategory.SMTP_REJECTED
    assert client.messages == []


@pytest.mark.parametrize(
    ("error", "disposition", "category"),
    [
        (smtplib.SMTPConnectError(421, "offline"), SendDisposition.TRANSIENT, NotificationFailureCategory.SMTP_TRANSIENT),
        (smtplib.SMTPRecipientsRefused({"operator@example.test": (550, b"denied")}), SendDisposition.REJECTED, NotificationFailureCategory.SMTP_REJECTED),
        (smtplib.SMTPServerDisconnected("lost after DATA"), SendDisposition.AMBIGUOUS, NotificationFailureCategory.SMTP_AMBIGUOUS),
    ],
)
def test_smtp_failures_map_to_sanitized_fixed_categories(
    error: Exception, disposition: SendDisposition, category: NotificationFailureCategory
) -> None:
    result = _sink(FakeSMTP(send_error=error)).send(_envelope())

    assert result.disposition is disposition
    assert result.failure_category is category
    assert "offline" not in repr(result)
    assert "denied" not in repr(result)
