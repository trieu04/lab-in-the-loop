# Code Standards

## Principles

- Keep it simple. Prefer explicit orchestration over hidden magic.
- Keep reads and writes separated: model reads/grounds; orchestrator writes.
- Use typed models for cross-boundary data.
- Treat Canvus writes as user-visible side effects.
- Do not fake green tests or hide failing behavior.
- Never commit secrets, `.env`, virtualenvs, caches, downloaded internal data, or generated lab output.

## Python conventions

- Python 3.11+.
- Use type hints on public functions and data models.
- Prefer `pydantic` models for structured model outputs and settings.
- Use async consistently around MCP/Canvus/model calls.
- Keep modules focused:
  - transport/client code in client modules;
  - workflow scanning in experiment modules;
  - orchestration in orchestrator modules;
  - rendering in render modules;
  - prompt text in prompt modules.
- Avoid broad exception handling except in watcher loops where the process must keep polling.
- If broad exception handling is used, log context with `structlog`.

## Naming

- Python files and directories follow existing ecosystem snake_case where already used.
- Markdown docs use kebab-case and evergreen names when stable.
- Marker names are exact strings and should not be casually renamed:
  - `RAGCluster_`
  - `Robot_`
  - `{idea: ...}`
  - `[EXP:Setup vNNN]`
  - `[EXP:Result vNNN]`
  - `[EXP:Closed]`
  - `{exp: ...}` — **serving-integration-only**: parsed by the `canvus-serving` `experiment_prepare` action under `integrations/canvus-serving-experiment-prepare/`, not by `canvus-mcp`/`lab-agent`. Do not conflate with `{idea: ...}`, which is the primary loop's marker. See [canvus-serving integration](canvus-serving-integration.md).

## Tool and write discipline

### Model-facing tools

The model should receive only read/grounding tools during reasoning. The enforced allowlist lives in `lab_agent/tool_bridge.py:READ_TOOLS`:

- `scan_server`
- `list_canvases`
- `check_ragcluster_connections`
- `check_widget_connections`
- `get_note`
- `get_widget`
- `download_pdf`
- `get_ingestion_status`
- `read_ingestion_chunks`

`scan_experiment_workflow` and `detect_experiment_loops` are called directly by the orchestrator/watcher (`lab_agent/watch.py`), not exposed to the model's tool-use loop. `download_image`/`download_asset` exist on `canvus-mcp` but are not currently in the model-facing allowlist. `get_ingestion_status` is operational/non-citeable; only bounded completed chunk text may enter the evidence ledger. See [experiment workflow](experiment-workflow.md) → "Tool contract" for the full reconciliation.

### Orchestrator-only writes

Writes are controlled by application code (`lab_agent/nodes.py`):

- `create_note` — user/legacy Note paths only; `{idea: ...}` and human-authored approval/review/input responses stay Notes.
- `create_browser` — generated Setup/Result/Closed and generated Needs Input status/prompt artifacts.
- `update_browser` — in-place Browser repair/token URL rotation without recreating the widget.
- `create_connector`

`create_image` exists as a `canvus-mcp` write tool but is not part of `lab-agent`'s automated generated-artifact write path.

Write helpers fail closed: `lab_agent/nodes.py` raises `MCPToolError` when write tools return malformed JSON, an explicit `error`, or no non-empty `id`. Do not treat an empty id as a successful canvas mutation.

Do not let an unconstrained model decide arbitrary write tool calls. Successful read-tool results that enter the model transcript must be labelled as untrusted data and recorded only in a per-run evidence ledger; blocked/error calls are not evidence.

## Resumable ingestion standards

