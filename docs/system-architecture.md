# System Architecture

## Overview

Lab-in-the-Loop turns a Canvus canvas into an experiment-control surface. The canvas stores knowledge scopes, ideas, generated artifact Browser widgets, legacy generated Notes, mock robot results, and loop closure decisions. The system has two runtime apps:

1. `canvus-mcp` — MCP server exposing Canvus operations as model-callable tools.
2. `lab-agent` — watcher/orchestrator that drives the experiment loop through those tools.

The design deliberately separates reads from writes:

- Model/agentic phase: read canvas state, retrieve grounding context, reason over setup/result text, emit structured decisions.
- Orchestrator phase: create user/legacy Notes, generated artifact Browser widgets, and connectors; enforce idempotency; and keep loop state deterministic.

## Current MVP vs. target harness (status note)

Everything below this line up to "Current limitations" describes the **current implementation** as it exists in code today: two runtime apps (`canvus-mcp`, `lab-agent`), OpenAI/Claude adapter-factory model choices, mock robot execution, generated Setup/Result/Closed plus generated Needs Input Browser artifacts backed by canonical versioned `ArtifactStore` records, a bounded per-run evidence ledger with citation/ambiguity gates before setup writes, and a durable, restart-safe local SQLite ledger for loop idempotency, retry/quarantine, single-writer canvas leasing, artifact records, and audit history (see "Durable harness core (Phase 2)", "Generated artifact Browser service (Phase 3 implementation)", and "Grounding/evidence gate (Phase 4)" below).

A 2026-07-16 architecture-recovery review (recovered from a separate `rag-canvus` session; see [project changelog](project-changelog.md)) recommends evolving this into an **independent, provider-neutral harness/orchestrator**, with the Claude Code skill/MCP registration as an optional developer/operator/demo interface rather than the runtime or source of truth for workflow logic, policy, grounding, and schemas. That recommendation is the session's final conclusion but was **not explicitly ratified by the project owner** — the architecture-choice prompt that would have confirmed it was interrupted. Treat it as **proposed direction**, not an implemented architecture. See "Target harness boundary (proposed)" below and the [roadmap](development-roadmap.md) for the phases this implies.

## Component map

```text
Canvus Server
  │
  │ REST API via canvus-sdk
  ▼
apps/canvus-mcp
  ├─ exposes MCP tools over streamable HTTP
  ├─ scans widgets/connectors
  ├─ reads notes/widgets/assets/PDFs
  ├─ detects experiment workflow state
  └─ creates/updates Notes, Browsers, and connectors on behalf of orchestrators
  │
  │ MCP streamable HTTP
  ▼
apps/lab-agent
  ├─ polls scan_experiment_workflow
  ├─ dispatches pending workflow steps, leased via a durable SQLite ledger
  ├─ grounds model using read-only tools and a per-run evidence ledger
  ├─ validates citations, sufficiency, and ambiguity before setup writes
  ├─ writes setup/result/closed and generated needs-input nodes as crash-safe Browser artifacts
  ├─ serves capability-protected artifact HTML
  └─ decides continue/stop through the configured model adapter
  │
  ├─ OpenAI adapter
  ├─ Claude adapter
  └─ local SQLite WAL ledger (.state/lab_agent.db) — attempts, leases,
     side-effect intents, artifact documents/tokens/widgets, hash-chained audit log
```

## Runtime apps

### `apps/canvus-mcp`

Purpose: one pre-configured bridge from a trusted process to a Canvus server.

Key modules:

| Module | Responsibility |
|---|---|
| `canvus_mcp/server.py` | FastMCP app and transport entrypoint |
| `canvus_mcp/config.py` | Environment-backed settings |
| `canvus_mcp/client.py` | Shared Canvus SDK client lifecycle |
| `canvus_mcp/ragcluster.py` | Connector index and RagCluster graph analysis |
| `canvus_mcp/experiments.py` | Experiment node classification and loop detection |
| `canvus_mcp/tools/scan.py` | Canvas/server scanning tools |
| `canvus_mcp/tools/content.py` | Note/widget/PDF/image/asset reads and downloads |
| `canvus_mcp/downloads.py` | Sniffs mime/suffix and writes downloaded bytes to `CANVUS_MCP_OUTPUT_DIR`, returning path/mime/size/sha256 metadata instead of inlining bytes |
| `canvus_mcp/tools/widgets.py` | Note/browser/image/connector writes |
| `canvus_mcp/tools/connections.py` | Widget and RagCluster connection summaries |
| `canvus_mcp/tools/experiments.py` | `scan_experiment_workflow`, `detect_experiment_loops` |

The MCP server owns Canvus credentials. Claude Code or `lab-agent` only needs the MCP URL.

### `apps/lab-agent`

Purpose: model-agnostic experiment-loop runner.

Key modules:

| Module | Responsibility |
|---|---|
| `lab_agent/cli.py` | CLI commands: `once`, `watch`, durable operator commands, `serve-artifacts` |
| `lab_agent/config.py` | MCP URL, provider, model, bounds |
| `lab_agent/mcp_client.py` | MCP transport client |
| `lab_agent/watch.py` | Poll loop and pending-trigger dispatcher |
| `lab_agent/orchestrator.py` | Mock robot result, loop continuation/closure, and next-round setup grounding via `orchestrator_setup.generate_setup` |
| `lab_agent/orchestrator_setup.py` | Idea/next-focus → setup entry point; creates a fresh `EvidenceLedger`, evaluates grounding with original idea text, writes setup only when executable, writes Needs Input for insufficient evidence/ambiguity, and leaves invalid citations retryable with no write |
| `lab_agent/grounding.py` | Phase 4 grounding gate: evidence sufficiency, citation membership, dictionary-backed acronym boundary scan, durable grounding audit, and Needs Input dispatch |
| `lab_agent/evidence.py` | Per-run bounded evidence ledger with deterministic source ids, bounded excerpts, citation validation, and minimal audit rows |
| `lab_agent/acronyms.py` | Approved acronym dictionary loader and acronym-like term detection; unknown/colliding terms stay unresolved |
| `lab_agent/models/evidence.py` | `EvidenceCitation`, `AcronymFlag`, `EvidenceStatus`, `GroundingDecision` |
| `lab_agent/orchestrator_support.py` | Stage helpers/re-export surface the orchestrator delegates to: fail-closed schema validation (`coerce_or_fail`/`SchemaValidationError`, `_emit_validated` — never fabricates missing fields), per-stage emit (`ground_and_emit_setup`, `emit_result`, `emit_decision`) and write (`write_setup_node`, `write_result_node`, `write_closed_node`, re-exported `write_needs_input_node`) helpers, round→version formatting (`version`), `LoopSummary`; generated writes delegate to Browser artifact durable writes |
| `lab_agent/orchestrator_needs_input.py` | On-demand `[EXP:Needs Input]` write stage; writes only the generated prompt/status Browser artifact with `NEEDS_REVIEW` artifact state, deduplicated by predecessor plus reason hash. The human response remains a separate Note |
| `lab_agent/nodes.py` | Canvas write surface: `create_node`, `create_artifact_widget`, `update_artifact_widget`, `connect`, `read_note_text` — user/legacy Notes use `create_note`; generated artifacts use `create_browser`/`update_browser`; write helpers raise `MCPToolError` on malformed/error/missing-id responses so failed writes are retried, not recorded as phantom successes |
| `lab_agent/tool_bridge.py` | Selects read-only tools exposed to the model, executes allowed reads, wraps successful results as `untrusted_data`, and records them in the ledger; write tools are rejected |
| `lab_agent/loop.py` | Tool-use loop and structured-output emission, threading the evidence ledger through setup grounding |
| `lab_agent/prompts.py` | System prompts for setup/result/decision phases, including untrusted-data and citation requirements for setup |
| `lab_agent/render.py` | Renders structured models into canvas-note text |
| `lab_agent/models/experiment.py` | `ExperimentSetup`, `ExperimentResult`, `LoopDecision`; setup includes additive Phase 4 evidence/citation/ambiguity fields with legacy-safe defaults |
| `lab_agent/models/states.py` | `DecisionState` enum (UC §12 lifecycle: `DRAFT` … `CLOSED`/`REJECTED`) written into note bodies as a `Status:` line |
| `lab_agent/adapters/*` | OpenAI and Claude model adapters; `factory.py` selects by `settings.model_provider` |
| `lab_agent/runtime.py` | Process-wide `RuntimeContext` (durable `StateStore` + a fresh-per-process `runtime_instance_id`), built once at CLI startup; fails closed on any integrity/audit-chain problem before canvas work starts |
| `lab_agent/state_store.py` | Public facade over the durable SQLite ledger — the only module CLI/orchestrator code is meant to call into for durable-harness reads/writes |
| `lab_agent/state/*` | Internal ledger implementation behind the facade: `connection.py` (WAL setup + versioned SQL migration runner with checksum drift detection), `attempts.py`/`attempts_retry.py` (workflow-attempt lifecycle: create/lease/complete, and backoff/quarantine/reset, split across two modules for the line budget), `leases.py` (single-writer canvas lease), `intents.py` (side-effect outbox rows), `audit.py` (hash-chained audit log), `edges.py` (`orchestrator_edges`), `models.py` (dataclasses/enums) |
| `lab_agent/recovery.py` | `idempotency_key`/`input_hash` and `reconcile_or_execute` — the generic intent-before-mutation, probe-before-create outbox algorithm |
| `lab_agent/canvas_probe.py` | Production `live_probe` callables (`probe_browser_by_tag`, `probe_note_by_tag`, `probe_connector`) that re-derive canvas state from MCP read tools to detect a crash-after-effect; fail closed on any MCP/parse error. Browser probes cover generated Setup/Result/Closed/Needs Input buckets; legacy Note probes still cover setup/result/closed notes via `scan_experiment_workflow` |
| `lab_agent/durable_browser.py` | Crash-safe generated-artifact Browser writes: require public base URL, persist/get artifact, issue token, create/repair Browser in place, map widget id, and connect it; later model reads pull text from `ArtifactStore` before falling back to legacy Notes |
| `lab_agent/artifact_store.py` | Public facade for canonical versioned generated artifacts, widget mappings, token issue/verify/rotate/revoke, and canvas-scoped lookup |
| `lab_agent/artifact_render.py` | Safe tabbed HTML rendering for artifact payloads, metadata, provenance, audit/version views |
| `lab_agent/artifact_http.py` | Artifact-service HTTP helpers: CSP/security headers, ETag handling, same-origin static asset loading, uniform 404 |
| `lab_agent/artifact_server.py` | Starlette ASGI app: `/artifacts/{opaque_id}?token=...`, same-origin assets, non-sensitive `/healthz` |
| `lab_agent/artifact_migration.py` / `artifact_migration_probe.py` | Dry-run/mirror-first legacy generated Note migration helpers; apply mode preserves original Notes/connectors |
| `lab_agent/durable_writes.py` | Legacy note/connector durable write helpers retained for compatibility and connector primitives |
| `lab_agent/intent_audit.py` | `reconcile_with_audit` (adds `intent_reconciled`/`intent_failed` audit events around `recovery.reconcile_or_execute`) and `connect_durable` (durable connector creation, records `orchestrator_edges`) |
| `lab_agent/admin.py` | Operator commands behind the CLI: `check_integrity`, `list_quarantined`, `reset_attempt`, `backup` |
| `lab_agent/migrations/*.sql` | Versioned schema migrations for the durable ledger and artifact tables (tracked in git; the ledger/artifact DB file itself is not — see `.gitignore`) |

### Durable harness core (Phase 2)

`lab-agent` now persists workflow progress to a local SQLite (WAL-mode) ledger instead of relying solely on in-memory state or canvas re-scanning. Scope: **local disk, single host, single active writer per canvas** — see "Local-disk, single-host scope" below for what this does and does not cover.

### Grounding/evidence gate (Phase 4)

