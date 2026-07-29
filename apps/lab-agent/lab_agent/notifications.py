"""Safe, metadata-only contracts for terminal notifications."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_KEY_VERSION = "lab-agent-notification-v1"
_MESSAGE_ID_DOMAIN = "lab-agent.local"


class NotificationFailureCategory(StrEnum):
    """Sanitized, durable categories; raw transport errors never cross this boundary."""

    SMTP_TRANSIENT = "smtp_transient"
    SMTP_REJECTED = "smtp_rejected"
    SMTP_AMBIGUOUS = "smtp_ambiguous"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"


class SendDisposition(StrEnum):
    """Delivery outcome consumed by the outbox lifecycle."""

    ACCEPTED = "accepted"
    DISABLED = "disabled"
    TRANSIENT = "transient"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"


def notification_logical_key(
    *, canvas_id: str, trigger_id: str, closure_id: str, round_index: int, reason: str,
    tenant_id: str = "default",
) -> str:
    """Return a tenant-qualified logical identity for one terminal closure."""
    payload = {
        "canvas_id": canvas_id,
        "closure_id": closure_id,
        "reason": reason,
        "round_index": round_index,
        "schema_version": _KEY_VERSION,
        "trigger_id": trigger_id,
    }
    # Existing single-tenant ledgers derive their key without this field. Keep
    # that exact default value stable while separating non-default tenants.
    if tenant_id != "default":
        payload["tenant_id"] = tenant_id
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def message_id_for_logical_key(logical_key: str) -> str:
    """Return a deterministic, non-content Message-ID for SMTP deduplication."""
    if len(logical_key) != 64 or any(char not in "0123456789abcdef" for char in logical_key):
        raise ValueError("logical key must be a SHA-256 hex digest")
    return f"<{logical_key}@{_MESSAGE_ID_DOMAIN}>"


def _safe_metadata(value: str) -> str:
    if "\r" in value or "\n" in value or any(ord(char) < 32 for char in value):
        raise ValueError("notification metadata must not contain control characters")
    return value


class NotificationEnvelope(BaseModel):
    """One metadata-only notification, bound to a durable terminal closure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canvas_id: str = Field(min_length=1, max_length=200)
    trigger_id: str = Field(min_length=1, max_length=200)
    closure_id: str = Field(min_length=1, max_length=200)
    round_index: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=100, pattern=r"^[a-z][a-z0-9_]*$")
    tenant_id: str = Field(default="default", min_length=1, max_length=128)
    logical_key: str = ""
    message_id: str = ""

    @field_validator("canvas_id", "trigger_id", "closure_id", "reason", "tenant_id")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        return _safe_metadata(value)

    @model_validator(mode="after")
    def bind_deterministic_identifiers(self) -> NotificationEnvelope:
        expected_key = notification_logical_key(
            canvas_id=self.canvas_id,
            trigger_id=self.trigger_id,
            closure_id=self.closure_id,
            round_index=self.round_index,
            reason=self.reason,
            tenant_id=self.tenant_id,
        )
        if self.logical_key and self.logical_key != expected_key:
            raise ValueError("logical_key does not match terminal closure metadata")
        expected_message_id = message_id_for_logical_key(expected_key)
        if self.message_id and self.message_id != expected_message_id:
            raise ValueError("message_id does not match logical_key")
        object.__setattr__(self, "logical_key", expected_key)
        object.__setattr__(self, "message_id", expected_message_id)
        return self


class SendResult(BaseModel):
    """A sanitized result. It deliberately cannot carry a transport error string."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    disposition: SendDisposition
    failure_category: NotificationFailureCategory | None = None

    @model_validator(mode="after")
    def verify_category(self) -> SendResult:
        expected = {
            SendDisposition.ACCEPTED: None,
            SendDisposition.DISABLED: None,
            SendDisposition.TRANSIENT: NotificationFailureCategory.SMTP_TRANSIENT,
            SendDisposition.REJECTED: NotificationFailureCategory.SMTP_REJECTED,
            SendDisposition.AMBIGUOUS: NotificationFailureCategory.SMTP_AMBIGUOUS,
        }[self.disposition]
        if self.failure_category is not expected:
            raise ValueError("send disposition requires its fixed failure category")
        return self

    @classmethod
    def accepted(cls) -> SendResult:
        return cls(disposition=SendDisposition.ACCEPTED)

    @classmethod
    def disabled(cls) -> SendResult:
        return cls(disposition=SendDisposition.DISABLED)

    @classmethod
    def transient(cls) -> SendResult:
        return cls(disposition=SendDisposition.TRANSIENT, failure_category=NotificationFailureCategory.SMTP_TRANSIENT)

    @classmethod
    def rejected(cls) -> SendResult:
        return cls(disposition=SendDisposition.REJECTED, failure_category=NotificationFailureCategory.SMTP_REJECTED)

    @classmethod
    def ambiguous(cls) -> SendResult:
        return cls(disposition=SendDisposition.AMBIGUOUS, failure_category=NotificationFailureCategory.SMTP_AMBIGUOUS)


@runtime_checkable
class NotificationSink(Protocol):
    """Channel boundary. Implementations receive no experiment body or URLs."""

    def send(self, envelope: NotificationEnvelope) -> SendResult:
        """Attempt one delivery and return a sanitized, actionable outcome."""
        ...


__all__ = [
    "NotificationEnvelope",
    "NotificationFailureCategory",
    "NotificationSink",
    "SendDisposition",
    "SendResult",
    "message_id_for_logical_key",
    "notification_logical_key",
]
