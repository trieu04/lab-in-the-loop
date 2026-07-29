# Development Roadmap

## Architecture direction note (proposed, pending owner confirmation)

A 2026-07-16 architecture-recovery review recommended evolving Lab-in-the-Loop toward an independent, provider-neutral harness/orchestrator, with the Claude Code skill as an optional interface only (see [system architecture](system-architecture.md) → "Target harness boundary"). Phases 3–7 below have been strengthened to reflect that direction. **This is a proposed direction, not an approved plan** — the source review's architecture-choice prompt was interrupted and never explicitly ratified by the project owner. None of the phases below are marked complete on the strength of that review alone.

## Status snapshot

| Area | Status | Notes |
|---|---|---|
| Repo split | Complete | New repo initialized at `~/dev/lab-in-the-loop` on branch `main`; git history exists (`git log`) — the extraction is a committed release, not untracked files on disk |
| `canvus-mcp` migration | Complete | Source/tests/docs copied, generated artifacts excluded |
| `lab-agent` migration | Complete | Source/tests/docs copied, generated artifacts excluded |
| Full docs initialization | Complete | README + architecture/workflow/setup/integration/standards/changelog |
| Test verification | Complete for recorded local gates | Phase 1/2 baselines remain recorded below. The 2026-07-18 Phase 4 final temper/review records `lab-agent` 314/314 tests passed, focused `canvus-mcp` marker tests 8/8 passed, `ruff`/`mypy` clean, workflow parity clean, and reviewer score 9.6/10 SEALED. Live external Canvus/TLS gates remain pending. |
| Durable harness core (local-disk, single-host) | Complete | SQLite WAL ledger: attempts/retry/quarantine, single-writer canvas leases, side-effect intent recovery, hash-chained audit log, operator commands; see Phase 3 below and [system architecture](system-architecture.md) → "Durable harness core" |
| Generated artifact Browser service | Delivered for local/source gates; deployment gates pending | Setup/Result/Closed and generated Needs Input status/prompt artifacts use Browser widgets backed by `ArtifactStore`; legacy Notes stay readable. Formal temper/review is sealed; live external reachability and production TLS/private-ingress validation remain operational gates. |
| Grounding/evidence gate | Complete | Per-run bounded evidence ledger, deterministic source ids, citation validation before writes, untrusted-data envelope, approved acronym dictionary scan across idea/setup/evidence excerpts, explicit Needs Input for insufficient evidence/ambiguity, and metadata-only durable audit. Future wiki/KG/vector sources remain adapters/external gates. |
| Governance and model routing | Complete for local/source gates | Provider-neutral task-stage routing; pre-dispatch locality/known-pricing authorization; exact/estimated usage; restart-safe reservations/intents; typed retry/reconciliation; and terminal stop closures. Final local verification: `lab-agent` 406/406, `canvus-mcp` 37/37, governance matrix 131/131 across four runs without flakes, endpoint suite 15/15, reviewer cycle 3 9.7/10 SEALED. Organization-approved endpoint/classification matrix, price maintenance, invoice reconciliation, and live SDK/API checks remain operational gates. |
| Resumable local-source ingestion | Complete for local/source gates (2026-07-19) | Separate SQLite/WAL ingestion ledger and protected cache; SHA-256/extractor-version dedup; standalone leased worker component; bounded local extractors; authenticated canvas-scoped status/chunk reads; evidence/locality integration. No deployment approval, worker CLI, queue/object store, automatic retention, hosted CI, live credentials, or large-format validation is claimed. |
| Phase 7 validation and approval gates | Complete for local/source infrastructure (2026-07-23) | Typed canonical `litl-canonical-json-v1` SHA-256 proposal/result hashes; deterministic dry-run validation visibly not scientific; canvas-scoped append-only validation/approval evidence; credential-verified scientist then lab-lead gates; Browser projections only. |
| Execution modes, terminal stops, and SMTP outbox | Complete for local/source gates (2026-07-23) | Mode parsing, fail-closed connected unknown modes, gate-safe staged multi-round continuation, terminal events, and durable SMTP outbox are locally verified. The loop remains mock/dry-run only, while real identity, scientific validation, hardware, and external SMTP operations remain gates. |
| Phase 8 milestone 7A — dry-run lifecycle contracts | Complete for contracts/mocks only (2026-07-23) | Typed execution/analysis/knowledge contracts, five append-only tables, deterministic memory-only adapters, approval-bound restart-safe lifecycle, and safe Browser projections. `phase8_execution_enabled=false`; all 7A output is **DRY RUN / MOCK — NOT MEASURED**. 7B real Flywheel/HPC, 7C real knowledge store, 7D real lab/robot, production identity/credential approval, retention/locality policy, and hosted integration remain external gates. |
| Real robot integration | Future | Neither Phase 7 nor Phase 8 7A provides real robot/lab execution; no hardware/lab SDK exists. |
| Flywheel / real in-silico integration | Future | Phase 7 implements deterministic/durable gate infrastructure, not a real scientific adapter or Flywheel integration. |