Setup generation now fails visible before canvas writes instead of trusting prompt wording alone:

- `tool_bridge.READ_TOOLS` is the only model-facing tool set; successful reads are wrapped as `untrusted_data` and captured in a fresh per-run `EvidenceLedger`. The ledger derives stable source ids from tool name, canonical arguments, and content hash, keeps bounded excerpts in memory, and is discarded after the grounding decision.
- `ExperimentSetup` gained additive, defaulted fields: `hypothesis`, `success_criteria`, `constraints`, `confidence`, `citations`, `evidence_status`, and `ambiguity_flags`. Defaults preserve legacy parsing; the grounding gate, not the schema, decides executability.
- `evaluate_grounding` requires `evidence_status='sufficient'`, validates all citation ids against the current ledger, and scans original idea text, emitted setup fields, and all bounded evidence excerpts against the approved acronym dictionary. Unknown or colliding acronym-like terms are Needs Input, never guessed.
- Insufficient evidence or blocking ambiguity writes one `[EXP:Needs Input]` Browser artifact with `NEEDS_REVIEW` state, deduplicated by `(canvas, predecessor_id, reason_hash)`. Invalid citations write no setup/connector and remain retryable/backoff/quarantine-eligible.
- Durable grounding audit stores only decision, truncated reason, and evidence ids/tool/content hashes. It uses the same 4096-byte payload cap as the audit store and deterministically trims newest evidence rows to fit; raw excerpts, tool arguments, credentials, and capability URLs are not persisted.

Future wiki/KG/vector sources can feed this boundary as retrieval adapters or external gates. They are not current hard dependencies, and no real external integrations or wet-lab autonomy are claimed.

### Generated artifact Browser service (Phase 3 implementation)

System-generated Setup/Result/Closed artifacts and generated Needs Input status/prompt artifacts are represented as Canvus Browser widgets. The generated Needs Input Browser artifact carries the request/status marker (`[EXP:Needs Input]`) and `NEEDS_REVIEW` artifact state only; it does not implement a future approval workflow or state transition gate. User-authored `{idea: ...}` notes and human-authored approval/review/input responses remain Notes. Legacy generated Notes remain readable while migration is in progress.

The Browser widget is only the canvas view and graph node. `ArtifactStore` is the canonical record: append-only versions store structured payload, metadata, provenance, state, round, content hash, timestamps, and Browser widget mapping in the same local SQLite state database. Later model reads use the canonical payload via Browser widget mapping and fall back to Note text for legacy canvases; Browser HTML is not parsed as data.

Artifact URLs have a stable resource path, `/artifacts/{opaque_id}`. The `token=...` query value is a bearer capability that may rotate on retries, repair, or revocation. `update_browser` repairs the token-bearing URL/title/location in place so the Browser widget id and connector graph stay stable; the widget is not recreated just because a capability URL rotates.

Operational/security boundaries:

- `LAB_AGENT_ARTIFACT_BIND_HOST` defaults to `127.0.0.1`; production must provide a reachable `LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL` through HTTPS/private ingress for intended Canvus clients. This repo has not verified live external Canvus reachability or TLS termination.
- `LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL` is required before Browser artifact writes; the code fails closed when it is empty or malformed.
- Capability URLs are secrets. Uvicorn access logging is disabled for `serve-artifacts`; audit/operator output must record ids/counts/status only, never full URLs or tokens.
- Token verification is scoped to artifact/canvas and supports rotation/revocation. Unauthorized, missing, wrong-canvas, guessed, or revoked capabilities return the same 404 shape.
- Static assets are served from the same origin under a strict CSP; there are no third-party scripts/styles/assets.
- Back up the shared state DB because it now contains both durable workflow state and canonical artifact records/tokens/widget mappings.
- Legacy migration is dry-run by default and mirror-first on `--apply`: Browser mirrors and equivalent connectors are created without deleting, archiving, or editing original generated Notes.


