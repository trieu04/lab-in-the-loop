# Phase 8 — Integrate Execution, Analysis & Knowledge

## Context Links

- Canonical requirements: `docs/lab-in-the-loop-use-case-specification.md` (FR-LITL-006/007/008/009/010/017/018, BR-LITL-008, NFR-LITL-005)
- Completed prerequisite: `phase-07-implement-in-silico-and-approval-gates.md`
- Contracts/persistence research: `../reports/researcher-260723-phase8-contracts-persistence.md`
- Orchestration/artifact research: `../reports/researcher-260723-phase8-orchestration-artifacts.md`
- Tests/docs/gates research: `../reports/researcher-260723-phase8-tests-docs-gates.md`
- Current seams: `apps/lab-agent/lab_agent/approval_service.py`, `state_store.py`, `state/intents.py`, `recovery.py`, `watch.py`, `artifact_store.py`, `durable_browser.py`, `integrations/in_silico.py`

## Overview

- Priority: P1 safety and durable contracts
- Status: **complete**
- Execution scope: **7A mocks/contracts only** — delivered
- Effort: 8d core delivered; real adapters remain unestimated external child plans
- Description: add typed execution, analysis, artifact-reference, knowledge-version, and conflict contracts; deterministic dry-run adapters; durable lifecycle persistence; approval-bound orchestration; Browser projections; exact regression gates. No live Flywheel, knowledge-store, lab, robot, or hardware API is designed or enabled here.

## Scope Boundary

### In 7A

- Typed immutable Pydantic boundary models and adapter Protocols.
- Deterministic lab-execution, analysis, and knowledge dry-run implementations.
- Five durable tables in one additive migration: `execution_runs`, `analysis_runs`, `artifact_refs`, `knowledge_versions`, `conflict_records`.
- Restart-safe submit/status/reconcile/abort/rerun behavior using existing side-effect intents.
- Explicit mock/dry-run evidence lineage, interpretation, knowledge append, conflict preservation, and Browser visibility.
- Contract, persistence, integration, restart, safety, and unchanged-regression tests.

### External child-plan gates; not 7A blockers

- **7B Flywheel/HPC:** real API/auth schema, sandbox fixtures, locality/retention, SLA/timeouts, idempotency/reconciliation, cancellation, owner approval, rollout/rollback.
- **7C knowledge store:** authoritative version/conflict schema, retention, tenant/access policy, recovery semantics, migration ownership, domain-owner acceptance.
- **7D lab/robot:** hardware SDK, production identity, authorization, safety review, sandbox, abort/incident runbook, locality/retention, operator sign-off, rollout/rollback.
- Production in-silico and production identity readiness remain separate external gates inherited from Phase 7.

No endpoint, SDK payload, credential shape, provider SLA, storage claim, or real success fixture may be invented in 7A.

## Settled Architecture Decisions

1. **SQLite remains canonical; Canvas remains a projection.** Canvas topology starts work only at the existing `setups_needing_run` ingress. Provider lifecycle resumes from SQLite, never from Browser markers/connectors.
2. **`watch.py` remains coordinator.** It leases/snapshots/triggers one narrow Phase 8 facade call. Execution, analysis, interpretation, and knowledge logic live in small modules; do not grow `watch.py`, `orchestrator.py`, `state_store.py`, `artifact_render.py`, or other near-limit files.
3. **Use existing ledgers, not a new job framework.** `side_effect_intents` owns external submit/abort claim, external id, ambiguity, and reconciliation. `workflow_attempts` owns only watcher scheduling/backoff. Do not add `job_attempts`, `execution_events`, or another generic outbox.
4. **Five-table domain persistence.** Reruns, refs, knowledge versions, and conflicts append new rows. Execution/analysis identity and lineage fields are immutable; only guarded monotonic lifecycle fields may transition until terminal. No row deletion, terminal reset, lineage rewrite, or historical overwrite.
5. **Artifact ledgers have separate roles.** `artifact_refs` stores logical raw/derived/log pointers and hashes; existing `artifact_versions` stores immutable Browser-facing projections. Neither stores external raw bytes, credentials, capability URLs, or arbitrary provider response bodies.
6. **Mock and measured evidence are type-separated.** Dry-run adapters can construct only `MOCK_OR_DRY_RUN`. `MEASURED` requires a real capture receipt, real provider job identity, retained location, immutable digest, real mode, and an installed real adapter. No installed real adapter exists in 7A, so a real-submit/measured predicate is impossible to satisfy with mock artifacts or configuration booleans.
7. **Real dispatch stays unreachable.** Add a Phase 8 lifecycle feature flag defaulting false; when enabled in tests/dev, only `dry_run` is selectable. Disabled-real factories always raise `implementation_not_installed`, even if readiness booleans are true. Do not reinterpret `wet_lab_execution_enabled` or legacy `run_on_robot` output as real execution.
8. **Conflict handling is preservation, not scientific adjudication.** 7A uses a deterministic mock disposition to exercise append-versus-conflict behavior. It preserves both versions and evidence; it does not claim semantic conflict detection or auto-resolution.

