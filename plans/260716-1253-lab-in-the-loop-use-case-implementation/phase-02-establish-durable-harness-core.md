# Phase 2 — Establish Durable Harness Core

## Context Links

- Canonical spec: `docs/lab-in-the-loop-use-case-specification.md` (FR-019, NFR-006, NFR-008, §11 state model)
- System architecture: `docs/system-architecture.md` § "Target harness boundary (proposed)" — source text to update during implementation; the decision log ratifies incremental harness-first evolution
- Research: `researcher-01-260716-1253-durable-workflow-core.md` (§2 SQLite ledger, §7 rollback), `researcher-03-260716-1253-safe-execution-integrations.md` (§7 canvas_id tenant key, §6 lease)
- Decision log: durable state = SQLite WAL first, documented migration boundary

## Overview

- Priority: P1 (durable spine for all safety/audit work)
- Status: pending
- Effort: 6d
- Description: Add a local SQLite (WAL) ledger owned by `lab-agent` for restart-safe idempotency, an append-only audit/event log, unique idempotency keys, and a single-writer-per-canvas lease. Define the harness policy/contract boundary and a documented Postgres/multi-host migration boundary. No external services, no state-machine engine (YAGNI vs Temporal).

## Key Insights

- `processed_loops` is an in-memory `set[str]` in `watch.py` reset every `once`/restart — the only durability gap left after Phase 1's structural fix; the two are distinct problems (research-01 §2).
- The ledger must be a record *about* canvas events, not a second competing state machine — canvas stays source of truth.
- Every orchestrator function already threads `canvas_id`; make it the composite key now to avoid a Phase 8 migration.
- No `update_note`/`update_connector` MCP write tool exists, so a canvas-embedded "processed" marker is blocked — a local ledger is the correct, in-scope choice (research-01 §2 feasibility row).

## Requirements

- FR-LITL-019 durable across restart; NFR-LITL-006 (retry/resume, idempotency survives restart); NFR-LITL-008 (structured audit trail baseline).
- Single-writer-per-canvas invariant (research-03 §6) as the first production-topology guard.
- Migration boundary documented (decision log: SQLite first, Postgres/multi-host gated).

## Architecture / Data Flow

```
watch.process_once(canvas_id, runtime_instance_id)
  ├─ acquire/renew canvas lease; foreign live lease → skip
  ├─ lease a due workflow_attempt (pending/failed with next_retry_at <= now)
  ├─ prepare side_effect_intent(idempotency_key, expected graph mutation)
  ├─ reconcile: graph/external id already shows effect? → mark reconciled
  ├─ otherwise execute write/call → persist returned id → reconcile
  └─ only then mark workflow_attempt completed; crash/failure schedules retry
```

Tables (WAL, versioned migrations, short `BEGIN IMMEDIATE` transactions):

```sql
workflow_attempts(canvas_id, trigger_id, status, attempt_count, lease_owner, lease_expires_at,
                  next_retry_at, last_error, completed_at, PRIMARY KEY(canvas_id, trigger_id))
side_effect_intents(idempotency_key PRIMARY KEY, canvas_id, kind, input_hash, status,
                    external_id, attempt_count, next_retry_at, last_error, created_at, reconciled_at)
orchestrator_edges(canvas_id, connector_id, kind, round, created_at, PRIMARY KEY(canvas_id, connector_id))
audit_events(id INTEGER PK, sequence, canvas_id, round, event, payload_json,
             previous_hash, event_hash, created_at)
canvas_leases(canvas_id PRIMARY KEY, runtime_instance_id, expires_at)
schema_migrations(version PRIMARY KEY, applied_at, checksum)
```

Canvas remains workflow truth. The ledger owns recovery/idempotency evidence. `workflow_attempts` distinguishes a leased attempt from a completed one; `side_effect_intents` closes the crash window between external/canvas effects and local audit. Runtime instance ids are generated per process, never shared configuration.

## Related Code Files

- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/state_store.py` — SQLite connection, migrations, attempt leases, retry schedule, audit hash chain, canvas leases, and intent persistence. All keyed by `canvas_id`.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/recovery.py` — prepare/reconcile side-effect intents against the live canvas/external result before retrying.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/migrations/001_durable_harness.sql` — initial versioned schema with checksum.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/watch.py` — lease due attempts, use durable exponential backoff/max-attempt quarantine, reconcile incomplete intents, and mark completed only after effects are confirmed.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/nodes.py` — execute prepared intents and return stable created ids; never treat an un-audited write as completed.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/config.py` — add `state_db_path`, lease TTL, retry base/max delay, and max attempts; do not configure a reusable holder id.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/cli.py` — generate a unique runtime instance id, initialize/migrate/verify the store, and pass recovery dependencies through `once`/`watch`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/.gitignore` — add `.state/`, SQLite DB/WAL/SHM, backups, and recovery artifacts.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_state_store.py` — migrations, attempt lifecycle, retry/quarantine, lease, audit-chain integrity.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_recovery.py` — crash before/after write, graph reconciliation, duplicate intent, provider timeout.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_orchestrator.py` — restart-mid-loop integration against real SQLite + live-recompute `FakeMCP`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/docs/system-architecture.md`, `/home/ntdm/dev/lap-in-the-loop/docs/setup-and-operations.md`, `/home/ntdm/dev/lap-in-the-loop/docs/development-roadmap.md` — durable-state design, ledger location, and the documented migration boundary.