## Phase 0 — Repository extraction

**Status:** Complete

Goal: move Lab-in-the-Loop work out of `rag-canvus` into a standalone repo.

Completed:

- Created `~/dev/lab-in-the-loop`.
- Initialized git with `main` branch and committed the extraction (`git log` shows the extraction commit and subsequent history — this is a committed release, not untracked files on disk).
- Copied `apps/canvus-mcp` excluding `.env`, `.venv`, caches, downloads.
- Copied `apps/lab-agent` excluding `.env`, `.venv`, caches.
- Copied full use-case doc.
- Preserved serving-side `{exp:}` patch under `integrations/`.
- Added root `.gitignore`.
- Added full documentation set.

Success criteria:

- No secrets copied.
- No virtualenv/cache/downloads copied.
- New repo has independent README/docs.

## Phase 1 — Local verification

**Status:** Complete

Goal: prove migrated code runs in the new path, and close the gaps a subsequent stabilization pass found (loop-detection false positive, defensive field fabrication, no cross-app contract check).

Tasks:

- Run `uv sync --extra dev` in `apps/canvus-mcp`.
- Run `uv run pytest -q` in `apps/canvus-mcp`.
- Run `uv run ruff check canvus_mcp tests`.
- Run `uv run mypy canvus_mcp`.
- Run `uv sync --extra dev` in `apps/lab-agent`.
- Run `uv run pytest -q` in `apps/lab-agent`.
- Run `uv run ruff check lab_agent tests`.
- Run `uv run mypy lab_agent`.
- Fix `detect_experiment_loops` to exclude the orchestrator's own round-advance edge (`round(setup) > round(result)`) from loop detection.
- Replace defensive field fabrication in the orchestrator with fail-closed schema validation (`SchemaValidationError`/`coerce_or_fail`).
- Add a `FakeMCP` live-recompute mode that exercises canvus-mcp's real detector from lab-agent's tests, and a regression test proving a two-round rescan yields no duplicate nodes and zero actionable loops.
- Add `scripts/check-workflow-contract-parity.py` to keep canvus-mcp's markers, lab-agent's markers, and the docs contract aligned without a runtime dependency between the two apps.

Success criteria:

- Tests pass or failures are documented with root cause — `canvus-mcp` 19/19, `lab-agent` 19/19, `ruff`/`mypy` clean for both apps, and `scripts/check-workflow-contract-parity.py` passes.
- Lint has no syntax/import failures.
- Lockfiles remain consistent.

## Phase 2 — Demo canvas operation

**Status:** Pending

Goal: run one end-to-end mock loop on a demo Canvus canvas.

Tasks:

- Start `canvus-mcp` with dev credentials.
- Register or directly target MCP URL.
- Create demo canvas with `RAGCluster_`, knowledge feeder, idea note, and `Robot_`.
- Run `lab-agent once --canvas <canvas-id>`.
- Confirm setup Browser artifact creation.
- Connect setup → robot.
- Run once again and confirm result Browser artifact creation.
- Connect result → setup.
- Run once/watch and confirm continue/stop behavior.

Success criteria:

- Canvas visibly shows setup/result/closed or next round.
- Generated Browser artifact payloads include the required model-readable first-line markers; legacy Notes still expose them directly.
- No duplicate setup/result on repeated scans.