## Architecture / Data Flow

```text
Canvus scan: setups_needing_run
  → watch coordinator leases one hash/version-bound workflow trigger
  → Phase 8 feature flag + dry_run-only factory check
  → require_current_execution_authorization(canvas, proposal_hash, validation_result_hash)
  → persist ExecutionRun + prepare lab_execution_submit intent
  → deterministic lab adapter submit/status or reconcile by idempotency key
  → persist MOCK_OR_DRY_RUN raw ArtifactRef rows
  → persist AnalysisRun + prepare analysis_submit intent
  → deterministic analysis adapter submit/status or reconcile
  → persist MOCK_OR_DRY_RUN derived ArtifactRef rows
  → typed interpretation against original hypothesis and explicit source refs
  → local KnowledgeAdapter append
      ├─ append KnowledgeVersion
      └─ append KnowledgeVersion + ConflictRecord preserving both sides
  → persist canonical Browser artifact/version
  → render Execution / Analysis / Knowledge / Conflict views labeled
    "DRY RUN / MOCK — NOT MEASURED"
```

On each watcher cycle, incomplete execution/analysis lifecycle rows are reconciled from SQLite independently of Canvas topology. A submitted or ambiguous intent is never blindly submitted again.

## Typed Contract Design

Create `apps/lab-agent/lab_agent/models/execution.py`; follow current `ConfigDict(extra="forbid", frozen=True)`, aware timestamps, bounded strings, SHA-256 patterns, `StrEnum`, and canonical-hash patterns.

Minimum types:

- `RunMode`: `dry_run | sandbox | real`; 7A factory accepts only `dry_run`.
- `EvidenceKind`: `mock_or_dry_run | measured`; mixed-kind analysis rejected in 7A.
- `ExternalRunStatus`: `pending | submitted | running | reconciling | ambiguous | succeeded | failed | abort_requested | aborted | blocked`.
- Typed failure enum: timeout, provider failure, invalid schema, not ready, authorization stale, reconciliation unsupported, retention/locality denied, aborted; persist code only, not raw provider text.
- `ArtifactRef`: id, canvas, owner linkage, digest, bounded logical URI, media type, classification, role (`raw | derived | log`), evidence kind, retention timestamp, recorded timestamp. No bytes, credentials, tokenized URL, mutable local-path assumption, or generic metadata dict.
- `ExecutionRequest` / `ExecutionRun`: request/run ids, canvas/setup/round, proposal and validation hashes, adapter name/version, mode/evidence kind, submit and optional abort intent keys, provider execution id, status/timestamps, failure code, optional `rerun_of_execution_id`.
- `AnalysisRequest` / `AnalysisRun`: execution id, source ArtifactRef ids, adapter/version, mode/evidence kind, submit/abort keys, provider job id, status/timestamps, failure code, optional `rerun_of_analysis_id`.
- `InterpretationResult`: original hypothesis/hash, source run/ref ids, evidence kind, caveats, bounded interpretation, deterministic 7A disposition (`append | conflict`), prior-version reference when conflict is requested.
- `KnowledgeVersion`: immutable version id, canvas, source execution/analysis ids, proposal/hypothesis hashes, evidence kind, provenance ref ids, payload/content hash, idempotency key, timestamp.
- `ConflictRecord`: immutable conflict id, canvas, prior/proposed version ids, old/new hypothesis hashes, evidence ref ids, safe reason code/text, idempotency key, timestamp.