- **Runtime context.** `cli.py` calls `runtime.build_runtime_context(settings)` exactly once per process: creates the ledger's parent directory, opens/migrates the SQLite file, runs `PRAGMA integrity_check`, and verifies the audit hash chain. Any failure raises `RuntimeStartupError` and the process exits before touching the canvas. Each process gets its own random `runtime_instance_id` (UUID4) — never derived from config — so two processes started with identical settings are still distinguishable lease/attempt owners.
- **Single-writer canvas lease.** `process_once`/`watch` acquire-or-renew a per-`canvas_id` lease keyed by `runtime_instance_id` before doing any work. A live lease held by a *different* runtime causes a safe skip (zero writes, `canvas_lease_denied` audit event) rather than a crash or a race. `once` releases its lease on clean shutdown; `watch` releases on loop exit or `Ctrl-C`.
- **Durable workflow attempts.** Every derived trigger (idea→setup, setup→run, result→loop) gets a `workflow_attempts` row: `pending → running → completed | failed | quarantined`. `completed` is permanent and blocks reprocessing across restarts. A `running` attempt whose lease has expired becomes retryable again (crash mid-attempt is not silently lost, and is not permanently stuck either). A failure schedules a full-jitter exponential backoff retry; once `attempt_count >= max_attempts` the trigger is quarantined (visible via `lab-agent list-quarantined`, resettable via `lab-agent reset`) instead of retried forever. A `schema_validation_failed` outcome is recorded as a failed/retryable attempt, not `completed` — malformed model output must not permanently suppress a trigger.
- **Side-effect intent recovery (outbox pattern).** canvus-mcp's create tools are not idempotent, and there is no `update_note`/search-by-text tool. Before any canvas mutation, `durable_writes.py`/`durable_browser.py` persist a `side_effect_intents` row keyed by a deterministic idempotency key (`recovery.idempotency_key`, folding in canvas id, write kind, and a discriminator — predecessor id, setup id, artifact type, endpoint pair, plus round index, so one loop attempt can safely own many round-scoped intents). Recovery live-probes the canvas (`canvas_probe.py`) for a prior run's already-completed effect before creating anything new. Generated Browser widgets carry a short discriminator tag (`" #" + first 12 hex chars of the idempotency key`) appended to the title so `probe_browser_by_tag` can find them via `scan_experiment_workflow`'s setup/result/closed/needs-input buckets; if found, `update_browser` repairs the URL/title/location in place. Legacy Note recovery remains available via `probe_note_by_tag`; connectors are separately probed via `check_widget_connections` on the known source widget. **The canvas remains workflow truth** — the ledger records intent and completion, it does not replace canvas state as the thing being reconciled against.
- **Audit log.** Every attempt lease/complete/fail/quarantine, lease deny/acquire/release, intent reconcile/fail, and operator action (`operator_integrity_check`, `operator_reset`, `operator_backup`) appends an `audit_events` row. Rows are hash-chained (`event_hash` covers `sequence, canvas_id, event, payload_json, previous_hash`) so tampering is detectable by `verify_audit_chain()` (also run at every CLI startup). Payloads are capped at 4096 bytes and store only ids/hashes/reasons/counts — **never** full note text, model payloads, credentials, or document bodies.
- **Backup.** `lab-agent backup --to <path>` takes a consistent hot copy of the WAL-mode SQLite file (safe to run against a live ledger) and records an `operator_backup` audit event. There is no unsafe restore-overwrite command; a restore drill (open the backup file directly and confirm `integrity_check()`/`verify_audit_chain()` pass) is a documented procedure, not a destructive CLI action — see [setup and operations](setup-and-operations.md).

### Local-disk, single-host scope, and the Postgres/multi-host trigger

The durable harness above assumes **one process (or a strict hand-off sequence of processes) writing to a given canvas at a time**, backed by a local SQLite file on that host's disk. This is intentional for the current single-operator/single-host deployment model, not an oversight:

- SQLite WAL mode supports one writer at a time; the canvas lease enforces that at the application level too (a second concurrent runtime is denied, not corrupted).
- The ledger is not replicated or shared across hosts — moving `lab-agent` to a second host, or running it as multiple concurrently-active replicas against the same canvas, is **not supported** by this implementation.