## Phase 3 — Harness contracts and persistent loop state

**Status:** Partially complete — durable idempotency shipped; harness contracts remain Future

Goal: make loop idempotency durable across process restarts, and define the source-of-truth contracts (schemas, prompts, provider policy, output normalization) that a future independent harness would own instead of `lab-agent` alone — see [system architecture](system-architecture.md) → "Target harness boundary (proposed)".

### Durable idempotency — Complete

Implemented as a local SQLite (WAL-mode) ledger in `lab-agent` (see [system architecture](system-architecture.md) → "Durable harness core (Phase 2)" and [setup and operations](setup-and-operations.md) → durable-harness env vars/operator commands):

- Chosen approach: local SQLite state file (`lab_agent/state_store.py`, `lab_agent/state/*`), not canvas-graph-only inference — graph inference alone cannot durably distinguish "not yet processed" from "processed, but the process crashed before drawing the confirming connector."
- Retry/resume/audit semantics for a failed or interrupted watcher cycle: every derived trigger gets a `workflow_attempts` row (`pending → running → completed | failed | quarantined`) with full-jitter exponential backoff and quarantine after `LAB_AGENT_MAX_ATTEMPTS`; every attempt/lease/intent/operator action is appended to a hash-chained `audit_events` log.
- Side-effect (note/connector write) recovery: intent-before-mutation plus a live-canvas probe closes the crash window between "canvas write landed" and "marked done locally" without a duplicate write (`lab_agent/recovery.py`, `lab_agent/canvas_probe.py`, `lab_agent/durable_writes.py`).
- Single-writer-per-canvas enforcement via a leased `canvas_leases` row, so two concurrently-running `lab-agent` instances against the same canvas cannot race each other.
- Scope: local disk, single host. Multi-host/shared-store deployment is a distinct future trigger — see [system architecture](system-architecture.md) → "Local-disk, single-host scope, and the Postgres/multi-host trigger" and Phase 7 below.

Success criteria (met):

- Restarting `lab-agent` does not reprocess a `completed` trigger (proven by `apps/lab-agent/tests/test_durable_recovery_integration.py` and `test_durable_operations_integration.py` across restart boundaries and backup/restore).
- A crash between canvas write and local reconcile recovers via live probe with no duplicate note/connector.
- Manual canvas edits can still intentionally trigger a new round (canvas-graph derivation of "what needs doing" is unchanged; only the "have I already durably started/finished this" answer moved off in-memory state).

### Generated artifact Browser service — Delivered for local/source gates; deployment gates pending

This is the implementation-plan Phase 3 track, not the external GSK "Phase 3" terminology noted in the use-case spec. Source now supports generated Setup/Result/Closed and generated Needs Input status/prompt artifacts as capability-protected HTML Browser widgets backed by canonical, versioned `ArtifactStore` records. `{idea: ...}` and human-authored approval/review/input responses remain Notes, and legacy generated Notes remain readable during migration.

Supported by implementation:

- Browser widget identity and `/artifacts/{opaque_id}` resource path are stable; token-bearing capability URLs may rotate and are repaired with `update_browser` in place.
- Artifact service binds privately by default and serves `/healthz`, same-origin assets, CSP-protected HTML, and uniform 404s for unauthorized/revoked/wrong-canvas requests.
- Migration is dry-run by default and mirror-first on `--apply`; original generated Notes/connectors are not deleted or edited, and `--apply` is the operator's explicit confirmation for mirror writes.
- The shared SQLite state DB now contains workflow state plus artifact documents, versions, token hashes, and widget mappings, so backup/restore covers both.

Closed local/source gates:

- Final temper/review sealed on 2026-07-18: 293 tests passed (37 `canvus-mcp` + 256 `lab-agent`), `ruff`/`mypy`/workflow parity clean, reviewer score 9.6/10 SEALED.

Still pending operational deployment gates:

- Live operational validation that the configured artifact public base URL is reachable by intended Canvus clients.
- Production HTTPS/private-ingress ownership and TLS verification.