## Persistence Design

### Migration ownership

- Create exactly `apps/lab-agent/lab_agent/migrations/010_execution_analysis_knowledge.sql`.
- Preserve untracked `008_notification_reconciliation_index.sql` and `009_notification_due_indexes.sql` **byte-for-byte**. Do not rename, fold, edit, stage, or delete them.
- Migration discovery is numeric and checksum-locked. Fresh and existing stores must apply 008, 009, then 010. Never edit migration 010 after it has been applied; later corrections require 011+.

### Five tables

| Table | Required shape and invariants | Indexes |
|---|---|---|
| `execution_runs` | PK run id; canvas/setup/round; proposal/result hashes; adapter/version; mode/evidence kind; unique submit key; optional unique abort key/provider id; guarded status; self-FK rerun; safe failure code; created/submitted/finished timestamps. Identity/lineage immutable; no delete/reset. | `(canvas_id, setup_id, created_at)`, provider id, rerun id, status |
| `analysis_runs` | PK analysis id; canvas; FK execution id; adapter/version; mode/evidence kind; unique submit/optional abort keys; provider id; guarded status; self-FK rerun; safe failure code/timestamps. | `(canvas_id, execution_run_id, created_at)`, provider id, rerun id, status |
| `artifact_refs` | PK ref id; canvas; exactly one owner FK (`execution_run_id` XOR `analysis_run_id`); role; evidence kind; SHA-256; logical location; media type; classification; retention and created timestamps. Insert-only. | owner FKs, `(canvas_id, content_hash)` |
| `knowledge_versions` | PK version id; canvas; source execution/analysis FKs; proposal/hypothesis hashes; evidence kind; bounded canonical payload/provenance JSON; content hash; unique idempotency key; created timestamp. Insert-only. | `(canvas_id, created_at)`, source ids, content hash |
| `conflict_records` | PK conflict id; canvas; prior/proposed knowledge FKs; old/new hypothesis hashes; bounded evidence-ref JSON; safe reason; unique idempotency key; created timestamp. Insert-only; no resolution columns in 7A. | `(canvas_id, created_at)`, prior/proposed ids |

All reads/writes verify `canvas_id`. Foreign keys and CHECK constraints are DB backstops; Python transition guards return precise fail-closed errors.

### Intent and retry semantics

- Submit kinds: `lab_execution_submit`, `analysis_submit`.
- Abort kinds: `lab_execution_abort`, `analysis_abort`.
- Prepare run row and intent before adapter call; atomically mark intent submitted immediately before entering adapter code.
- Timeout/process death after submit leaves lifecycle `ambiguous`/`reconciling`. Query `find_by_idempotency_key` or provider id. Retry submit only after a successful authoritative query proves no external job exists.
- If reconciliation is unsupported/unavailable, remain blocked/ambiguous and make zero new submit calls.
- Rerun creates a new run row, new submit key, and self-FK to the original. Replay of the same rerun request converges to that row.
- Abort creates a separate intent. Ambiguous abort remains `abort_requested`/blocked until reconciled. Preserve all rows, refs, artifacts, and audit history.
- `workflow_attempts` may retry the watcher trigger; it may not convert submitted/executed/ambiguous provider work into another provider submit.

## Module and File Plan

### Task #30 — contracts and persistence

**Owns only:**