- Keep ingestion schema migrations immutable, numbered SQL files. The migration runner must apply them under SQLite WAL/`BEGIN IMMEDIATE`, record each migration's SHA-256, and fail on checksum or ordering drift.
- Keep raw storage local, ignored, and private: directories `0700`, files `0600`. Use directory file descriptors plus no-follow opens for cache traversal; reject symlinks, non-regular files, ownership/mode/inode changes, and digest mismatches. Publish cache bytes only after private temp-file fsync and verification through no-replace promotion; repair an invalid interrupted final but never replace a verified collision. Do not retain a mutable download path as job identity; buffered downloads must publish to unique immutable paths bound to their hashes.
- Acquire bounded sources by streaming and counting bytes before final persistence. Content identity is SHA-256; derived work identity adds extractor version; source authorization remains canvas-scoped. Normalize untrusted declared MIME before routing generic assets to an extractor.
- Parsers must be local, deterministic, strict, and bounded. Decode text/CSV/TSV/JSON as strict UTF-8; run PDF page text extraction in a resource-limited child; emit typed `malformed`, `encrypted`, `oversized`, or `unsupported` outcomes rather than partial fabrication or raw parser diagnostics. Require the restrictive PDF password-file contract; do not add video or non-CSV/TSV spreadsheet claims without an approved extractor.
- Worker completion must prove the current lease token/generation and atomically persist chunks with the unit completion. Expired leases are reclaimable; recovery must apply the same poison-attempt ceiling; stale generations cannot commit; completed units never rerun. Preserve retry/backoff, poison, cancellation, and restart behavior in durable state.
- Register ingestion mutations behind role/canvas authorization. `ctx` stays hidden from tool schemas; every HTTP ingestion call requires exactly one strict Bearer credential, while stdio remains reader-only by default. Denials use a fixed public shape and metadata-only audit — never token values, raw arguments, paths, or resource-existence detail. Return requested-source provenance exactly; represent ambiguous job-only provenance as a bounded source set with `unknown` classification. Existing non-ingestion anonymous reads/downloads remain backward-compatible.
- At the model boundary, accept only exact allowlisted bare/configured-namespaced tool identities. Reject mutations and arbitrary suffix matches. Validate and bound arguments, results, call counts, and provider-visible transcripts; use fixed sanitized errors for malformed/unsafe/over-limit data. Status is operational data, not evidence; chunks are bounded `untrusted_data` with allowlisted scalar provenance and classification, and become citeable only after ledger retention. Apply locality before every later provider call.
- The MCP server owns cache/store lifecycle and must close cache descriptors before SQLite while unwinding partial startup safely; it must not start a worker. Maintain the standalone worker lifecycle (claim, renewal, graceful stop, expiry reclaim) independently; do not document a worker CLI unless it is registered in `pyproject.toml`.

## Generated artifact Browser standards

- Canvus Browser widgets are views/graph nodes only. Canonical generated artifact data belongs in `ArtifactStore` as versioned structured records with metadata, provenance, content hash, state, round, and Browser-widget mapping.
- Browser widget identity and `/artifacts/{opaque_id}` resource paths are stable. Token-bearing capability URLs may rotate; repair with `update_browser` in place rather than recreating the widget.
- Place new generated Browser widgets relative to their source/predecessor geometry when `get_widget` returns usable coordinates; fall back to the deterministic grid when geometry is unavailable. Preserve existing Browser positions during URL/title repair so manual canvas adjustments are not undone.
- Never log, audit, paste into model context, or print full capability URLs/tokens. Store only token hashes and emit ids/counts/status in operator output.
- Bind the artifact service privately by default. Production needs a reachable public base URL for Canvus clients plus HTTPS/private ingress; external live Canvus reachability and TLS are operational gates, not assumed by code or docs.
- Enforce artifact/canvas scope on every read, support revocation/rotation, and keep static assets same-origin under the service CSP.
- Generated Needs Input artifacts are Browser prompt/status markers only; do not imply future approval workflow transitions are implemented. Human-authored responses remain Notes.
- Legacy generated Notes remain readable during migration; migration must be dry-run/mirror-first and non-destructive by default.

## Provider and harness boundaries