### Harness contracts — Future

Not yet started; still owned informally by `lab-agent` source, not a documented cross-implementation contract:

- Decide the authoritative owner of workflow schemas (`ExperimentSetup`/`ExperimentResult`/`LoopDecision`), prompts, and provider policy so the Claude Code skill, `lab-agent`, and the `canvus-serving` `{exp:}` action do not drift independently (see [canvus-serving integration](canvus-serving-integration.md)).

Success criteria:

- A single documented contract governs schemas/prompts/policy across all three current implementations.

## Phase 4 — Stronger grounding

**Status:** Complete — sealed 2026-07-18 (314/314 `lab-agent` tests; focused `canvus-mcp` marker tests 8/8; reviewer score 9.6/10 SEALED)

Goal: improve scientific context quality before setup generation using the current RagCluster/read-tool boundary, without requiring future wiki/KG/vector integrations.

Completed:

- Added a bounded per-run `EvidenceLedger` around successful read-tool results with deterministic source ids, content hashes, bounded in-memory excerpts, and citation membership validation.
- Wrapped retrieved content in an explicit `untrusted_data` envelope and kept model-facing tools read-only; writes remain orchestrator-owned.
- Extended `ExperimentSetup` additively with `hypothesis`, `success_criteria`, `constraints`, `confidence`, citations, evidence status, and ambiguity flags while preserving legacy parsing defaults.
- Validated evidence before setup writes: insufficient evidence becomes Needs Input; invalid/fabricated citations write nothing and remain retryable.
- Scanned original idea text, emitted setup fields, and all bounded evidence excerpts against an approved acronym dictionary; unknown/colliding terms are surfaced instead of guessed.
- Wrote explicit insufficient-evidence/ambiguity `[EXP:Needs Input]` Browser artifacts deduplicated by predecessor plus reason hash.
- Kept durable audit metadata-only (decision/reason/source ids/tool/hash), capped to the store's 4096-byte payload limit by trimming evidence rows.

Deferred/future:

- Internal wiki, knowledge graph, and vector retrieval sources are future adapters or external gates, not current hard dependencies.
- Acronym dictionary curation remains an operational/domain-owner concern before live scientific use.

Success criteria (met):

- Setups either cite internal notes/PDFs/widgets through valid ledger source ids or produce an explicit Needs Input/invalid-citation outcome before any setup write.
- Ambiguous terms are visible through Needs Input, not silently guessed.

## Phase 4b — Token/resource governance and model routing

**Status:** Complete for local/source gates — Phase 5 implementation verified 2026-07-19: `lab-agent` 406/406, `canvus-mcp` 37/37, governance matrix 131/131 across four runs without flakes, endpoint suite 15/15, reviewer cycle 3 9.7/10 SEALED; external approval/operations remain pending

Goal: give the harness/orchestrator control over cost, provider selection, and where data is allowed to flow.

Completed:

- Added provider-neutral task-stage routing over the existing `openai`/`claude` adapter factory. `setup`, `mock_result`, and `loop_decision` use a configuration-only ordered provider preference with locality-aware pre-dispatch fallback.
- Added fail-closed evidence classification and endpoint authorization before provider dispatch. Credential-gated canonical OpenAI/Claude endpoints are defaults; explicit endpoint configuration controls the SDK destination; unknown/custom providers remain denied.
- Added normalized `exact`/`estimated`/`unavailable` usage. Claude exact input includes cache-creation/read tokens; estimates conservatively cover provider-visible messages, tools, response schema, schema name, and output cap, and are not invoices.
- Added versioned model-price checks and durable SQLite reservations for per-trigger run and per-canvas token/cost envelopes. Unknown pricing denies dispatch even without a numeric cost cap; a trigger resets its run accounting while canvas totals/active holds survive restart.
- Added durable model-call intents, idempotent reserve/commit/release, bounded typed pre-submission retries, capability-aware submitted/ambiguous reconciliation, and no blind redispatch.
- Added distinct round, token, cost, wall-time, no-progress, locality, and reservation terminal closures, rendered/audited before any later provider or canvas write.

Success criteria (met locally):