- Create `apps/lab-agent/lab_agent/models/execution.py`.
- Modify `apps/lab-agent/lab_agent/models/__init__.py` for exports.
- Create `apps/lab-agent/lab_agent/migrations/010_execution_analysis_knowledge.sql`.
- Create `apps/lab-agent/lab_agent/state/external_models.py` for row projections.
- Create `apps/lab-agent/lab_agent/state/external_runs.py` for execution/analysis inserts, lookups, guarded transitions, rerun lineage.
- Create `apps/lab-agent/lab_agent/state/knowledge.py` for refs, versions, conflicts.
- Create `apps/lab-agent/lab_agent/state/execution_store.py` with `ExecutionStoreMixin` facade methods.
- Modify `apps/lab-agent/lab_agent/state_store.py` only to compose/export the mixin; add no Phase 8 methods inline and keep the file at or below its current size.

### Task #31 — dry-run adapters; parallel-safe with #30

**Owns only:**

- Create `apps/lab-agent/lab_agent/integrations/lab_execution.py`.
- Create `apps/lab-agent/lab_agent/integrations/flywheel.py`.
- Create `apps/lab-agent/lab_agent/integrations/knowledge.py`.
- Modify `apps/lab-agent/lab_agent/integrations/__init__.py` for exports.

Each module contains one async runtime-checkable Protocol, deterministic dry-run implementation, typed errors, reconciliation capability declaration, and disabled-real sentinel. Do not create a shared adapter base class or provider-specific real request/response model.

### Task #32 — orchestration and projections; starts after #30 and #31

**Owns only after shared-file reservation check:**

- Create `apps/lab-agent/lab_agent/execution_lifecycle.py` for submit/status/reconcile/abort transitions.
- Create `apps/lab-agent/lab_agent/analysis_lifecycle.py` for analysis progression and retained-ref/rerun rules.
- Create `apps/lab-agent/lab_agent/execution_orchestrator.py` as the narrow facade.
- Create `apps/lab-agent/lab_agent/knowledge_update.py` for typed interpretation, append, and conflict preservation.
- Create `apps/lab-agent/lab_agent/watch_execution.py` for watcher handoff/reconciliation selection.
- Create `apps/lab-agent/lab_agent/artifact_lifecycle_payloads.py` for safe Browser payloads and mandatory mock labels.
- Create `apps/lab-agent/lab_agent/artifact_browser_registry.py`; move bucket/fallback-layout registration out of near-limit `durable_browser.py` before adding Execution/Analysis/Knowledge/Conflict entries.
- Modify `apps/lab-agent/lab_agent/watch.py` only for one coordinator delegation; keep it at or below 200 lines.
- Modify `apps/lab-agent/lab_agent/config.py` and `runtime.py` for default-off 7A factory wiring. No 7A setting may select a real implementation.
- Modify `apps/lab-agent/lab_agent/models/artifact.py` to add `ArtifactType.EXECUTION`; reuse existing ANALYSIS/KNOWLEDGE/CONFLICT and generic renderer sections.
- Modify `apps/lab-agent/lab_agent/durable_browser.py` to consume the extracted registry; do not grow `artifact_render.py` (already over the project line budget).
- After concurrent scanner ownership clears, add display-only Execution/Analysis/Knowledge/Conflict buckets in `apps/canvus-mcp/canvus_mcp/experiment_widgets.py`, `experiments.py`, `tools/experiments.py`, and config if required. These markers never schedule provider work.

Do not add Phase 8 logic to `orchestrator.py`, `orchestrator_robot.py`, `nodes.py`, or `models/states.py` unless a failing contract proves it necessary. Preserve the legacy mock-result path as compatibility, clearly labeled non-real.

### Task #33 — tests only

**Owns test files only:** new contract/persistence/orchestration/knowledge tests and narrow additions to existing non-dirty regression files. Must not edit `apps/lab-agent/tests/test_notification_outbox.py`; run it unchanged.

### Tasks #34–#36

- #34 owns sequential simplification of Phase 8 implementation files and validation commands; no test-contract weakening.
- #35 is read-only review plus review report/evidence ownership; no implementation edits unless a new fix task is explicitly assigned.
- #36 owns README/docs/changelog/roadmap, Phase 8 completion evidence, this phase file, and the Phase 8 row in `plan.md`; no app/test edits.

## Dependency Graph, Rollback, and Observable Exit

