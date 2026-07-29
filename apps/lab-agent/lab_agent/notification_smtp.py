"""SMTP notification sink with strict transport and address safety boundaries."""

from __future__ import annotations

import re
import smtplib
import socket
import ssl
from contextlib import suppress
from email.message import EmailMessage
from email.utils import parseaddr
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from lab_agent.notifications import NotificationEnvelope, SendResult

_ADDRESS = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$")


class SMTPTransportSecurity(StrEnum):
    """Only encrypted SMTP transports are permitted."""

    IMPLICIT_TLS = "implicit_tls"
    STARTTLS = "starttls"


class SMTPClient(Protocol):
    """Small stdlib SMTP surface, kept injectable for contract tests."""

    def ehlo(self) -> object: ...
    def has_extn(self, name: str) -> bool: ...
    def starttls(self, *, context: ssl.SSLContext) -> object: ...
    def login(self, user: str, password: str) -> object: ...
    def send_message(self, message: EmailMessage) -> dict[str, tuple[int, bytes]]: ...
    def quit(self) -> object: ...


class SMTPFactory(Protocol):
    def __call__(self, host: str, port: int, *, timeout: float) -> SMTPClient: ...


class SMTPSSLFactory(Protocol):
    def __call__(self, host: str, port: int, *, timeout: float, context: ssl.SSLContext) -> SMTPClient: ...


def _default_smtp(host: str, port: int, *, timeout: float) -> SMTPClient:
    return smtplib.SMTP(host, port, timeout=timeout)


def _default_smtp_ssl(host: str, port: int, *, timeout: float, context: ssl.SSLContext) -> SMTPClient:
    return smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)


def _address(value: str) -> str:
    if not isinstance(value, str) or len(value) > 254 or "\r" in value or "\n" in value:
        raise ValueError("address is invalid")
    display_name, parsed = parseaddr(value)
    if display_name or parsed != value or _ADDRESS.fullmatch(value) is None:
        raise ValueError("address is invalid")
    return value.casefold()


def _host(value: str) -> str:
    if not value or len(value) > 253 or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("SMTP host is invalid")
    return value


class SMTPConfiguration(BaseModel):
    """Disabled-by-default SMTP settings with an explicit address policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    host: str = ""
    port: int = 0
    security: SMTPTransportSecurity = SMTPTransportSecurity.STARTTLS
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    username: str | None = None
    password: SecretStr | None = Field(default=None, repr=False)
    sender: str = ""
    recipients: tuple[str, ...] = ()
    sender_allowlist: tuple[str, ...] = ()
    recipient_allowlist: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_enabled_configuration(self) -> SMTPConfiguration:
        if not self.enabled:
            return self
        _host(self.host)
        if not 1 <= self.port <= 65535:
            raise ValueError("SMTP port is invalid")
        if bool(self.username) != (self.password is not None):
            raise ValueError("SMTP authentication requires both username and password")
        sender = _address(self.sender)
        recipients = tuple(_address(value) for value in self.recipients)
        allowed_senders = frozenset(_address(value) for value in self.sender_allowlist)
        allowed_recipients = frozenset(_address(value) for value in self.recipient_allowlist)
        if not recipients or not allowed_senders or not allowed_recipients:
            raise ValueError("SMTP sender and recipient policies are required")
        if len(set(recipients)) != len(recipients) or sender not in allowed_senders:
            raise ValueError("SMTP sender or recipient policy is invalid")
        if any(recipient not in allowed_recipients for recipient in recipients):
            raise ValueError("SMTP sender or recipient policy is invalid")
        object.__setattr__(self, "sender", sender)
        object.__setattr__(self, "recipients", recipients)
        return self


class SMTPNotificationSink:
    """Synchronous SMTP adapter returning categories safe for durable storage."""

    def __init__(
        self,
        configuration: SMTPConfiguration,
        *,
        smtp_factory: SMTPFactory = _default_smtp,
        smtp_ssl_factory: SMTPSSLFactory = _default_smtp_ssl,
    ) -> None:
        self._configuration = configuration
        self._smtp_factory = smtp_factory
        self._smtp_ssl_factory = smtp_ssl_factory

    def send(self, envelope: NotificationEnvelope) -> SendResult:
        """Send one safe envelope; no raw SMTP response is retained or logged."""
        if not self._configuration.enabled:
            return SendResult.disabled()
        client: SMTPClient | None = None
        send_started = False
        try:
            client = self._open_client()
            self._authenticate(client)
            send_started = True
            refused = client.send_message(self._message(envelope))
            # A refusal mapping means at least one recipient was accepted; retrying
            # the whole message could duplicate delivery for those recipients.
            return SendResult.ambiguous() if refused else SendResult.accepted()
        except Exception as error:
            return self._map_exception(error, send_started=send_started)
        finally:
            if client is not None:
                # A post-response QUIT failure cannot alter the acknowledged send result.
                with suppress(Exception):
                    client.quit()

    def _open_client(self) -> SMTPClient:
        config = self._configuration
        context = ssl.create_default_context()
        if config.security is SMTPTransportSecurity.IMPLICIT_TLS:
            return self._smtp_ssl_factory(config.host, config.port, timeout=config.timeout_seconds, context=context)
        client = self._smtp_factory(config.host, config.port, timeout=config.timeout_seconds)
        client.ehlo()
        if not client.has_extn("starttls"):
            raise smtplib.SMTPNotSupportedError("STARTTLS unavailable")
        client.starttls(context=context)
        client.ehlo()
        return client

    def _authenticate(self, client: SMTPClient) -> None:
        if self._configuration.username is not None:
            password = self._configuration.password
            if password is None:  # Configuration validation makes this unreachable.
                raise RuntimeError("SMTP authentication configuration is incomplete")
            client.login(self._configuration.username, password.get_secret_value())

    def _message(self, envelope: NotificationEnvelope) -> EmailMessage:
        message = EmailMessage()
        message["From"] = self._configuration.sender
        message["To"] = ", ".join(self._configuration.recipients)
        message["Subject"] = "Lab Agent terminal closure notification"
        message["Message-ID"] = envelope.message_id
        message.set_content(
            "Lab Agent terminal closure notification.\n"
            f"Canvas ID: {envelope.canvas_id}\n"
            f"Trigger ID: {envelope.trigger_id}\n"
            f"Closure ID: {envelope.closure_id}\n"
            f"Round: {envelope.round_index}\n"
            f"Reason: {envelope.reason}\n"
            f"Notification key: {envelope.logical_key}\n"
        )
        return message

    @staticmethod
    def _map_exception(error: Exception, *, send_started: bool) -> SendResult:
        if isinstance(error, (smtplib.SMTPRecipientsRefused, smtplib.SMTPAuthenticationError, smtplib.SMTPNotSupportedError)):
            return SendResult.rejected()
        if isinstance(error, smtplib.SMTPResponseException):
            return SendResult.rejected() if error.smtp_code >= 500 else SendResult.transient()
        if isinstance(error, (smtplib.SMTPServerDisconnected, socket.timeout, TimeoutError, OSError)):
            return SendResult.ambiguous() if send_started else SendResult.transient()
        if isinstance(error, smtplib.SMTPException):
            return SendResult.rejected()
        return SendResult.ambiguous() if send_started else SendResult.transient()


__all__ = ["SMTPConfiguration", "SMTPNotificationSink", "SMTPTransportSecurity"]