- A run can be capped by token/cost budget, not only round count.
- Model/provider choice can vary per task type without orchestration code changes.
- A governed request cannot leave the process without locality authorization, known pricing, a durable intent, and a reservation.

Still operational/external:

- The organization must approve and maintain the provider endpoint/classification matrix and versioned model prices.
- Live provider SDK/API compatibility, endpoint reachability, price/invoice reconciliation, and production policy approval are not proven by local tests.

## Phase 4c — Async multimodal ingestion

**Status:** Complete for local-source implementation — 2026-07-19; deployment and capacity policy remain external gates

Goal: support bounded local PDF/image/table/text ingestion without blocking the loop on one opaque model call (UC §11).

Completed:

- Added a separate single-host SQLite/WAL ingestion ledger with immutable SHA-256-checked migrations for assets, canvas-scoped sources, jobs, deterministic units, leases, attempts, chunks, and cancellation state.
- Made source identity SHA-256 and derived-work identity SHA-256 plus extractor version; unchanged content reuses work while a new extractor version creates new derived work.
- Added byte-counted streaming acquisition, a protected ignored raw cache (`0700` directories, `0600` files, descriptor-confined no-follow access), and no model-visible raw bytes, paths, or capability URLs.
- Added bounded strict UTF-8 text, CSV/TSV, JSON, image-metadata, and PDF-page extraction with typed malformed/encrypted/oversized/unsupported outcomes.
- Added a standalone leased worker component with renewal, stale-generation rejection, atomic chunk/completion, retry/backoff, poison, cancellation, graceful stop, and restart reclaim. The MCP server does not start it and no worker console command is registered.
- Added static reader/trusted-service/operator roles, exact canvas allowlists, strict Bearer ingestion calls, reader-only stdio default, bounded status/chunk reads, and metadata-only denial audit. Existing non-ingestion anonymous reads/downloads remain compatible.
- Added exact ingestion read allowlisting to `lab-agent`: status is operational/non-citeable; chunks become bounded untrusted evidence and Phase 5 locality applies before every later provider call.

Final local validation sealed: `canvus-mcp` full suite **134 passed** and focused suite **77 passed**; `lab-agent` full suite **480 passed** and focused suite **122 passed**. Ruff, mypy, compileall, lockfile checks, package builds, workflow parity, tracked/untracked whitespace checks, and Phase 7 isolation passed. Real local streamable-HTTP authorization and subprocess crash/restart proofs passed. Final inspection is **9.7/10**, `criticalCount: 0`, `decision: SEALED`. Known deprecation warnings remain. Statement/branch coverage is unclaimed because authoritative hosted/locked-environment coverage tooling was unavailable; no ephemeral coverage result is treated as final evidence.

Still external/operator-owned:

- Hosted CI, live credentials, and live Canvus deployment validation.
- Large-format/video/non-CSV/TSV spreadsheet extractor validation; video and those spreadsheet formats remain unsupported/external gates.
- Cache/DB retention, operator capacity policy, backups, and disk monitoring. There is no automatic cleanup.
- Any external queue/object-store migration. Trigger it only from measured sustained backlog/throughput, disk pressure, availability/SLO failure, or a multi-host requirement; no distributed implementation or numeric threshold exists today.

Success criteria met locally:

- Interrupted work resumes unfinished units without redoing completed chunks, and progress/status is readable without scheduling work.
- Re-enqueueing unchanged content reuses its job/cache; a new extractor version invalidates only derived work.
- Model context sees only bounded extracted chunk evidence and scalar provenance, never raw bytes or local storage/capability details.

## Phase 5 — In-silico validation gate

**Status:** Complete for Phase 7 local/source gate infrastructure (2026-07-23); real scientific adapter remains Future

Goal: add a fail-closed validation step before any eligible execution state.

Workflow target:

```text
AI Experiment Design
→ In Silico Simulation
→ Predicted Outcome + Confidence
→ Proceed / Revise / Reject
→ Human Approval
→ Lab Execution
```

Completed Phase 7 local/source infrastructure:

- Typed `InSilicoRequest`/`InSilicoResult` contracts use canonical SHA-256 hashes under `litl-canonical-json-v1`.
- The deterministic local adapter returns structured predicted outcome, confidence, uncertainty, assumptions, risk flags, recommended changes, and proceed/revise/reject. Its output is explicitly not scientific validation; the real adapter is disabled/unimplemented and fails closed.
- Validation and approval evidence is append-only SQLite state scoped to the canvas. Retries/recovery/restarts and stable approval replay are covered; stale proposal/result evidence is retained but non-authorizing.
- Browser validation and approval-status artifacts are projections only. Canvas Notes, titles, connectors, and author text cannot approve.
- `wet_lab_execution_enabled` defaults false, so the watcher does not process setup-to-robot mock execution or call `run_on_robot`. Governed result-to-setup loop decisions remain separate from this execution switch.

Still Future:

- A real scientific/digital-twin adapter and its operational/provider approvals.
- Any hardware, robotic-lab, wet-lab, Flywheel, or laboratory SDK integration.

## Phase 6 — In-silico, human approval, Flywheel, and lab integration

**Status:** Partially complete — Phase 7 durable scientist/lab-lead approval gates delivered; Flywheel and real lab integration remain Future

Goal: replace mock robot results with real execution/result ingestion where authorized, gated by explicit review and approval.