| Task | Depends on | Parallel/file rule | Observable done | Rollback without cascade | Main risk (L/I) and countermove |
|---|---|---|---|---|---|
| #30 Contracts/persistence | #29 | Parallel with #31; owns models/state/migration only | Migration 010 creates exactly five tables; typed round-trips, canvas scope, idempotency, transitions, rerun/abort lineage proven | Revert code while leaving applied additive tables intact; never down-migrate or edit applied SQL | Schema churn M/H: keep real-provider fields out; later child plans use 011+ |
| #31 Dry-run adapters | #29 | Parallel with #30; owns integrations only | Three deterministic adapters pass one shared contract; disabled-real call count/network count zero | Remove factory registration/revert modules; no durable data loss | Mock mistaken for real M/C: typed evidence kind, mandatory labels, disabled-real sentinel |
| #32 Orchestration/artifacts | #30, #31 | Sole owner of watch/config/runtime/scanner/artifact seams; no concurrent edits | Approved dry-run completes/reconciles from SQLite; flag-off and stale gates make zero submits; visible labeled artifacts | Disable Phase 8 flag and restore prior watcher handoff; retain all rows/artifacts/audit | Duplicate external work M/C: intent-before-call plus authoritative reconcile-before-retry |
| #33 Tests | #30, #31, #32 | Test files only | Exact matrix below green; prohibited outcomes asserted, not inferred | Revert test-only changes; implementation remains inspectable | False green M/H: real SQLite + call counters + durable row/hash assertions |
| #34 Simplify/validate | #33 | Sequential access to Phase 8 implementation; tests read-only | Focused and full suites, Ruff, mypy, compileall, parity, diff check all green | Revert simplification commit/patch only; rerun same gates | Large-file regression H/M: extract single-purpose modules before adding behavior |
| #35 Review | #34 | Read-only | Score ≥9.5/10, zero Critical/High correctness or safety findings, safety contract intact | No code rollback needed; open fix task and return to #34 | Hidden auth/evidence bypass L/C: adversarial stale-hash, cross-canvas, mock-as-measured review |
| #36 Docs/evidence | #35 | Docs/plan/evidence only | Docs match shipped 7A; Phase 8 completion evidence reproducible; external gates explicit | Revert inaccurate docs/evidence only; never rewrite implementation history | Overclaiming real capability M/H: use mock/dry-run wording and child-plan gates everywhere |

Execution order: `#29 → (#30 || #31) → #32 → #33 → #34 → #35 → #36`.

Before #32 touches shared files, re-run `git status`/`git diff` and confirm ownership of `watch.py`, `config.py`, runtime, scanner files, and tests. The concurrent `260723-0029-experiment-mode-validation-stop-email` changes land first or a single lead-owned integration applies both. Never edit migrations 008/009 or the dirty notification-outbox test.

## Exact Acceptance / Test Matrix

**Validation run 2026-07-23:** 133 focused passed (contracts, persistence, reconciliation, watcher, Browser, approval, intent, artifact, loop, and notification regressions); 684 full lab-agent passed (4 existing dependency deprecation warnings); 163 full Canvus-MCP passed (3 existing dependency deprecation warnings). Ruff, mypy, compileall, workflow parity, diff check all green.

