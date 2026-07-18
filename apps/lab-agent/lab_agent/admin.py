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

from pathlib import Path

from lab_agent.runtime import RuntimeContext
from lab_agent.state_store import AttemptNotQuarantinedError

#: Audit events for harness-wide operator actions (integrity check, backup)
#: are not scoped to one canvas, but ``audit_events.canvas_id`` is NOT NULL --
#: this sentinel makes that scope explicit rather than picking an arbitrary
#: real canvas id.
GLOBAL_CANVAS = "_global"


def check_integrity(ctx: RuntimeContext) -> int:
    """Run ``PRAGMA integrity_check`` and verify the audit hash chain.

    Prints a human-readable result and returns the process exit code: ``0``
    if both checks pass, ``1`` otherwise.
    """
    problems = ctx.store.integrity_check()
    chain_error = ""
    try:
        ctx.store.verify_audit_chain()
    except Exception as exc:  # noqa: BLE001 - report, don't crash the admin command
        chain_error = str(exc)[:200]

    ok = not problems and not chain_error
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


def backup(ctx: RuntimeContext, destination: str) -> int:
    """Take a consistent hot backup of the durable ledger (safe under WAL)."""
    dest = Path(destination)
    dest.parent.mkdir(parents=True, exist_ok=True)
    ctx.store.backup(dest)
    ctx.store.append_audit_event(GLOBAL_CANVAS, "operator_backup", {"destination": str(dest)})
    print(f"Backup written to {dest}")
    return 0


__all__ = ["GLOBAL_CANVAS", "backup", "check_integrity", "list_quarantined", "reset_attempt"]