Approval sequence (this repo's canonical ordering — see the [original meeting vision](notes/use-case-lab-in-the-loop.md) §8 and the [canonical use case specification](lab-in-the-loop-use-case-specification.md) for the reconciled gate ordering):

```text
AI design → in-silico validation → scientist review → lab lead approval → wet lab
```

Candidate integrations:

- robotic lab scheduler;
- Flywheel data upload event;
- micro-CT segmentation/quantification gear;
- HPC/R/Python analysis pipeline;
- knowledge update/versioning service.

Safety requirements:

- implemented: credential-verified scientist review then lab-lead approval, both bound to current durable validation/proposal evidence;
- implemented: append-only evidence/audit and deterministic dry-run mode visibly distinguished from scientific validation;
- implemented: no autonomous execution by default (`wet_lab_execution_enabled=false`);
- Future: real-adapter operational controls, hardware abort/rollback, and a real laboratory execution boundary.

Success criteria:

- Complete locally: no eligible execution state can be projected without current scientist-review then lab-lead approval evidence.
- Future: Real result artifacts link back to setup id and round.
- Future: Analysis output is ingested into canvas/knowledge store.
- Future: Next-round decision uses measured results, not mock data.
- Future: A real wet-lab step runs only through a reviewed hardware/lab integration after explicit operational authorization.

## Phase 6b — Execution modes, terminal stop governance, and durable notifications

**Status:** Complete for local/source infrastructure (2026-07-23); the legacy multi-round loop is gate-safe through staged successor Setup artifacts, while Phase 8 7A separately provides dry-run-only contracts. External identity, scientific, hardware, and SMTP operations remain gates

Completed:

- Added exact `{idea: ...}` legacy/manual and `{idea+auto: ...}` automatic mode markers. Unsupported mode tokens are visible and fail closed into one Needs Input request.
- Added deterministic in-silico setup validation, proposal/result hash binding, ordered credential-verified scientist → lab-lead approval, and first-round manual activation. Auto mode bypasses only that extra activation pause; it never bypasses validation, approvals, freshness, locality, budgets, or execution enablement.
- Hardened genuine terminal stops so they create one Closed artifact and sanitized `loop_stopped` event with no post-terminal writes. The legacy CONTINUE branch now stages one successor Setup per decision and routes each changed proposal through fresh validation and ordered approval; Phase 8 7A remains a separate approval-bound dry-run lifecycle and neither branch claims real execution or measured evidence.
- Added durable SQLite terminal-notification outbox, deterministic logical key/Message-ID, TLS-only SMTP, allowlists, retry/quarantine/ambiguous reconciliation, indexed bounded drains, and safe notification CLI commands. Delivery is best-effort and logically deduplicated; only known pre-submit/transient failures retry automatically, while ambiguous outcomes require reconciliation. No exactly-once or at-least-once inbox guarantee is claimed.

Verification:

- Final current-tree matrix: `lab-agent` 697 passed (4 warnings); `canvus-mcp` 165 passed (3 warnings). Ruff, mypy, compileall, workflow-contract parity, Markdown-link validation, and whitespace checks passed.
- Local integration matrix covers manual activation/restart, auto approval gates, unknown-mode fail-closed behavior, bounded per-canvas delivery, quarantine/reset, ambiguity reconciliation, and metadata redaction.

Not claimed:

- No production identity provider, real scientific adapter, hardware/robot/lab SDK, live Canvus/TLS deployment, or live SMTP credential/recipient operation is claimed.

## Phase 7 — Production hardening, multi-user deployment, and observability

**Status:** Future

Tasks:

- Add structured logs with run ids.
- Add metrics: scans, setups created, results created, decisions, failures.
- Add health checks for MCP and model providers.
- Add integration tests with a fake Canvus/MCP server.
- Package both apps for deployment.
- Add CI.
- Support multiple concurrent users/canvases with per-user/per-canvas isolation of loop state and credentials.
- Add observability (dashboards/alerts) for watcher liveness, error rate, and per-canvas loop progress across users.

Success criteria:

- Repo can be tested in CI.
- Watcher failures are observable.
- Deployment docs cover restart and recovery.
- Multiple users/canvases can run concurrently without state or credential leakage between them.

## Phase 8 — Contracts-only external lifecycle foundation (7A)

**Status:** Complete for 7A contracts/mocks only — sealed 2026-07-23; no real execution or measured scientific truth

Goal: establish a typed, durable, approval-bound lifecycle seam for later external execution, analysis, and knowledge work without installing any real provider, lab, network, or data-retention integration.

Completed in 7A:

- Added frozen typed contracts for run mode, evidence kind, external statuses/failure codes, execution/analysis runs, opaque artifact refs, measured-evidence receipts, knowledge versions, and conflicts. The measured-evidence type boundary requires a real mode plus capture receipt; 7A produces only `mock_or_dry_run` evidence.
- Added immutable migration `010_execution_analysis_knowledge.sql` with append-only `execution_runs`, `analysis_runs`, `artifact_refs`, `knowledge_versions`, and `conflict_records`. Existing `side_effect_intents` keeps submit/abort identity; `workflow_attempts` keeps watcher scheduling, retry/backoff, and quarantine.
- Added deterministic memory-only lab, Flywheel, and knowledge adapters plus disabled-real sentinels. All 7A outputs are visibly **DRY RUN / MOCK — NOT MEASURED**. No real provider API, network, robot, wet-lab, Flywheel/HPC, knowledge store, credential, raw provider text, capability URL, or measured evidence is implemented.
- Added approval-bound, restart-safe orchestration: exact current proposal/validation hashes and ordered approvals are rechecked, first-round manual mode stays hash-bound, a durable atomic claim prevents duplicate concurrent submit, and submitted/ambiguous runs reconcile authoritatively before retry. Terminal recovery settles the matching intent without rewriting append-only history.
- Added safe Browser projections for execution, analysis, knowledge, and conflict records. They expose safe ids/hashes/status/lineage and omit `logical_uri`; Canvus buckets remain display-only and never authorize or schedule work. The legacy multi-round synthetic mock loop remains compatible.
- Kept `LAB_AGENT_PHASE8_EXECUTION_ENABLED=false` and `LAB_AGENT_PHASE8_EXECUTION_MODE=dry_run` as the safety defaults; `sandbox` and `real` fail closed.

Verification:

- Focused Phase 8 suite: **133 passed**.
- Full suites: `lab-agent` **684 passed** with **4 existing dependency deprecation warnings**; `canvus-mcp` **163 passed** with **3 existing dependency deprecation warnings**.
- Ruff, mypy, compileall, workflow-contract parity, and `git diff --check` were clean. Final reviewer re-review: **9.8/10**, zero findings.

External child-plan gates (not part of 7A):

- **7B:** real Flywheel/HPC analysis.
- **7C:** real knowledge store.
- **7D:** real lab/robot execution.
- Production identity/credential approval, retention/locality policy, and hosted integration.