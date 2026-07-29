"""Operator commands for the durable harness: integrity, quarantine, backup.

Thin wrappers over ``StateStore`` that add operator-facing audit events and a
consistent stdout/exit-code contract for ``cli.py``'s admin subcommands.
Every command here is an explicit, one-shot operator action -- there is no
config-driven automatic retry/reset, and no unsafe restore-overwrite command.
A restore drill (verify a backup file opens and integrity-checks cleanly) is
documented as a test/procedure in docs/setup-and-operations.md, not a command
that overwrites a live database.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from lab_agent.runtime import RuntimeContext
from lab_agent.state.notification_outbox import (
    FailureCategory,
    NotificationNotQuarantinedError,
    NotificationStatus,
)
from lab_agent.state_store import AttemptNotQuarantinedError

#: Unbound legacy stores use this sentinel for explicitly global operator events.
GLOBAL_CANVAS = "_global"


def _require_global_store(ctx: RuntimeContext) -> None:
    if getattr(ctx.store, "tenant_context", None) is not None:
        raise PermissionError("global operator action is unavailable to a tenant-bound runtime")


def check_integrity(ctx: RuntimeContext) -> int:
    """Run ``PRAGMA integrity_check`` and verify the audit hash chain.

    Prints a human-readable result and returns the process exit code: ``0``
    if both checks pass, ``1`` otherwise.
    """
    problems = ctx.store.integrity_check()
    chain_error = ""
    global_operator = getattr(ctx, "tenant_context", None) is None
    try:
        if global_operator:
            ctx.store.verify_all_audit_chains()
        else:
            ctx.store.verify_audit_chain()
    except Exception as exc:  # noqa: BLE001 - report, don't crash the admin command
        chain_error = str(exc)[:200]

    ok = not problems and not chain_error
    if global_operator and getattr(ctx.store, "tenant_context", None) is None:
        ctx.store.append_audit_event(
            GLOBAL_CANVAS, "operator_integrity_check",
            {"ok": ok, "problem_count": len(problems), "chain_error": chain_error},
        )
    if ok:
        print("OK: SQLite integrity and audit chain verified.")
        return 0
    for problem in problems:
        print(f"INTEGRITY PROBLEM: {problem}")
    if chain_error:
        print(f"AUDIT CHAIN PROBLEM: {chain_error}")
    return 1


def list_quarantined(ctx: RuntimeContext, canvas_id: str | None) -> int:
    """Print every quarantined attempt, optionally filtered to one canvas."""
    attempts = ctx.store.list_quarantined_attempts(canvas_id)
    if not attempts:
        print("No quarantined attempts.")
        return 0
    for a in attempts:
        print(f"{a.canvas_id}\t{a.trigger_id}\tattempts={a.attempt_count}\tlast_error={a.last_error!r}")
    return 0


def reset_attempt(ctx: RuntimeContext, canvas_id: str, trigger_id: str) -> int:
    """Reset one quarantined attempt back to pending (explicit operator action)."""
    try:
        ctx.store.reset_quarantined_attempt(canvas_id, trigger_id)
    except AttemptNotQuarantinedError as exc:
        print(f"ERROR: {exc}")
        return 1
    ctx.store.append_audit_event(canvas_id, "operator_reset", {"trigger_id": trigger_id})
    print(f"Reset {canvas_id}/{trigger_id} to pending.")
    return 0


def backup(ctx: RuntimeContext, destination: str, *, global_authority: bool = False) -> int:
    """Take a consistent hot backup only under explicit global authority."""
    if not global_authority:
        raise PermissionError("full SQLite backup requires explicit global authority")
    _require_global_store(ctx)
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ctx.store.backup(dest)
    ctx.store.append_audit_event(GLOBAL_CANVAS, "operator_backup", {"destination": str(dest)})
    print(f"Backup written to {dest}")
    return 0


def notification_status(ctx: RuntimeContext, canvas_id: str | None) -> int:
    """Print only delivery counts, optionally for one canvas identifier."""
    counts = Counter(
        record.status for record in ctx.store.list_notification_records(canvas_id=canvas_id)
    )
    print(" ".join(f"{status.value}={counts[status]}" for status in NotificationStatus))
    return 0


def list_notification_quarantined(ctx: RuntimeContext, canvas_id: str | None) -> int:
    """List safe identifiers and fixed categories for quarantined notifications."""
    records = ctx.store.list_notification_records(canvas_id=canvas_id, status=NotificationStatus.QUARANTINED)
    if not records:
        print("No quarantined notifications.")
        return 0
    for record in records:
        category = record.failure_category.value if record.failure_category else "none"
        print(
            f"{record.canvas_id}\t{record.logical_key}"
            f"\tattempts={record.attempt_count}\tcategory={category}"
        )
    return 0


def retry_notification(ctx: RuntimeContext, logical_key: str) -> int:
    """Explicitly return one quarantined notification to the durable queue."""
    try:
        ctx.store.reset_quarantined_notification(logical_key)
    except NotificationNotQuarantinedError:
        print("ERROR: notification is not quarantined.")
        return 1
    print("Notification retry queued.")
    return 0


def quarantine_notification(ctx: RuntimeContext, logical_key: str) -> int:
    """Quarantine one currently due notification without attempting delivery."""
    record = ctx.store.get_notification(logical_key)
    if record is None or record.status is not NotificationStatus.PENDING:
        print("ERROR: notification is not pending.")
        return 1
    leased = ctx.store.lease_due_notification(
        logical_key,
        lease_owner=ctx.runtime_instance_id,
        lease_ttl_seconds=ctx.settings.notification_lease_ttl_seconds,
        reconciliation_window_seconds=ctx.settings.notification_reconciliation_seconds,
    )
    if leased is None:
        print("ERROR: notification is not due.")
        return 1
    ctx.store.quarantine_notification(
        logical_key,
        ctx.runtime_instance_id,
        leased.lease_generation,
        FailureCategory.IDEMPOTENCY_CONFLICT,
    )
    print("Notification quarantined.")
    return 0


__all__ = ["GLOBAL_CANVAS", "backup", "check_integrity", "list_notification_quarantined", "list_quarantined", "notification_status", "quarantine_notification", "reset_attempt", "retry_notification"]