- Model providers are adapters (`lab_agent/adapters/factory.py`: `openai`, `claude`); no workflow logic may hard-code a specific provider or assume a Claude Code license is available.
- A 2026-07-16 architecture review proposed (pending owner confirmation) that shared workflow logic, policy, grounding, and schemas belong in an independent harness/orchestrator (`lab-agent`, evolved), with the Claude Code skill/MCP registration kept as an optional developer/operator/demo interface only. See [system architecture](system-architecture.md) → "Target harness boundary (proposed)". Do not add business logic to a Claude Code skill that the headless `lab-agent` path cannot also exercise.
- Production dispatch must go through `GovernedAdapter`/`model_gateway.py`. Orchestration identifies a `TaskStage`, never a hard-coded provider; routing selects only a constructed provider that locality authorizes before dispatch. Do not add a retry/fallback that can resend a submitted/ambiguous request to any provider.
- Require a configured HTTPS endpoint, classification allowlist, known versioned model price, and durable reservation before a provider sees content. Unknown classification, endpoint, provider, price, or reservation outcome fails closed. Provider/locality approval and price-table maintenance are operator controls, not implied by source configuration.
- Normalize usage as `exact`, `estimated`, or `unavailable`. Preserve Claude cache-creation/read counts in exact input usage. Conservative estimates must cover every provider-visible request field (messages, tools, response schema, schema name, output cap) and must never be described as invoices.
- Persist only safe fixed categories, digests, counts, and approved metadata for provider intents/failures. Never persist or audit raw prompt, response, secret, or provider-error values. Typed, known-not-dispatched transients alone may retry with a durable `next_retry_at`; deterministic, ambiguous, and untyped outcomes fail closed or reconcile only through a provider capability.
- Treat model decision, maximum rounds, token/cost budget, wall time, no progress, locality denial, and reservation denial as distinct terminal closures. After a terminal closure begins, do not issue another provider call or canvas mutation for that run.

## In-silico validation and approval gates

- Bind typed `ExperimentSetup` proposals and `InSilicoResult` records to canonical SHA-256 hashes. The canonical serialization contract is `litl-canonical-json-v1`; do not replace it with incidental JSON formatting or untyped canvas text.
- Treat `DeterministicInSilicoAdapter` output as a local structural dry run, not scientific validation. It must remain visibly labelled as such. A real adapter is disabled/unimplemented and must fail closed until its explicit readiness controls and integration exist.
- Store validation and approval evidence append-only in the local SQLite ledger, scoped to the canvas. A durable approval must bind the exact proposal hash, validation-result hash, validation adapter/version/algorithm metadata, credential-verified identity, role, and decision.
- Approval roles are unique per current proposal/result and ordered: scientist first, then lab lead. Retain stale evidence for audit, but never use it to authorize an edited proposal or changed validation result.
- Canvas Notes, titles, connectors, widget author text, and Browser status artifacts are topology or projections only. They are never approval evidence. Obtain approvals only through an `IdentityProvider` that verifies a credential; never persist the credential or include it in an audit, artifact, prompt, log, or error.
- Browser validation and approval-status artifacts project durable evidence. Their terminal `APPROVED_FOR_WET_LAB` state does not enable execution: `execution_enabled` remains `false` in Phase 7.
- `wet_lab_execution_enabled` defaults to `false`. The watcher must not call the legacy `run_loop` path. An explicitly enabled future Phase 8 branch may produce only a model-generated `MOCK_RESULT` after the durable gates; it is not a hardware or laboratory integration.

## Structured output

Model outputs that drive workflow state should be schema-bound:

- `ExperimentSetup`
- `ExperimentResult`
- `LoopDecision`

If a provider returns invalid schema, fail visibly and leave the canvas state pending so a retry can happen safely. **Never fabricate or fill in missing required fields** to make an invalid payload pass — a note that looks complete but was silently patched by the orchestrator is worse than no note at all.

The enforcement path is `lab_agent/orchestrator_support.py:coerce_or_fail` plus `_emit_validated`: `coerce_or_fail` validates parsed model output against its Pydantic schema and raises `SchemaValidationError` instead of coercing or defaulting missing fields; `_emit_validated` catches that error, logs a `schema_validation_failed` warning (stage, model name, raw payload) via `structlog`, and returns `None`. The setup, result, and decision callers propagate that fail-closed outcome without writing a note or connector or retrying within the same cycle. The canvas is left exactly as it was; the trigger's durable attempt is marked failed/backoff/quarantine-eligible so a later due poll can retry safely.

