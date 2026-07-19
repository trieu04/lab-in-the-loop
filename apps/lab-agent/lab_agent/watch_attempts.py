"""Durable leasing and completion for one watcher trigger."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from lab_agent.adapters.base import (
    DeterministicProviderError,
    ProviderCallError,
    TransientProviderError,
)
from lab_agent.config import Settings
from lab_agent.state_store import AttemptStatus, StateStore

Work = Callable[[], Awaitable[tuple[bool, str]]]
_SAFE_FAILURE_DETAILS = frozenset({"invalid_citation", "schema_validation_failed"})


def failure_code(exc: Exception) -> tuple[str, bool]:
    """Return a durable-safe code and whether the trigger must be quarantined."""
    if isinstance(exc, TransientProviderError):
        return "provider_transient", False
    if isinstance(exc, DeterministicProviderError):
        return "provider_deterministic", True
    if isinstance(exc, ProviderCallError):
        return "provider_ambiguous", True
    return "unexpected_failure", False


def _safe_work_detail(detail: str) -> str:
    return detail if detail in _SAFE_FAILURE_DETAILS else "workflow_failed"


async def process_trigger(
    store: StateStore,
    *,
    canvas_id: str,
    trigger_id: str,
    runtime_instance_id: str,
    settings: Settings,
    work: Work,
) -> bool:
    """Lease a due trigger, run it, and durably record its safe outcome."""
    store.ensure_attempt(canvas_id, trigger_id)
    attempt = store.lease_due_attempt(
        canvas_id,
        trigger_id,
        lease_owner=runtime_instance_id,
        lease_ttl_seconds=settings.attempt_lease_ttl_seconds,
    )
    if attempt is None:
        return False
    store.append_audit_event(canvas_id, "attempt_leased", {"trigger_id": trigger_id})

    quarantine = False
    try:
        ok, detail = await work()
        detail = _safe_work_detail(detail) if not ok else ""
    except Exception as exc:  # noqa: BLE001 - watcher must durably contain failures
        detail, quarantine = failure_code(exc)
        ok = False

    if ok:
        store.mark_attempt_completed(canvas_id, trigger_id, lease_owner=runtime_instance_id)
        store.append_audit_event(canvas_id, "attempt_completed", {"trigger_id": trigger_id})
        return True

    updated = store.mark_attempt_failed(
        canvas_id,
        trigger_id,
        lease_owner=runtime_instance_id,
        error=detail,
        base_seconds=settings.retry_base_seconds,
        max_seconds=settings.retry_max_seconds,
        max_attempts=1 if quarantine else settings.max_attempts,
    )
    event = "attempt_quarantined" if updated.status is AttemptStatus.QUARANTINED else "attempt_failed"
    store.append_audit_event(canvas_id, event, {"trigger_id": trigger_id, "error": detail})
    return False


__all__ = ["Work", "failure_code", "process_trigger"]
