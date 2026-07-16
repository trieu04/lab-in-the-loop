"""Fail-closed watcher skips for non-due durable attempts."""

from __future__ import annotations

from typing import Any

import pytest
from structlog.testing import capture_logs

from lab_agent.config import Settings
from lab_agent.state.models import AttemptStatus
from lab_agent.state_store import StateStore
from lab_agent.watch_attempts import process_trigger

CANVAS_ID = "canvas-regression"
TRIGGER_ID = "trigger-regression"
RUNTIME_ID = "watcher-current"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "attempt_lease_ttl_seconds": 30.0,
        "retry_base_seconds": 10.0,
        "retry_max_seconds": 60.0,
        "max_attempts": 5,
    }
    values.update(overrides)
    return Settings(**values)


def _prepare(store: StateStore, status: AttemptStatus) -> None:
    store.ensure_attempt(CANVAS_ID, TRIGGER_ID)
    if status is AttemptStatus.COMPLETED:
        store.lease_due_attempt(CANVAS_ID, TRIGGER_ID, lease_owner="previous", lease_ttl_seconds=30)
        assert store.mark_attempt_completed(CANVAS_ID, TRIGGER_ID, lease_owner="previous")
    elif status is AttemptStatus.QUARANTINED:
        store.lease_due_attempt(CANVAS_ID, TRIGGER_ID, lease_owner="previous", lease_ttl_seconds=30)
        store.mark_attempt_failed(
            CANVAS_ID,
            TRIGGER_ID,
            lease_owner="previous",
            error="safe-failure",
            base_seconds=10.0,
            max_seconds=60.0,
            max_attempts=1,
        )
    elif status is AttemptStatus.FAILED:
        store.lease_due_attempt(CANVAS_ID, TRIGGER_ID, lease_owner="previous", lease_ttl_seconds=30)
        store.mark_attempt_failed(
            CANVAS_ID,
            TRIGGER_ID,
            lease_owner="previous",
            error="safe-failure",
            base_seconds=10.0,
            max_seconds=60.0,
            max_attempts=5,
        )
    elif status is AttemptStatus.RUNNING:
        assert store.lease_due_attempt(
            CANVAS_ID, TRIGGER_ID, lease_owner="previous", lease_ttl_seconds=30
        )


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (AttemptStatus.COMPLETED, "completed"),
        (AttemptStatus.QUARANTINED, "quarantined"),
        (AttemptStatus.FAILED, "retry_backoff"),
        (AttemptStatus.RUNNING, "active_lease"),
    ],
)
async def test_process_trigger_skips_non_due_attempt_without_mutation(
    store: StateStore, status: AttemptStatus, reason: str
) -> None:
    _prepare(store, status)
    before = store.get_attempt(CANVAS_ID, TRIGGER_ID)
    audit_count = len(store.list_audit_events(CANVAS_ID))
    work_called = False

    async def work() -> tuple[bool, str]:
        nonlocal work_called
        work_called = True
        raise AssertionError("non-due attempt must not dispatch work")

    with capture_logs() as logs:
        assert not await process_trigger(
            store,
            canvas_id=CANVAS_ID,
            trigger_id=TRIGGER_ID,
            runtime_instance_id=RUNTIME_ID,
            settings=_settings(),
            work=work,
        )

    assert not work_called
    assert store.get_attempt(CANVAS_ID, TRIGGER_ID) == before
    assert len(store.list_audit_events(CANVAS_ID)) == audit_count
    assert logs == [
        {
            "canvas_id": CANVAS_ID,
            "trigger_id": TRIGGER_ID,
            "status": status.value,
            "reason": reason,
            "event": "attempt_skipped",
            "log_level": "info",
        }
    ]
    allowed = {"canvas_id", "trigger_id", "status", "reason", "event", "log_level"}
    prohibited = {
        "error",
        "detail",
        "last_error",
        "lease_owner",
        "lease_expires_at",
        "next_retry_at",
        "prompt",
        "provider_error",
    }
    assert set(logs[0]) <= allowed
    assert not prohibited.intersection(logs[0])