| Layer | Case | Required assertion | Prohibited outcome | Test result |
|---|---|---|---|---|
| Unit/contract | Typed adapter parity | Lab, Flywheel, Knowledge mocks share typed request/result, stable key, status, abort/cancel, reconciliation capability, typed failures, no secret/raw-body retention | Provider-specific untyped dicts; network access | ✓ pass |
| Unit | Model truth boundary | Mock constructors yield only `MOCK_OR_DRY_RUN`; mixed basis rejected; measured requires real capture receipt and installed real adapter | Mock ref/result accepted as measured | ✓ pass |
| Persistence | Migration/reopen | 010 follows untouched 008/009; exactly five new tables/indexes; reopen idempotent; checksum drift fails; existing DB readable | Renumber/edit 008/009; destructive backfill | ✓ pass |
| Persistence | Scope/idempotency | All rows canvas-scoped; same key/same input converges; same key/different input fails; invalid transitions/FKs rejected | Cross-canvas lookup/write; terminal reset/delete | ✓ pass |
| Integration | Approval/default-off | Flag off, missing/stale/reordered/wrong-canvas approvals, unsupported mode/classification, or disabled-real factory produces zero submits and one safe blocked/Needs Input result where applicable | Flag or Canvas topology alone dispatches work | ✓ pass |
| Integration/E2E | 7A happy path | Approved setup → one dry-run execution → raw mock refs → one mock analysis → explicit mock interpretation → one knowledge version or conflict; one lineage per key | Any measured/scientific/real-lab claim | ✓ pass |
| Restart | Ambiguous submit, capable adapter | Crash after provider accepts submit; reopen; reconcile same external id; submit count remains **1**; intent/run/audit/budget lineage not duplicated | Second submit/external id | ✓ pass |
| Restart | Ambiguous submit, no reconciliation | Lifecycle remains ambiguous/blocked; downstream stages blocked; restart submit count **0** | Blind retry or guessed success/failure | ✓ pass |
| Lifecycle | Failed analysis/rerun | Raw refs immutable/readable; failed AnalysisRun has safe code; no interpretation/version; explicit rerun creates one linked row; replay converges | Ref deletion, old-row mutation, unlinked rerun, KB update | ✓ pass |
| Lifecycle | Abort | Persist abort request/intent; acknowledgement reaches aborted; ambiguity remains reconcilable; audit/incident context appended | Deleting run/artifacts; retrying abort blindly; downstream analysis | ✓ pass |
| Knowledge | Append-only history | v1 bytes/hash/provenance unchanged after v2; same key returns v2 not v3; source lineage complete | UPDATE/delete old version; duplicate retry version | ✓ pass |
| Knowledge | Conflict preservation | Proposed immutable version plus ConflictRecord references old/new versions and evidence; Conflict Browser artifact rendered | Overwrite/auto-resolution/free-text-only log | ✓ pass |
| Boundary | Mock/measured non-conflation | Every run/ref/interpretation/version/audit/payload carries mock kind; Browser repeats `DRY RUN / MOCK — NOT MEASURED`; real factory stays disabled even with readiness booleans true | `measured`, `scientific`, or production status derived from mock | ✓ pass |
| Regression | Canvus/classification | Display buckets retain `data_classification`; markers remain non-authorizing; scanner policy stays pure | Classification loss or marker-driven dispatch | ✓ pass |
| Regression | Existing durable systems | Notification, intent, artifact, approval, loop, and restart suites stay green; notification test file unchanged | Lease/index/reconciliation regression | ✓ pass |

Use deterministic mocks, `FakeMCP`, scripted adapters, call counters, and temporary **real SQLite** files. No live credential, API, hardware, Flywheel, lab, robot, or external knowledge-store test belongs in 7A.

## Implementation Steps

1. #30: land immutable contracts, evidence/status/error enums, migration 010, single-purpose repositories, row models, and `ExecutionStoreMixin`; preserve 008/009 exactly.
2. #31 in parallel: land three Protocol/dry-run/disabled-real adapter modules without provider-specific real schemas.
3. #32: wire factories default-off/dry-run-only; implement execution submit/reconcile/abort first, then analysis retention/rerun, then typed interpretation and knowledge append/conflict.
4. #32: persist canonical data before Browser projection. Add execution artifact type and display buckets; Browser labels project typed evidence kind and never authorize work.
5. #32: replace only the approved execution handoff with one watcher delegate after shared-file ownership clears. Resume incomplete lifecycle rows from SQLite every cycle.
6. #33: implement the exact matrix, including crash windows, unsupported reconciliation, zero-call gates, cross-canvas denial, immutable history, and unchanged notification regression.
7. #34: simplify modules to ≤200 lines where practical, compile after edits, then run focused and full gates. Do not weaken contracts to obtain green tests.
8. #35: adversarially review authorization freshness, ambiguity, mock/measured separation, secrets/locality, append-only history, rollback, and concurrent-file preservation.
9. #36: update README and canonical docs with 7A-only truth, adapter tiers, migration/backup effect, external child-plan gates, validation counts, and review evidence. Mark Phase 8 complete only after all gates pass.