## Implementation Steps

1. Add versioned SQL migrations and store startup checks: WAL, `busy_timeout`, `synchronous=NORMAL`, `foreign_keys=ON`, schema checksum, and `PRAGMA integrity_check` operator command.
2. Implement workflow-attempt lifecycle (`pending → running → completed|failed|quarantined`) with expiring attempt lease. A crash never turns `running` into permanently completed work.
3. Implement runtime-unique canvas leases. A second process with the same config still receives a distinct instance id and cannot share a lease accidentally.
4. Implement durable retry scheduling: exponential backoff with jitter, max delay, max attempts, structured last error, quarantine/dead-letter state, and explicit operator retry/reset.
5. Implement side-effect intent/outbox: persist intent before a canvas/provider mutation, execute with stable idempotency key, store returned id, then reconcile against live graph/provider status before completion.
6. Rewire `watch.process_once` and node writes around attempts/intents. Keep broad watcher resilience, but do not swallow an error without updating the durable attempt.
7. Add append-only audit sequencing and hashes for safety-relevant events; verify chain continuity on startup/operator command. Store ids/hashes/reasons, not sensitive payload bodies.
8. Add migration, lifecycle, retry, quarantine, lease, crash-window, and reconciliation tests. Kill/restart at every boundary: before intent, after intent, after external write, after returned id, before completion.
9. Add backup/restore procedure and drill using a copied SQLite DB/WAL checkpoint. Verify restored state prevents duplicate canvas/external effects.
10. Docs: architecture, operations, roadmap, and changelog. Document single-host/local-disk scope, migration/backup policy, retry/quarantine operations, and the measured Postgres/multi-host trigger.

## Todo List

- [ ] Versioned SQLite migrations + integrity checks
- [ ] Attempt lifecycle, expiring attempt leases, retry/backoff, and quarantine
- [ ] Runtime-unique per-canvas writer lease
- [ ] Side-effect intent/outbox + live reconciliation
- [ ] Append-only sequenced/hashed safety audit events
- [ ] Config/CLI wiring and ignored DB/WAL/backup artifacts
- [ ] Crash-boundary, restart, quarantine, reconciliation, and audit-chain tests
- [ ] Backup/restore drill documented and verified
- [ ] Architecture + operations + roadmap + changelog updated

## Success Criteria / Validation

- `cd apps/lab-agent && uv run pytest -q && uv run ruff check lab_agent tests && uv run mypy lab_agent`
- Simulated restart at every intent/write/completion boundary eventually completes or quarantines work without duplicate node, connector, or external intent (NFR-006).
- A leased-but-incomplete attempt becomes retryable after expiry; only `completed` blocks future execution.
- A second runtime instance cannot write while a live canvas lease is held; lease expiry releases it.
- Quarantined work is visible and can be explicitly retried by an operator.
- Audit-chain and SQLite integrity checks pass before and after a tested backup/restore.
- `.db`, WAL/SHM, backups, and `.state/` never appear in `git status --short`.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Ledger drifts into a competing state machine | Med | High | Canvas remains workflow truth; ledger stores attempts/intents/recovery/audit only. |
| Crash after side effect but before completion duplicates work | Med | Critical | Intent persisted first, provider/graph reconciliation, stable idempotency keys, crash-boundary tests. |
| Retry storm consumes model budget | Med | High | Durable exponential backoff, max attempts, quarantine, Phase 4 budget authorization. |
| SQLite/audit corruption destroys authorization evidence | Low | Critical | Versioned migrations, integrity/hash-chain checks, checkpointed backup/restore drill. |
| WAL on network filesystem corrupts | Low | High | Local disk single-host mandate; Postgres/multi-host only after Phase 8 trigger. |
| Reused lease identity allows concurrent writers | Low | Critical | Generate unique runtime instance id; expiry + ownership tests. |

## Security Considerations

- `audit_events.payload_json` must exclude credentials and full document text; store ids/hashes/reasons only.
- Ledger is `canvas_id`-partitioned from day one, preventing cross-canvas leakage once multi-canvas arrives (NFR isolation groundwork).
- No new network surface; the store is a local file with the same git-ignore treatment as `.env`/`downloads/`.

## Next Steps / Dependencies

- Depends on: Phase 1 (green baseline, fail-visible writes).
- Blocks: Phase 3 (evidence/audit), Phase 5 (reuses WAL/lease patterns), Phase 6 (durable GateApproval store).
- Docs impact: major (architecture, operations, roadmap, changelog).