**Migration trigger:** if/when Lab-in-the-Loop needs multi-host deployment, multiple concurrently-active writer processes, or centralized cross-host observability of attempts/audit history, the durable harness should move from local SQLite to a shared server-backed store (e.g. Postgres) with the same `workflow_attempts`/`canvas_leases`/`side_effect_intents`/`audit_events`/`orchestrator_edges` schema shape adapted to that engine. Until that trigger is hit, SQLite-on-local-disk remains the deliberate choice — see [roadmap](development-roadmap.md) Phase 7 (multi-user deployment) for where this is tracked.

## Data flow

### Idea to setup

```text
RAGCluster_ image ─connector─► Note `{idea: ...}`
        │
        ▼
scan_experiment_workflow reports `ideas_needing_setup`
        │
        ▼
lab-agent reads idea + RagCluster connections
        │
        ▼
model emits ExperimentSetup with citations/evidence status
        │
        ▼
lab-agent validates per-run ledger citations + dictionary ambiguity scan
        │
        ├─ executable → create `[EXP:Setup v001]` Browser artifact and connector idea → setup
        ├─ insufficient/ambiguous → create deduplicated `[EXP:Needs Input]` Browser artifact
        └─ invalid citation/schema → write nothing; durable attempt can retry
```

### Setup to mock robot result

```text
[EXP:Setup vNNN] ─connector─► Robot_
        │
        ▼
scan_experiment_workflow reports `setups_needing_run`
        │
        ▼
model emits clearly mock ExperimentResult
        │
        ▼
lab-agent creates `[EXP:Result vNNN]` Browser artifact and connector robot → result
```

### Result to next setup or close

```text
User connects [EXP:Result vNNN] ─► [EXP:Setup vNNN]
        │
        ▼
detect_experiment_loops resolves setup/result/robot/idea/RagCluster ids
        │
        ▼
model compares setup vs result
        │
        ├─ STOP     → create `[EXP:Closed]` Browser artifact, connect result → closed
        └─ CONTINUE → create `[EXP:Setup vNNN+1]` Browser artifact, connect result → setup, run mock robot
```

## State and idempotency

The canvas remains the source of *workflow* state — the agent treats connector presence as the state transition signal, and `scan_experiment_workflow`/`detect_experiment_loops` still derive "what needs doing" from the live canvas graph. What changed in Phase 2 is *how durably and safely* the agent acts on that signal:

- An idea is processed only if it has no existing setup; a setup is run only if it has no existing result — as before, derived from the canvas graph.
- A detected loop connector, and every other derived trigger, now gets a durable `workflow_attempts` row (see "Durable harness core" below) instead of relying on an in-memory `processed_loops` set — processing state survives process restart, not just one watcher session.
- Each canvas mutation (note/connector creation) is preceded by a persisted `side_effect_intents` row and a live-canvas probe, so a crash between "mutation landed" and "marked done" is recovered without a duplicate write, not just a retried write.
- Writes happen one node at a time: canonical artifact row, Browser widget create/update, setup/result/closed connector.
- The watcher catches transient errors, records them durably (failed attempt + audit event), and continues polling — a crash no longer silently loses which triggers were already in flight.

## Trust boundaries

| Boundary | Risk | Control |
|---|---|---|
| Canvus credentials | Secret leakage | Kept in `apps/canvus-mcp/.env`, ignored by git |
| MCP write tools | Unintended canvas mutation | Orchestrator-only write usage; model gets read tools for grounding |
| Artifact capability URLs | Bearer URL leakage or cross-canvas access | High-entropy tokens stored only as hashes; no access logs; no token in audit/model context; artifact/canvas scope checks; revocation/rotation; private bind by default |
| Artifact HTML rendering | XSS or remote asset leakage | Escaped structured rendering, same-origin CSS/JS only, CSP, no third-party assets |
| Downloaded PDFs/images | Sensitive data | Written to ignored `downloads/`; bytes not in model context by default |
| Retrieved evidence text | Prompt injection or data leakage | Read results wrapped as `untrusted_data`; model has no write tools; durable audit stores ids/hashes/reasons only |
| Model output | Hallucinated domain facts, fabricated citations, guessed acronyms | Ground via RagCluster/read tools; validate citations against per-run ledger; scan idea/setup/evidence excerpts with approved dictionary; structured schemas |
| Loop autonomy | Runaway rounds | Model stop decision plus `LAB_AGENT_LOOP_MAX_ROUNDS` backstop |