## Todo List

- [x] Typed execution/analysis/artifact/interpretation/knowledge/conflict contracts added
- [x] Migration `010_execution_analysis_knowledge.sql` creates exactly five durable tables
- [x] `ExecutionStoreMixin` and single-purpose state modules keep `StateStore` within size budget
- [x] Lab/Flywheel/knowledge dry-run adapters pass shared contract tests
- [x] Disabled-real factories remain unreachable and default-off
- [x] Approval-bound execution orchestration is restart-safe and reconcile-before-retry
- [x] Failed analysis retains refs and supports one idempotent linked rerun
- [x] Abort preserves history and blocks downstream progression
- [x] Knowledge versions and conflicts are append-only and provenance-complete
- [x] Mock/dry-run and measured evidence cannot be conflated
- [x] Watch stays coordinator-only; shared watch/config/scanner changes are sequenced safely
- [x] Full acceptance matrix, lint, typing, compile, parity, and regression gates pass
- [x] Real Flywheel, knowledge-store, and lab/robot child-plan gates remain explicit
- [x] README/docs/changelog/roadmap and completion evidence updated

## Validation Commands

Focused gate after #33:

```bash
cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent
uv run pytest -q \
  tests/contracts/test_lab_execution_contract.py \
  tests/contracts/test_flywheel_contract.py \
  tests/contracts/test_knowledge_contract.py \
  tests/test_execution_models.py \
  tests/test_execution_store.py \
  tests/test_execution_reconciliation.py \
  tests/test_execution_orchestrator.py \
  tests/test_knowledge_update.py \
  tests/test_execution_modes.py \
  tests/test_in_silico_adapter.py \
  tests/test_submitted_intents.py \
  tests/test_artifact_store.py \
  tests/test_artifact_idempotency.py \
  tests/test_state_store_migrations.py \
  tests/test_watch_approval_gates.py \
  tests/test_loop_min_rounds_and_append.py \
  tests/test_notification_outbox.py \
  tests/test_notification_replay_and_watch.py
uv run ruff check lab_agent tests
uv run mypy lab_agent
python3 -m compileall -q lab_agent tests

cd /home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp
uv run pytest -q tests/test_experiment_workflow_classification.py tests/test_experiments.py
uv run ruff check canvus_mcp tests
uv run mypy canvus_mcp
python3 -m compileall -q canvus_mcp tests
```

Full gate after focused green:

```bash
cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent
uv run pytest -q && uv run ruff check lab_agent tests && uv run mypy lab_agent
cd /home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp
uv run pytest -q && uv run ruff check canvus_mcp tests && uv run mypy canvus_mcp
cd /home/ntdm/dev/lap-in-the-loop
python3 scripts/check-workflow-contract-parity.py
git diff --check
```

## Success Criteria

- Phase 7 remains complete; Phase 8 7A is complete with #30–#36 closed and real adapters preserved as external child-plan gates.
- Exactly one additive Phase 8 migration numbered 010; migrations 008/009 and dirty notification test preserved unchanged.
- Approved, explicitly enabled dry-run lifecycle survives restart and produces durable execution, analysis, refs, knowledge/conflict lineage plus Browser projections.
- Flag-off, stale/missing authorization, unsupported reconciliation, cross-canvas access, and disabled-real selection produce zero prohibited adapter calls.
- Every mock output is typed and visibly non-measured. No mock artifact can satisfy real-submit or measured-evidence predicates.
- All focused/full tests, Ruff, mypy, compileall, workflow parity, and whitespace checks pass; final review ≥9.5/10 with zero Critical/High safety findings.

## Phase 8 Closure Evidence

**Validation run date:** 2026-07-23

