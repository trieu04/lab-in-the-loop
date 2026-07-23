"""Process-wide runtime context: the durable ``StateStore`` plus a unique
runtime instance id.

Built once at CLI startup (see ``cli.py``). Internal call sites and tests use
this context instead of the in-memory ``processed_loops`` set Phase 1 used as
a non-durable dedup mechanism -- the durable ``workflow_attempts`` ledger is
now the sole source of truth for "has this trigger already been handled",
surviving process restarts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from lab_agent.config import Settings
from lab_agent.notification_outbox import NotificationOutbox
from lab_agent.state_store import StateStore


class RuntimeStartupError(RuntimeError):
    """Raised when the durable ledger fails an integrity/audit-chain check at startup."""


@dataclass(frozen=True)
class RuntimeContext:
    """Everything a CLI command needs to touch the durable harness."""

    store: StateStore
    runtime_instance_id: str
    settings: Settings
    notifications: NotificationOutbox | None = None


def build_notification_outbox(
    store: StateStore, settings: Settings, runtime_instance_id: str
) -> NotificationOutbox:
    """Build notification delivery with the process-wide retry and lease settings."""
    return NotificationOutbox(
        store,
        settings.notification_smtp,
        runtime_instance_id,
        retry_base_seconds=settings.retry_base_seconds,
        retry_max_seconds=settings.retry_max_seconds,
        max_attempts=settings.max_attempts,
        reconciliation_seconds=settings.notification_reconciliation_seconds,
        lease_ttl_seconds=settings.notification_lease_ttl_seconds,
        batch_size=settings.notification_batch_size,
    )


def build_runtime_context(settings: Settings) -> RuntimeContext:
    """Create the DB parent dir, open/migrate the store, and verify integrity.

    Fails closed: any SQLite integrity problem or audit-chain tamper raises
    ``RuntimeStartupError`` before the process does any canvas work.
    ``runtime_instance_id`` is a fresh UUID4 per process -- never sourced from
    config -- so two processes started with identical config still get
    distinct writer identities for the single-writer canvas lease.
    """
    db_path = Path(settings.state_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        store = StateStore(db_path)
    except Exception as exc:  # corrupt/non-SQLite file, un-migratable schema, etc.
        raise RuntimeStartupError(f"Failed to open/migrate durable ledger at {db_path}: {exc}") from exc
    try:
        problems = store.integrity_check()
        if problems:
            raise RuntimeStartupError(f"SQLite integrity check failed: {'; '.join(problems)}")
        store.verify_audit_chain()
    except RuntimeStartupError:
        store.close()
        raise
    except Exception as exc:  # audit-chain tamper or other verification failure
        store.close()
        raise RuntimeStartupError(f"Durable ledger verification failed: {exc}") from exc
    runtime_id = str(uuid.uuid4())
    return RuntimeContext(
        store=store,
        runtime_instance_id=runtime_id,
        settings=settings,
        notifications=build_notification_outbox(store, settings, runtime_id),
    )


def close_runtime_context(ctx: RuntimeContext) -> None:
    """Release resources held by the runtime context. Safe to call once at clean shutdown."""
    ctx.store.close()


def release_lease_with_audit(store: StateStore, runtime_instance_id: str, canvas_id: str) -> bool:
    """Release the canvas lease (no-op if not currently held by us) and, on an
    actual release, append a ``canvas_lease_released`` audit event.

    Used at every clean CLI shutdown path -- ``watch``'s loop exit and
    ``once``'s single cycle -- so an orderly shutdown never leaves a canvas
    lease held past this process's own use of it.
    """
    released = store.release_canvas_lease(canvas_id, runtime_instance_id=runtime_instance_id)
    if released:
        store.append_audit_event(canvas_id, "canvas_lease_released", {"runtime_instance_id": runtime_instance_id})
    return released


__all__ = [
    "RuntimeContext",
    "RuntimeStartupError",
    "build_notification_outbox",
    "build_runtime_context",
    "close_runtime_context",
    "release_lease_with_audit",
]