## Integration boundary with `rag-canvus`

This repo preserves a serving-side `{exp:}` integration under:

```text
integrations/canvus-serving-experiment-prepare/
```

That integration adds a `canvus-serving` action for:

```text
RAGCluster_ → Note `{exp: ...}` → OpenAI experiment-prep result note
```

It is optional and separate from the primary `canvus-mcp` + `lab-agent` loop. The primary repo can operate without modifying `rag-canvus` if the canvas is driven through MCP.

## Target harness boundary (proposed)

**Status: proposed, pending owner confirmation — not implemented.** The 2026-07-16 recovery review proposed that a future independent harness/orchestrator (today's `lab-agent`, evolved) own:

- Canvus event watching/scanning (today: `lab_agent/watch.py`).
- A durable workflow state machine, with idempotency, retry, resume, failure recovery, and audit/version history (today: **implemented for local-disk, single-host scope** — see "Durable harness core (Phase 2)" above; a shared/replicated store for multi-host deployment remains future, see "Local-disk, single-host scope" above).
- Policy and approval gates (today: none — mock robot only, no human-approval gate in code).
- Token/resource budgets, model routing, cost thresholds, loop limits, and stop conditions (today: only `LAB_AGENT_LOOP_MAX_ROUNDS` as a runaway backstop, plus the model's own `LoopDecision`).
- Retrieval/grounding against internal wiki, knowledge graph, documents, and experiment history (today: RagCluster/read-tool context plus an approved acronym dictionary; no KG/wiki/vector DB integration).
- Context packaging and evidence tracking, model adapter/router selection, and structured-output validation (today: per-run evidence ledger + citation/ambiguity gate, adapter factory, and fail-closed `orchestrator_support.coerce_or_fail`/`_emit_validated`; provider-specific but not policy-aware).
- Tool/action routing to Canvus, Flywheel, in-silico simulation, and robotic/human lab execution (today: mock only; no Flywheel/in-silico wiring).
- Async, chunked, cached, resumable multimodal ingestion rather than one model call per document (today: whole-file downloads via `canvus_mcp/downloads.py`; no chunking/caching/resume).

Under this proposal, the Claude Code skill/MCP registration becomes an **optional developer/operator/demo client** over the same harness — not the production runtime and not the owner of business logic, which stays in the harness and the shared `canvus-mcp` tool boundary. Explicit owner ratification of this direction is absent from the source transcript; do not treat it as approved.

## Current limitations

- Robot execution is mock only.
- Retrieval is canvas/RagCluster/read-tool-oriented with a local approved acronym dictionary; future wiki/KG/vector sources are adapters or external gates, not a full vector DB or knowledge graph runtime today.
- Flywheel/in-silico gates are represented in docs and roadmap, not implemented as live integrations.
- The durable harness is local-disk, single-host, single-active-writer-per-canvas scoped (SQLite WAL) — not a shared/replicated store; see "Local-disk, single-host scope" above for the Postgres/multi-host migration trigger.
- External live Canvus reachability to the artifact public base URL and production TLS/private-ingress verification remain operational gates; this repository documents the requirement but does not prove deployment.
- Token/resource governance, model routing, and multi-user observability do not exist yet — these are the target-harness items above, not current behavior.

## References

- [Experiment workflow](experiment-workflow.md)
- [Setup and operations](setup-and-operations.md)
- [Canonical use case specification](lab-in-the-loop-use-case-specification.md)
- [Original meeting vision](notes/use-case-lab-in-the-loop.md)
- [Canvus-serving integration](canvus-serving-integration.md)