- **Phase 8/focused lab suite:** 133 passed (contracts, persistence, reconciliation, watcher, Browser, approval, intent, artifact, loop, and notification regressions).
- **Full lab-agent:** 684 passed, 4 existing dependency deprecation warnings.
- **Full Canvus-MCP:** 163 passed, 3 existing dependency deprecation warnings.
- **Code quality:** Ruff clean; mypy clean; compileall clean; workflow parity self-test clean; `git diff --check` clean.
- **Final review:** 9.8/10, zero Critical/High/Medium/Low findings. Prior blockers fixed. Concurrent analysis regression fixed and passing.

**Scope delivered:** 7A mocks/contracts-only. Typed execution/analysis/artifact/knowledge/conflict contracts; migration 010 with five append-only tables; deterministic dry-run adapters; fail-closed disabled-real sentinels; restart-safe idempotent lifecycle and reconciliation; atomic concurrent retry claim; manual activation preserved in Phase 8 watcher handoff; terminal intent recovery; opaque mock/opaque artifact refs and Browser location redaction; display-only Canvus buckets; legacy multi-round mock loop preserved. Real Flywheel/HPC, knowledge store, lab/robot adapters remain external child-plan gates. `execution_enabled` remains false and no wet-lab action is reachable.

**External gates remain open:** Phase 7 production in-silico and identity readiness; 7B Flywheel/HPC real API/auth/sandbox/SLA/reconciliation/rollout; 7C knowledge-store authoritative version/conflict/retention/access policy; 7D lab/robot hardware/production-identity/safety/sandbox/rollout.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Mock path is presented or reused as real measured execution | Medium | Critical | EvidenceKind invariant, dry-run-only factory, disabled-real sentinel, capture-receipt predicate, visible labels, negative tests |
| Ambiguous submit duplicates external work | Medium | Critical | Persist intent/run before call; explicit ambiguous/reconciling state; authoritative lookup before any retry; block when unsupported |
| Concurrent work corrupts migration/order or shared watcher changes | High | High | Preserve 008/009 and notification test; #32 sole shared-file integrator; status/diff ownership check before edit |
| Near-limit files become unmaintainable | High | Medium | ExecutionStoreMixin, lifecycle modules, watch delegate, extracted Browser registry; ≤200-line gate where practical |
| Raw data, secret, capability URL, or provider error leaks to SQLite/audit/Browser | Medium | Critical | Bounded typed refs, logical URIs/digests only, SecretStr boundary, safe error codes, redaction/security tests |
| Knowledge history is overwritten or conflict auto-resolved | Low | High | Insert-only versions/conflicts, immutable hashes/provenance, no resolution API, DB/test assertions |
| Real-provider discovery later invalidates schema | Medium | High | Keep 7A provider-neutral and minimal; child plans own 011+ migrations and revised estimates |

## Security and Compatibility

- Recheck exact proposal and validation hashes plus ordered durable approvals immediately before every submit/rerun. Canvas text, markers, topology, prior approval status, and model output are non-authorizing.
- Unknown classification/locality is deny. Persist no credentials, raw bodies, prompts, tokenized URLs, capability tokens, or arbitrary provider errors.
- Every row and lookup is canvas-scoped. Cross-canvas refs/versions/conflicts fail closed.
- No existing data backfill or Canvas rewrite. Legacy Note/Browser artifacts remain readable; legacy `run_on_robot` remains clearly mock compatibility and is not relabeled real.
- Rollback disables the Phase 8 flag and restores the old watcher handoff while retaining additive tables, audit, refs, and artifacts. Physical/non-reversible history is never deleted to simulate rollback.

## Next Steps / External Gates

- #30–#36 completed; Phase 8 7A complete.
- Phase 8 enables Phase 9 production hardening.
- Real Flywheel/HPC, knowledge-store, and lab/robot work requires separate approved child plans. Their unknown APIs, owners, retention, safety, identity, locality, and operational semantics are intentional external gates, not 7A acceptance failures.
- Unresolved 7A questions: none. Real conflict semantics, retention policy, operator identity, and provider contracts belong to 7B–7D discovery.
- Docs impact: major, completed in #36 (README, architecture, roadmap, changelog, code-standards updated with 7A scope, adapter tiers, migration 010 baseline, external gates).