## Grounding and scientific caution

- Do not invent domain facts.
- Do not guess acronym meanings; only an approved acronym dictionary can resolve a term.
- Setup generation must use a bounded, per-run evidence ledger with deterministic source ids derived from read tool, canonical arguments, and content hash.
- Validate evidence before writes: `evidence_status` must be `sufficient`, every citation must resolve in the current ledger, and unresolved acronym-like terms across original idea text, emitted setup fields, and all bounded retrieved evidence excerpts must produce Needs Input.
- Invalid citations are fail-closed: write no setup/connector, keep the durable attempt retryable/backoff/quarantine-eligible.
- Needs Input artifacts are explicit Browser request/status artifacts for insufficient evidence or ambiguity, deduplicated by predecessor plus reason hash.
- Durable audits store ids/hashes/status/reason only, cap payloads at 4096 bytes, and trim evidence rows to fit; never persist raw excerpts, tool arguments, credentials, or capability URLs.
- Future wiki/KG/vector sources are retrieval adapters or external gates, not current hard dependencies.
- Mock robot results must be clearly mock.
- Future wet-lab integrations require explicit human approval and safety gates.

## Tests

Each app keeps its own tests:

```bash
cd apps/canvus-mcp && uv run pytest -q
cd apps/lab-agent && uv run pytest -q
```

Run lint:

```bash
cd apps/canvus-mcp && uv run ruff check canvus_mcp tests
cd apps/lab-agent && uv run ruff check lab_agent tests
```

Run mypy when changing typed contracts:

```bash
cd apps/canvus-mcp && uv run mypy canvus_mcp
cd apps/lab-agent && uv run mypy lab_agent
```

For ingestion changes, retain direct regressions for checksummed-migration drift, dedup/version behavior, stale lease rejection, atomic chunks/completion, retry/poison/cancel/restart, streaming byte caps, descriptor/no-follow storage, strict parser outcomes, canvas authorization, strict Bearer transport, bounded pagination/output, model mutation exclusion, exact namespace matching, and locality after ingestion reads. Use real local streamable-HTTP and subprocess crash/restart proofs where the contract crosses a process boundary; do not replace them with a fake authorization bypass.

Keep modules focused and within the repository's approximately 200-line Python-module convention. Split concentrated storage, retry, extraction, or policy responsibilities rather than creating a broad ingestion manager.

## Documentation updates

Update docs when changing:

- canvas markers;
- MCP tool names or schemas;
- environment variables;
- loop semantics;
- provider configuration;
- safety/idempotency behavior;
- generated artifact storage/rendering, capability URL handling, artifact service deployment, or migration behavior;
- evidence/citation validation, acronym dictionary behavior, untrusted-data boundaries, or durable audit payload policy;
- typed validation/approval hashes, IdentityProvider behavior, durable gate evidence, or execution-gate defaults;
- any future real lab/Flywheel integration.

Docs to keep in sync:

- `README.md`
- `docs/notes/use-case-lab-in-the-loop.md`
- `docs/lab-in-the-loop-use-case-specification.md`
- `docs/system-architecture.md`
- `docs/experiment-workflow.md`
- `docs/setup-and-operations.md`
- `docs/canvus-serving-integration.md`
- `docs/code-standards.md`
- `docs/development-roadmap.md`
- `docs/project-changelog.md`

## Git hygiene

Recommended commit split:

1. source code changes for one app;
2. tests for that app if not included in same feature commit;
3. docs updates;
4. integration patches separately.

Conventional commits:

- `feat:` new workflow capability
- `fix:` bug fix
- `docs:` documentation only
- `test:` test-only changes
- `refactor:` behavior-preserving cleanup
- `chore:` repo maintenance

Before committing:

```bash
git status --short
find . -name .env -o -name .venv -o -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache -o -name .ruff_cache
```

The second command should not show files staged for commit.
