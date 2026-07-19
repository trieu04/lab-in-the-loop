# Development Roadmap

## Architecture direction note (proposed, pending owner confirmation)

A 2026-07-16 architecture-recovery review recommended evolving Lab-in-the-Loop toward an independent, provider-neutral harness/orchestrator, with the Claude Code skill as an optional interface only (see [system architecture](system-architecture.md) → "Target harness boundary"). Phases 3–7 below have been strengthened to reflect that direction. **This is a proposed direction, not an approved plan** — the source review's architecture-choice prompt was interrupted and never explicitly ratified by the project owner. None of the phases below are marked complete on the strength of that review alone.

## Status snapshot

| Area | Status | Notes |
|---|---|---|
| Repo split | Complete | New repo initialized at `~/dev/lap-in-the-loop` on branch `main`; git history exists (`git log`) — the extraction is a committed release, not untracked files on disk |
| `canvus-mcp` migration | Complete | Source/tests/docs copied, generated artifacts excluded |
| `lab-agent` migration | Complete | Source/tests/docs copied, generated artifacts excluded |
| Full docs initialization | Complete | README + architecture/workflow/setup/integration/standards/changelog |
| Test verification | Complete for recorded local gates | Phase 1/2 baselines remain recorded below. The 2026-07-18 Phase 4 final temper/review records `lab-agent` 314/314 tests passed, focused `canvus-mcp` marker tests 8/8 passed, `ruff`/`mypy` clean, workflow parity clean, and reviewer score 9.6/10 SEALED. Live external Canvus/TLS gates remain pending. |
| Durable harness core (local-disk, single-host) | Complete | SQLite WAL ledger: attempts/retry/quarantine, single-writer canvas leases, side-effect intent recovery, hash-chained audit log, operator commands; see Phase 3 below and [system architecture](system-architecture.md) → "Durable harness core" |
| Generated artifact Browser service | Delivered for local/source gates; deployment gates pending | Setup/Result/Closed and generated Needs Input status/prompt artifacts use Browser widgets backed by `ArtifactStore`; legacy Notes stay readable. Formal temper/review is sealed; live external reachability and production TLS/private-ingress validation remain operational gates. |
| Grounding/evidence gate | Complete | Per-run bounded evidence ledger, deterministic source ids, citation validation before writes, untrusted-data envelope, approved acronym dictionary scan across idea/setup/evidence excerpts, explicit Needs Input for insufficient evidence/ambiguity, and metadata-only durable audit. Future wiki/KG/vector sources remain adapters/external gates. |
| Governance and model routing | Complete for local/source gates | Provider-neutral task-stage routing; pre-dispatch locality/known-pricing authorization; exact/estimated usage; restart-safe reservations/intents; typed retry/reconciliation; and terminal stop closures. Final local verification: `lab-agent` 406/406, `canvus-mcp` 37/37, governance matrix 131/131 across four runs without flakes, endpoint suite 15/15, reviewer cycle 3 9.7/10 SEALED. Organization-approved endpoint/classification matrix, price maintenance, invoice reconciliation, and live SDK/API checks remain operational gates. |
| Real robot integration | Future | Mock-only for now |
| Flywheel/in-silico gates | Future | Documented, not implemented |

## Phase 0 — Repository extraction

**Status:** Complete

Goal: move Lab-in-the-Loop work out of `rag-canvus` into a standalone repo.

Completed:

- Created `~/dev/lap-in-the-loop`.
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

**Status:** Future

Goal: support PDF/image/video/table ingestion at scale without blocking the loop on one large model call (UC §11).

Tasks:

- Chunk large documents/media instead of sending them whole to one model call.
- Cache extracted/summarized content so repeated scans do not re-process unchanged assets.
- Make ingestion resumable and track progress for long-running extractions.
- Add modality-specific extraction (PDF text, image/video description, tabular data) ahead of setup generation.

Success criteria:

- A multi-day/large-asset ingestion case can resume after interruption without redoing completed chunks.
- Ingestion progress is observable, not a single opaque long-running call.

## Phase 5 — In-silico validation gate

**Status:** Future

Goal: add a digital-twin/in-silico step before wet lab execution.

Workflow target:

```text
AI Experiment Design
→ In Silico Simulation
→ Predicted Outcome + Confidence
→ Proceed / Revise / Reject
→ Human Approval
→ Lab Execution
```

Success criteria:

- Setup can be routed to an in-silico validation node.
- Result includes predicted outcome, assumptions, risk flags, and recommendation.
- Wet-lab execution remains blocked until approval gate is explicit.

## Phase 6 — In-silico, human approval, Flywheel, and lab integration

**Status:** Future

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

- explicit scientist review and lab-lead approval before wet-lab execution;
- audit log;
- dry-run mode;
- rollback/abort path;
- no autonomous wet-lab execution without durable authorization.

Success criteria:

- Real result artifacts link back to setup id and round.
- Analysis output is ingested into canvas/knowledge store.
- Next-round decision uses measured results, not mock data.
- No wet-lab step runs without a recorded scientist-review and lab-lead-approval decision.

## Phase 7 — Production hardening, multi-user deployment, and observability

**Status:** Future

Tasks:

- Add structured logs with run ids.
- Add metrics: scans, setups created, results created, decisions, failures.
- Add health checks for MCP and model providers.
- Add integration tests with a fake Canvus/MCP server (durable-harness crash/recovery tests already exist per-canvas, see Phase 3; this item covers MCP-transport-level fakes).
- Package both apps for deployment.
- Add CI.
- Support multiple concurrent users/canvases with per-user/per-canvas isolation of loop state and credentials. This is the trigger for migrating the durable harness off local SQLite to a shared server-backed store — see [system architecture](system-architecture.md) → "Local-disk, single-host scope, and the Postgres/multi-host trigger".
- Add observability (dashboards/alerts) for watcher liveness, error rate, and per-canvas loop progress across users.

Success criteria:

- Repo can be tested in CI.
- Watcher failures are observable.
- Deployment docs cover restart and recovery.
- Multiple users/canvases can run concurrently without state or credential leakage between them.
