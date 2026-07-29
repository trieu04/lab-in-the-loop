# Setup and Operations

## Prerequisites

- Python 3.11+
- `uv`
- Access to a Canvus server
- Canvus API token
- At least one model provider API key:
  - OpenAI for `LAB_AGENT_MODEL_PROVIDER=openai` (also used for OpenAI-compatible endpoints — see "Provider-neutral operation" below)
  - Anthropic for `LAB_AGENT_MODEL_PROVIDER=claude`
- Claude Code — **optional**, needed only if you want to register `canvus-mcp` as an MCP server for interactive/demo use (see "Register with Claude Code" below). `canvus-mcp` and `lab-agent` run without it.

## Provider-neutral operation

`lab-agent`'s adapter factory (`lab_agent/adapters/factory.py`) currently supports two named providers: `openai` and `claude`. The `openai` adapter accepts an OpenAI-compatible `base_url` (`LAB_AGENT_OPENAI_BASE_URL`), so Ollama, vLLM, and other OpenAI-compatible endpoints can be used through the `openai` provider by pointing `base_url` at them. There are **no dedicated Ollama or vLLM adapters** in the code — compatibility is via the OpenAI-compatible HTTP surface, not a first-class adapter. Running the workflow does not require a Claude Code license; only the `claude` provider path needs an Anthropic API key, and that is one of two interchangeable choices.

## Repository layout

```text
~/dev/lab-in-the-loop
├── apps/canvus-mcp
├── apps/lab-agent
├── docs
├── integrations
└── assets
```

## Configure `canvus-mcp`

```bash
cd ~/dev/lab-in-the-loop/apps/canvus-mcp
cp .env.example .env
```

Fill:

```bash
CANVUS_API_URL=https://your-canvus-server.example/api/v1
CANVUS_API_KEY=your-long-lived-api-token
CANVUS_VERIFY_SSL=true
CANVUS_MCP_OUTPUT_DIR=./downloads
CANVUS_MCP_HOST=127.0.0.1
CANVUS_MCP_PORT=8931
CANVUS_MCP_RAGCLUSTER_MARKER=RAGCluster_
```

Secrets stay in `.env`; `.env` is git-ignored.

### Canvas classification for governed dispatch

Leave `CANVUS_CANVAS_CLASSIFICATIONS={}` as the safe default until an operator assigns a class. Its JSON key must exactly match the canvas id passed to `lab-agent --canvas`; supported values are `public`, `internal`, and `restricted`.

```bash
# Placeholder only: replace <exact-canvas-id>; do not use a real canvas id in shared examples.
CANVUS_CANVAS_CLASSIFICATIONS={"<exact-canvas-id>":"internal"}
```

An unmapped, malformed, or unsupported value becomes `unknown`. `unknown` denies the trigger before provider dispatch.

### Implementation-plan Phase 6 ingestion configuration and access (roadmap Phase 4c)

The ingestion ledger/cache is local-only state, separate from `lab-agent`'s state DB. Configure the following `CANVUS_MCP_*` values only when overriding defaults. Paths are process-working-directory-relative unless absolute.

| Variable | Default / constraint | Purpose |
|---|---|---|
| `CANVUS_MCP_INGESTION_DB_PATH` | `./.state/ingestion.db` | SQLite/WAL ingestion ledger |
| `CANVUS_MCP_INGESTION_CACHE_DIR` | `./.state/ingestion-cache` | private immutable raw-byte cache |
| `CANVUS_MCP_INGESTION_WORKER_CONCURRENCY` | `2`; integer `1`–`4` | value available to an embedding worker runner; not wired by the MCP server |
| `CANVUS_MCP_INGESTION_LEASE_SECONDS` | `60`; `>= 0.1` | value available to an embedding worker runner; not wired by the MCP server |
| `CANVUS_MCP_INGESTION_MAX_ATTEMPTS` | `3`; integer `1`–`10` | value available to an embedding worker runner; not wired by the MCP server |
| `CANVUS_MCP_INGESTION_MAX_SOURCE_BYTES` | `10485760`; `>= 1` | streaming acquisition and extractor source cap |
| `CANVUS_MCP_INGESTION_MAX_OUTPUT_CHARS` | `16000`; `>= 1` | extractor output cap per unit |
| `CANVUS_MCP_INGESTION_MAX_RECORDS` | `500`; `>= 1` | CSV/TSV and JSON record cap |
| `CANVUS_MCP_INGESTION_MAX_PDF_PAGES` | `200`; `>= 1` | PDF page cap |
| `CANVUS_MCP_INGESTION_CHUNK_CHAR_CAP` | `8000`; integer `1`–`8000` | maximum persisted/read chunk characters |
| `CANVUS_MCP_INGESTION_PDF_PASSWORD_FILE` | unset | optional regular, non-symlink password file; it must be owner-only (no group/other bits), at most 256 bytes, valid UTF-8, and outside the repository |

Do not configure a password directly in `.env`. Encrypted PDFs without a password that meets this file contract return the typed `encrypted` outcome. Local extractors support strict UTF-8 text, CSV/TSV, JSON records, PNG/JPEG/GIF metadata, and PDF page text; malformed, oversized, and unsupported sources return typed outcomes. Video and spreadsheets other than CSV/TSV are not supported by this implementation. The server reads the three worker-setting values but does not construct a worker; any separate embedding runner must explicitly pass them to `IngestionWorker` rather than assuming the environment starts or configures one.

Ingestion access is static and canvas-scoped. Leave token examples blank and put exact JSON canvas allowlists in the role that needs them:

```bash
CANVUS_MCP_READER_TOKEN=
CANVUS_MCP_TRUSTED_SERVICE_TOKEN=
CANVUS_MCP_OPERATOR_TOKEN=
CANVUS_MCP_READER_CANVASES=[]
CANVUS_MCP_TRUSTED_SERVICE_CANVASES=[]
CANVUS_MCP_OPERATOR_CANVASES=[]
CANVUS_MCP_STDIO_ROLE=reader
CANVUS_MCP_STDIO_CANVASES=[]
CANVUS_CANVAS_CLASSIFICATIONS={}
```

`CANVUS_MCP_STDIO_ROLE` is one of `reader`, `trusted_service`, or `operator` and defaults to `reader`; its scope is `CANVUS_MCP_STDIO_CANVASES`. The three role token values are secrets. `reader` can only call `get_ingestion_status` and `read_ingestion_chunks`; `trusted_service` additionally gets `enqueue_ingestion`, `create_note`, `create_browser`, `update_browser`, `create_image`, and `create_connector`; `operator` additionally gets `retry_ingestion`, `cancel_ingestion`, `integrity`, `admin`, and `health`. Every role is limited to its exact configured canvas ids (or an explicitly configured `"*"` scope); HTTP requests must carry exactly one valid `Authorization: Bearer <token>` header. The `health` tool has no canvas parameter but remains operator-only. The classification map is operator-owned JSON keyed by exact canvas id; unmapped or malformed entries are `unknown`.

Existing non-ingestion reads/downloads retain their anonymous compatibility behavior. That compatibility does not permit enqueue, retry, cancel, or any authenticated ingestion read.

## Run `canvus-mcp`

```bash
cd ~/dev/lab-in-the-loop/apps/canvus-mcp
uv sync --extra dev
uv run canvus-mcp
```

Default MCP endpoint:

```text
http://127.0.0.1:8931/mcp
```

`canvus-mcp` is the only Canvus package console command (`canvus-mcp`, optionally `canvus-mcp --stdio`). Its server constructs the ingestion store/cache/auth runtime but does **not** start an `IngestionWorker`; no ingestion-worker console command is registered in the current `pyproject.toml`. An operator embedding the supported `IngestionWorker` component must run it as a separate process against the same configured ingestion DB/cache and give it a stable, unique owner value. On normal stop, call `stop()` so it stops claiming new units while already claimed units finish; restart recovery reclaims expired leases. Do not run a worker inside the MCP server process.

There is also no application-level implementation-plan Phase 6 / roadmap Phase 4c ingestion `integrity`, backup, or restore CLI. The `lab-agent integrity`/`backup` commands below apply only to `LAB_AGENT_STATE_DB_PATH`, not `CANVUS_MCP_INGESTION_DB_PATH`. Treat the ingestion DB/cache as operator-managed local state: back it up and retain/prune it only through an approved operational procedure, with the service stopped or SQLite/WAL-consistent handling selected by the operator.

### Register with Claude Code (optional)

Registering `canvus-mcp` with Claude Code is only needed for the interactive/demo/operator Claude Code skill path. It is not required to run `canvus-mcp` + `lab-agent` as the primary workflow — skip this subsection entirely if you only need the headless `lab-agent` watcher.

HTTP transport:

```bash
claude mcp add --transport http -s user canvus http://127.0.0.1:8931/mcp
```

Stdio alternative:

```bash
claude mcp add -s user canvus -- \
  uv run --directory /home/ntdm/dev/lab-in-the-loop/apps/canvus-mcp canvus-mcp --stdio
```

After registering, fully restart Claude Code. Resuming an old session may not rebuild the MCP tool schema.

## Configure `lab-agent`

```bash
cd ~/dev/lab-in-the-loop/apps/lab-agent
cp .env.example .env
```

For OpenAI:

```bash
LAB_AGENT_MCP_URL=http://127.0.0.1:8931/mcp
LAB_AGENT_MODEL_PROVIDER=openai
LAB_AGENT_OPENAI_API_KEY=sk-...
LAB_AGENT_OPENAI_MODEL=gpt-4o-mini
LAB_AGENT_OPENAI_BASE_URL=
```

`LAB_AGENT_OPENAI_BASE_URL` can point at any OpenAI-compatible endpoint (e.g. a local Ollama or vLLM server exposing an OpenAI-compatible API) while keeping `LAB_AGENT_MODEL_PROVIDER=openai` — this is provider-neutral operation via the existing `openai` adapter, not a separate named adapter. For governed dispatch, including a local deployment, that endpoint must be approved and served over HTTPS/TLS; configure the same HTTPS URL in `LAB_AGENT_PROVIDER_ENDPOINTS["openai"]`, which is the SDK-authoritative destination.

For Claude:

```bash
LAB_AGENT_MCP_URL=http://127.0.0.1:8931/mcp
LAB_AGENT_MODEL_PROVIDER=claude
LAB_AGENT_ANTHROPIC_API_KEY=sk-ant-...
LAB_AGENT_ANTHROPIC_MODEL=claude-sonnet-4-5
```

### Runtime categories

#### Mandatory governed dispatch gates — no bypass

Every `once` and `watch` model call is governed. Dispatch requires classified
canvas/source evidence that the selected provider may receive, an approved HTTPS
provider endpoint, a complete input/output rate entry in a versioned pricing
table, a durable intent, and a durable reservation. Stage routing is
deterministic; a later provider is eligible only as a **pre-dispatch** fallback,
never after submission. MCP results/arguments and model tool calls,
transcripts, and evidence are bounded, while retry/reconciliation rules prevent
blind redispatch. Missing, empty, unknown, or unauthorized values fail closed;
there are no switches to bypass these controls.

The empty `LAB_AGENT_MODEL_PRICING={}` template intentionally makes governed
dispatch unavailable. It does not mean zero-cost use. Every configured model
must provide finite, nonnegative `input_per_1k` and `output_per_1k` rates;
missing, malformed, negative, NaN, or infinite rates are rejected when settings
load. Explicit zero rates remain valid for an approved free/local model. Prices
calculate estimates for reservation and accounting only, and estimates are not
invoices.

#### Core runtime

Core operation requires Canvus and `canvus-mcp`, a governed named provider,
`lab-agent once` or `watch`, and the single-host SQLite ledger for leases,
attempts, intents, audit, gate evidence, and generated artifacts. Generated
Browser artifacts additionally need a valid public artifact URL and the
artifact server when users must view them. In authenticated MCP deployments,
orchestrator writes require a scoped trusted-service credential.

For production, set a named `LAB_AGENT_TENANT_ID` and explicit
`LAB_AGENT_ALLOWED_CANVAS_IDS`. The default tenant with no allowlist remains
legacy, unbound development behavior; a named tenant without an allowlist fails
at startup.

#### Optional limits

Run/canvas token and cost budgets and the wall-time ceiling are optional
numeric limits. Omit them to leave that particular dimension uncapped; pricing,
locality, endpoint authorization, durable intent, and reservation still remain
mandatory. Routing customization, loop policy, concurrency, and lease/retry
tuning are optional runtime controls.

#### Optional extensions

- `LAB_AGENT_IN_SILICO_VALIDATION_ENABLED=true` by default schedules
  deterministic validation for new eligible setups; setting it false disables
  only that new scheduling.
- `LAB_AGENT_WET_LAB_EXECUTION_ENABLED=false` keeps the legacy mock-result
  path off by default.
- `LAB_AGENT_PHASE8_EXECUTION_ENABLED=false` keeps the deterministic Phase 8
  dry-run lifecycle off by default.
- SMTP is disabled until fully configured.
- The ingestion worker is separate and optional; LightRAG and real lab,
  Flywheel, and knowledge integrations are unavailable in this runtime.

Loops and approval-status reconciliation have no feature flag. `canvus-mcp`
still initializes its ingestion storage/cache when its server starts, even if
no worker is running.

Runtime bounds:

```bash
LAB_AGENT_MAX_TOOL_STEPS=8
LAB_AGENT_MODEL_MAX_OUTPUT_TOKENS=4096  # Settings default; optional in .env
LAB_AGENT_WATCH_POLL_SECONDS=30
LAB_AGENT_LOOP_MAX_ROUNDS=25
# Default-on deterministic structural validation; false skips only new scheduling.
LAB_AGENT_IN_SILICO_VALIDATION_ENABLED=true
# Safe default: watcher will not dispatch a setup-to-robot mock-result path.
LAB_AGENT_WET_LAB_EXECUTION_ENABLED=false
# Phase 8 milestone 7A is also default-off and accepts dry_run only.
LAB_AGENT_PHASE8_EXECUTION_ENABLED=false
LAB_AGENT_PHASE8_EXECUTION_MODE=dry_run
```

`LAB_AGENT_LOOP_MAX_ROUNDS` is one governance backstop. Terminal closure can also come from model decision, token/cost budget, wall time, no progress, locality denial, or reservation denial.

### Phase 7 approval gates and Phase 8 7A dry-run lifecycle safety

Every canonical Setup Browser artifact is eligible for a typed in-silico validation pass. The default local `DeterministicInSilicoAdapter` is a deterministic structural dry run: it checks whether required proposal and review fields are present, is visibly rendered as **not scientific validation**, and calls no scientific simulator or external provider. It produces neither scientific validity nor measured evidence. The real adapter boundary remains disabled/unimplemented and fails closed.

`LAB_AGENT_IN_SILICO_VALIDATION_ENABLED=false` skips only new validation scheduling: it creates no new adapter call, validation attempt, result, projection, connector, retry, or synthetic decision. It does not erase or invalidate existing durable validation/approval evidence, and it does not disable loop processing, approval reconciliation, or execution reconciliation. Existing evidence may still reconcile or be considered by the independent approval and execution gates.

Validation and approvals are append-only records in `LAB_AGENT_STATE_DB_PATH`, scoped to the canvas. Proposal and result identity use canonical SHA-256 (`litl-canonical-json-v1`). A current validation `proceed` decision must be followed by credential-verified **scientist** approval and then credential-verified **lab-lead** approval. Approval submissions require an `IdentityProvider`; development providers are explicitly non-production and the production provider is deliberately disabled until readiness requirements are implemented. Credentials are verification inputs only and are never stored in SQLite, Browser artifacts, logs, prompts, or audit records.

A Canvas Note, title, connector, author field, or Browser widget cannot approve a setup. Validation and Approval Status Browser artifacts are read-only projections of durable evidence. A projected terminal `APPROVED_FOR_WET_LAB` state still reports `execution_enabled=false` in Phase 7.

`LAB_AGENT_WET_LAB_EXECUTION_ENABLED` defaults to `false`. With that default, the legacy watcher does not process `setups_needing_run` through `run_on_robot`. Loop continuation may still stage a grounded successor Setup, but Result generation waits for fresh deterministic validation, ordered approval, and explicit execution enablement; no path establishes real execution or measured scientific truth.

`LAB_AGENT_PHASE8_EXECUTION_ENABLED` also defaults to `false`. If an operator explicitly enables it, the 7A factory accepts only `LAB_AGENT_PHASE8_EXECUTION_MODE=dry_run` and constructs deterministic memory-only lab, Flywheel, and knowledge adapters. The lifecycle rechecks the exact current proposal/result hashes, validation `proceed`, credential-verified scientist approval, and credential-verified lab-lead approval. It writes only **DRY RUN / MOCK — NOT MEASURED** execution, analysis, knowledge, and possible conflict projections. It performs no real provider API or network call, robot/wet-lab action, Flywheel/HPC job, knowledge-store write, credential handling, raw-provider-text retention, capability-URL projection, or measured-evidence production. `sandbox` and `real` modes fail closed.

Execution mode belongs in the user-authored idea Note: `{idea: ...}` is the backward-compatible manual mode, and `{idea+auto: ...}` is automatic mode. Any other `{idea+<mode>: ...}` fails closed and creates a deduplicated Needs Input request. In manual mode, the first round additionally requires a credential-verified, durable activation bound to the exact canvas/setup/proposal/validation hashes; an approval/activation service must supply it. There is no approval or activation CLI and Canvas topology/text cannot substitute for verified identity.

Before a real or sandbox execution adapter is introduced, execution authorization must enforce production eligibility. Current reachable adapters are legacy mock or Phase 8 dry-run only, so this is a prerequisite for extending—not a claim that real or sandbox execution is presently available.

### Authenticated bounded MCP reads

Implementation-plan Phase 6 (roadmap Phase 4c) adds the following `LAB_AGENT_*` MCP and model-boundary settings. Result limits apply before an inbound tool result can reach an evidence ledger or transcript; argument, call-count, transcript, and ledger limits bound model use:

| Variable | Default / constraint | Purpose |
|---|---|---|
| `LAB_AGENT_MCP_BEARER_TOKEN` | unset; blank becomes unset | optional Bearer credential sent to `canvus-mcp`; keep it secret |
| `LAB_AGENT_MCP_SERVER_NAMESPACE` | `canvus`; 1–64 ASCII letters/digits/`_`/`-` | exact namespace accepted for MCP tool names |
| `LAB_AGENT_MCP_RESULT_MAX_DEPTH` | `8`; `1`–`16` | nested result depth cap |
| `LAB_AGENT_MCP_RESULT_MAX_CONTAINERS` | `256`; `1`–`4096` | container-count cap |
| `LAB_AGENT_MCP_RESULT_MAX_STRING_CHARS` | `8192`; `1`–`32768`, no greater than byte cap | individual string cap |
| `LAB_AGENT_MCP_RESULT_MAX_BYTES` | `32768`; `1024`–`1048576` | total result/envelope byte cap |
| `LAB_AGENT_MCP_RESULT_MAX_ITEMS` | `256`; `1`–`4096` | collection-item cap |
| `LAB_AGENT_MCP_ARGUMENT_MAX_BYTES` | `32768`; `1024`–`1048576` | serialized tool-argument byte cap |
| `LAB_AGENT_MCP_ARGUMENT_MAX_ITEMS` | `256`; `1`–`4096` | tool-argument collection-item cap |
| `LAB_AGENT_MODEL_TOOL_CALLS_PER_TURN` | `8`; `1`–`64` | maximum dispatched calls from one model turn |
| `LAB_AGENT_MODEL_TOOL_CALLS_PER_RUN` | `32`; `1`–`512`, not below per-turn cap | aggregate dispatched calls for one run |
| `LAB_AGENT_MODEL_TRANSCRIPT_MAX_BYTES` | `262144`; `4096`–`4194304` | provider-visible transcript byte cap |
| `LAB_AGENT_MODEL_EVIDENCE_MAX_BYTES` | `131072`; `1024`–`4194304` | per-run evidence-ledger byte cap |
| `LAB_AGENT_MODEL_EVIDENCE_MAX_ITEMS` | `64`; `1`–`4096` | per-run evidence-ledger item cap |

`LAB_AGENT_MCP_BEARER_TOKEN` is deliberately blank in `.env.example`. The standard Lab Agent CLI uses one MCP session for both bounded reads and orchestrator-owned canvas writes, so an authenticated deployment must configure a canvas-scoped trusted-service credential that authorizes that combined session. A reader credential is appropriate only for a separately restricted read-only client; using it with the standard CLI makes `create_note`, `create_browser`, and `create_connector` fail authorization. The model-facing tool bridge still exposes only exact-matched `get_ingestion_status` and `read_ingestion_chunks` reads (bare or under the configured namespace), rejects `enqueue_ingestion`/`retry_ingestion`/`cancel_ingestion`, and turns malformed, unsafe, or over-bound results into a fixed sanitized error without reflecting paths, credentials, capability URLs, or diagnostics. Status is operational/non-citeable; completed chunks are bounded untrusted evidence carrying only scalar provenance/classification and become model-citeable only after the ledger retains them. The Phase 5 locality policy is evaluated before every subsequent provider call.

### Governance and model routing

`once` and `watch` always construct the governed adapter. Every model call is routed by task stage, authorized for data locality, price-checked, reserved in SQLite, intent-guarded, and checked against terminal stop policy before a later canvas write. Complete the following configuration before operating against a real canvas; an absent/invalid authorization or unpriced model denies dispatch rather than treating it as free or safe.

```bash
# Optional task-stage provider order (JSON). Omitted stage -> LAB_AGENT_MODEL_PROVIDER.
LAB_AGENT_ROUTING_TABLE={"setup":["claude","openai"],"mock_result":["openai"],"loop_decision":["openai"]}

# Provider -> approved HTTPS endpoint and permitted classifications (JSON).
# The current .env.example supplies these explicit values.
LAB_AGENT_PROVIDER_ENDPOINTS={"openai":"https://api.openai.com/v1","claude":"https://api.anthropic.com"}
LAB_AGENT_PROVIDER_DATA_CLASSIFICATIONS={"openai":["public","internal"],"claude":["public","internal"]}

# Versioned model-price table (JSON); rates are USD per 1,000 tokens.
# The current template is intentionally unpriced and therefore cannot dispatch.
LAB_AGENT_PRICING_VERSION=unset
LAB_AGENT_MODEL_PRICING={}

# Optional limits. Leave these commented/omitted for no numeric envelope.
# Pricing remains mandatory for dispatch even when cost limits are omitted.
# LAB_AGENT_RUN_TOKEN_BUDGET=200000
# LAB_AGENT_RUN_COST_BUDGET_USD=5.0
# LAB_AGENT_CANVAS_TOKEN_BUDGET=1000000
# LAB_AGENT_CANVAS_COST_BUDGET_USD=25.0

# Optional wall-time ceiling; the other two values have the defaults shown.
# LAB_AGENT_WALL_TIME_BUDGET_SECONDS=3600
LAB_AGENT_NO_PROGRESS_ROUNDS=3
LAB_AGENT_MODEL_CALL_MAX_ATTEMPTS=3
```

Replace the template's empty pricing table with actual organization-approved rates before dispatch. `LAB_AGENT_PRICING_VERSION` is stamped on estimates so an operator can reconcile them against a maintained rate table. A model absent from `LAB_AGENT_MODEL_PRICING`, or a missing input/output rate, makes cost `unavailable` and denies governed dispatch even if every cost-budget variable is omitted. Usage/cost estimates never equal a provider invoice.

The routing keys are `setup`, `mock_result`, and `loop_decision`; `in_silico` and `analysis` are vocabulary for future stages, not current workflow calls. The first configured, locality-authorized provider in a stage's order is selected. A later provider can be used only as a pre-dispatch fallback; the gateway never changes provider after a submission.

Locality authorization is evaluated before SDK dispatch against source/evidence classifications. `unknown` is denied, and all classifications in a call must be allowed by the selected provider; an unlisted provider, classification, or non-HTTPS/missing endpoint is denied. The Settings default derives canonical built-in endpoints only when the matching OpenAI/Anthropic credential is present (`https://api.openai.com/v1` and `https://api.anthropic.com`). The template deliberately supplies explicit endpoint overrides. `LAB_AGENT_PROVIDER_ENDPOINTS` is the authoritative endpoint passed to the SDK. For an OpenAI-compatible deployment, set the same HTTPS URL in both `LAB_AGENT_OPENAI_BASE_URL` and `LAB_AGENT_PROVIDER_ENDPOINTS["openai"]`; a different explicit endpoint overrides the legacy base URL. There is no dynamic custom-provider adapter: unknown provider names remain fail-closed.

Provider usage is normalized as `exact`, `estimated`, or `unavailable`. Exact usage comes from the SDK; Claude input totals include cache-creation and cache-read tokens. If counts are omitted, a conservative estimate includes serialized messages, tools, response schema, schema name, and `LAB_AGENT_MODEL_MAX_OUTPUT_TOKENS` (the hard request/output cap). Estimates drive reservations but are explicitly not invoices. Missing or unknown usage is never silently zero-priced.

A logical model call persists its digest-backed intent and a durable reservation before dispatch. Holds are transactional and idempotently `reserve`d, then `commit`ted from normalized usage or `release`d only when dispatch is known not to have happened. Per-trigger run accounting resets at each trigger; per-canvas committed totals and active holds survive process restart. Known typed pre-submission transients retry only after `next_retry_at` and only up to `LAB_AGENT_MODEL_CALL_MAX_ATTEMPTS`. Deterministic errors do not retry. Submitted, executed, ambiguous, and untyped outcomes are reconciled only by a provider capability when available; otherwise they remain blocked — never blindly redispatched. Durable records use fixed failure categories, hashes/digests, and approved metadata; prompts, responses, secrets, and raw provider error text are not persisted.

The terminal closures are distinct: model decision, maximum rounds, token budget, cost budget, wall time, no progress, locality denial, and reservation denial. The closure is rendered and audited; no subsequent provider call or canvas write follows it. External provider/locality approval, rate-table maintenance, live endpoint/SDK/API validation, and invoice reconciliation remain operator responsibilities.

Durable harness (local SQLite WAL ledger — see [system architecture](system-architecture.md) → "Durable harness core"):

```bash
LAB_AGENT_STATE_DB_PATH=.state/lab_agent.db      # parent dir created automatically
LAB_AGENT_CANVAS_LEASE_TTL_SECONDS=180            # single-writer canvas lease, renewed each cycle
LAB_AGENT_ATTEMPT_LEASE_TTL_SECONDS=600           # per-trigger lease, covers a full multi-round loop
LAB_AGENT_RETRY_BASE_SECONDS=5                    # full-jitter backoff base
LAB_AGENT_RETRY_MAX_SECONDS=300                   # full-jitter backoff ceiling
LAB_AGENT_MAX_ATTEMPTS=5                          # attempts before a trigger is quarantined
```

All six have safe defaults (shown above) — none are required to run `lab-agent`, and they may be omitted from `.env` unless overriding behavior. `LAB_AGENT_STATE_DB_PATH` is relative to the process's working directory unless given as an absolute path; keep it out of version control (the default `.state/` prefix is already git-ignored). The same SQLite file also stores canonical generated-artifact records, token hashes, Browser widget mappings, gate evidence, and the notification outbox.

### Terminal SMTP notifications

SMTP is disabled by default. Enabling it requires every policy field below; an incomplete enabled configuration fails at startup rather than quietly falling back to plaintext, an untrusted sender, or an unrestricted recipient list.

```bash
LAB_AGENT_NOTIFICATION_SMTP__ENABLED=true
LAB_AGENT_NOTIFICATION_SMTP__HOST=smtp.example.test
LAB_AGENT_NOTIFICATION_SMTP__PORT=587
LAB_AGENT_NOTIFICATION_SMTP__SECURITY=starttls       # starttls (default) or implicit_tls
LAB_AGENT_NOTIFICATION_SMTP__USERNAME=mailer         # omit both username and password for unauthenticated SMTP
LAB_AGENT_NOTIFICATION_SMTP__PASSWORD=               # secret; do not commit or log
LAB_AGENT_NOTIFICATION_SMTP__SENDER=lab-agent@example.test
LAB_AGENT_NOTIFICATION_SMTP__RECIPIENTS=["operator@example.test"]
LAB_AGENT_NOTIFICATION_SMTP__SENDER_ALLOWLIST=["lab-agent@example.test"]
LAB_AGENT_NOTIFICATION_SMTP__RECIPIENT_ALLOWLIST=["operator@example.test"]
LAB_AGENT_NOTIFICATION_LEASE_TTL_SECONDS=60
LAB_AGENT_NOTIFICATION_RECONCILIATION_SECONDS=3600
LAB_AGENT_NOTIFICATION_BATCH_SIZE=1
```

Only encrypted SMTP is supported: `starttls` requires the server to advertise STARTTLS and verifies TLS before delivery; `implicit_tls` uses TLS from connection establishment. The sender and every recipient must be exact valid addresses in their corresponding allowlists. Keep the password in `.env` or your secret manager only; never place it in commands, logs, artifacts, tickets, or documentation.

The watcher enqueues only a qualifying, durable terminal closure and drains at most `LAB_AGENT_NOTIFICATION_BATCH_SIZE` rows after a workflow cycle; the default is `1` (accepted range `1`–`100`). The durable logical key and deterministic `Message-ID` deduplicate one closure across process restart/replay. Delivery is best-effort and logically deduplicated, with automatic retry only for known pre-submit/transient failures; neither exactly-once nor at-least-once inbox delivery is guaranteed. The email body contains closure metadata only: canvas id, trigger id, closure id, round, reason, and notification key—never setup/result content, URLs, credentials, model payloads, or raw SMTP diagnostics.

Known transient pre-submit failures use the durable retry/backoff policy. Full deterministic recipient rejection and malformed outbox metadata quarantine the row. A partial-recipient refusal, process loss after leasing, or failure after SMTP submission is ambiguous: the row is held outside normal sending until its reconciliation deadline and then quarantined; it is not automatically resent. Before using `retry-notification` on an ambiguous row, inspect the mail system/inboxes for every configured recipient and decide whether an intentional duplicate risk is acceptable.

Artifact Browser service (generated Setup/Result/Closed and generated Needs Input status/prompt artifacts):

```bash
LAB_AGENT_ARTIFACT_BIND_HOST=127.0.0.1      # private default
LAB_AGENT_ARTIFACT_BIND_PORT=8600
LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL=          # required before Browser artifact writes
```

`LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL` must be the base URL that Canvus clients can reach, for example an internal HTTPS ingress that forwards to the private bind. Browser artifact writes fail closed when this value is empty or malformed; local `127.0.0.1` only works if the Canvus client browser can reach the same host. Production must use HTTPS/private ingress and restrict access to intended Canvus clients/users. Capability URLs are bearer secrets: do not log, audit, print, paste, or send full token-bearing URLs to model context. This repo has not verified live external Canvus reachability or TLS termination.

## Run `lab-agent`

Every subcommand opens the durable ledger first: it creates `LAB_AGENT_STATE_DB_PATH`'s parent directory if missing, applies any pending SQL migration, runs `PRAGMA integrity_check`, and verifies the audit hash chain. If any of that fails, the command prints `STARTUP FAILED: ...` to stderr, exits `1`, and touches no canvas — this is deliberate fail-closed behavior, not a bug to work around by deleting the ledger (see "Durable harness operator commands" below for the safe path).

One scan/process cycle:

```bash
cd ~/dev/lab-in-the-loop/apps/lab-agent
uv sync --extra dev
uv run lab-agent once --canvas <canvas-id>
```

Continuous watcher:

```bash
# One exact canvas.
uv run lab-agent watch --canvas <canvas-id>

# An explicit bounded subset (the ids must be allowlisted when
# LAB_AGENT_ALLOWED_CANVAS_IDS is configured).
uv run lab-agent watch --canvases <canvas-id-1> <canvas-id-2>

# Or use the configured LAB_AGENT_ALLOWED_CANVAS_IDS list.
uv run lab-agent watch
```

`watch` accepts either `--canvas` or `--canvases`, never both. With no argument it requires `LAB_AGENT_ALLOWED_CANVAS_IDS`; it does not discover canvases to watch. The multi-canvas scheduler limits active canvas cycles to `LAB_AGENT_WATCH_MAX_CONCURRENT_CANVASES` and isolates a failed cycle so other allowlisted canvases continue. It does not make the SQLite ledger, Canvus credentials, or writer leases multi-host-safe.

Artifact HTML service, in a separate long-running process using the same state DB:

```bash
uv run lab-agent serve-artifacts
# or override bind only; public URL still comes from LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL
uv run lab-agent serve-artifacts --host 127.0.0.1 --port 8600
```

Artifact-service health check:

```text
GET /healthz -> {"status":"ok"}
```

The artifact-service response is intentionally non-sensitive. It does not expose DB paths, artifact ids, tokens, or config.

Local operator health:

```bash
uv run lab-agent health
```

This command emits one sanitized JSON readiness summary. It reports only fixed dependency states for the local state store/audit chain, configured provider endpoint, artifact service, MCP reachability, ingestion-worker check state, and Phase 8 mode; it does not print URLs, paths, secrets, raw errors, canvas contents, or provider payloads. A `blocked` summary exits `1`; a non-blocked degraded summary exits `0`.

`canvus-mcp` exposes its detailed health only as the operator-authorized MCP `health` tool. It reports fixed readiness states for the process, ingestion store/cache, Canvus dependency, and ingestion-worker check state. It never returns raw SDK/storage errors, URLs, paths, tokens, or canvas content; reader and trusted-service credentials cannot call it.

Stop with Ctrl-C. The watcher finishes the current operation before exiting if the process receives normal interruption; on exit (clean or Ctrl-C) it releases the canvas lease it holds, so a subsequent run (this process restarted, or a different host/runtime) is not blocked waiting for a stale lease to expire.

Only one runtime instance can hold the write lease for a given `--canvas` at a time. If a second `lab-agent once`/`watch` is started against the same canvas while another instance's lease is still live, it safely skips all work for that cycle (zero canvas writes, a `canvas_lease_denied` audit event) rather than racing or crashing — see "Second instance appears to do nothing" under Troubleshooting below.

## Durable harness operator commands

```bash
# Verify SQLite integrity and the audit hash chain; exit 0 if clean, 1 otherwise.
uv run lab-agent integrity

# List quarantined workflow attempts (a trigger that failed LAB_AGENT_MAX_ATTEMPTS times).
uv run lab-agent list-quarantined [--canvas <canvas-id>]

# Reset one quarantined attempt back to pending so the next once/watch cycle retries it.
uv run lab-agent reset --canvas <canvas-id> --trigger <trigger_id>

# Write a consistent hot backup of an unbound legacy/global ledger.
# This requires explicit authority; tenant-bound runtimes cannot take a full-ledger backup.
uv run lab-agent backup --global-authority --to <path>

# Show only safe notification delivery counts; optionally scope to a canvas.
uv run lab-agent notification-status [--canvas <canvas-id>]

# List quarantined notification keys and fixed failure categories.
uv run lab-agent list-notification-quarantined [--canvas <canvas-id>]

# Return one quarantined notification to the pending queue.
# Investigate an ambiguous SMTP outcome first; this can intentionally resend it.
uv run lab-agent retry-notification --key <logical-key>

# Deliberately quarantine one currently due pending notification without sending it.
uv run lab-agent quarantine-notification --key <logical-key>
```

`<trigger_id>` comes from `list-quarantined`'s output (e.g. `idea_setup:<widget_id>`, `setup_run:<widget_id>`, `loop:<connector_id>`). `reset` only succeeds on an attempt that is actually quarantined; it prints `ERROR: ...` and exits `1` otherwise — there is no bulk/blind reset. Notification commands likewise print only counts, canvas ids, logical keys, attempt counts, and fixed categories; they never print SMTP passwords, raw transport errors, mail bodies, or capability URLs. `retry-notification` succeeds only for a quarantined key, and `quarantine-notification` only for one currently due pending key.

### Ledger migrations and rollback

At startup, every command opens the SQLite ledger and applies numbered migrations in order. Migrations `005_gate_evidence.sql`, `006_notification_outbox.sql`, `007_terminal_audit_lookup.sql`, `008_notification_reconciliation_index.sql`, `009_notification_due_indexes.sql`, and `010_execution_analysis_knowledge.sql` add validation/approval evidence, the durable notification outbox, terminal-event lookup, bounded notification-query indexes, and Phase 8 append-only execution/analysis/artifact-lineage/knowledge/conflict records. Migration 010 creates `execution_runs`, `analysis_runs`, `artifact_refs`, `knowledge_versions`, and `conflict_records`; existing `side_effect_intents` retains submit/abort identity and `workflow_attempts` retains watcher retry/backoff. The runner records each migration's SHA-256 and rejects checksum or ordering drift; migrations are forward-only. Do not edit an already-applied migration, delete rows from `schema_migrations`, or attempt a hand-written downgrade.

To stop notifications, set `LAB_AGENT_NOTIFICATION_SMTP__ENABLED=false` and restart; this disables sending but does not remove migrated tables or durable outbox history. To roll back application state after a bad migration or operator error, stop `lab-agent` and `serve-artifacts`, restore an already verified pre-change backup using the restore procedure below, and restart. A restore reverts the complete ledger state, including attempts, artifacts, gate evidence, and notification outbox records, to that backup point. Take and integrity-check a backup before upgrading or manual SQLite maintenance.

### Workflow attempt recovery

Failed attempts wait and retry after backoff. Fix the cause before using the targeted `reset --canvas <canvas-id> --trigger <trigger_id>` command for a quarantined attempt. Completed attempts remain immutable: do not reset them; use a new trigger or a separately reviewed recovery mechanism.

### Restore drill

There is intentionally **no** command that overwrites a live ledger from a backup — an operator restoring from backup does so explicitly, outside `lab-agent`, so a live ledger is never silently clobbered. To verify a backup file is restorable:

```bash
cd ~/dev/lab-in-the-loop/apps/lab-agent
cp /path/to/backup.db /tmp/restore-drill.db
LAB_AGENT_STATE_DB_PATH=/tmp/restore-drill.db uv run lab-agent integrity
```

A clean `OK: SQLite integrity and audit chain verified.` confirms the backup is a valid, restorable ledger. To actually restore, stop `lab-agent` and `serve-artifacts`, replace the live `LAB_AGENT_STATE_DB_PATH` file with the backup copy (including its `-wal`/`-shm` siblings if present), and restart — attempts/leases/audit history and generated artifact records/tokens/widget mappings resume exactly as of the backup, so any trigger completed after the backup was taken is safely reprocessed (not silently lost) rather than duplicated, because completion state for anything *before* the backup is preserved.

## Demo preflight

Check these independent gates before a governed demo:

1. **Classification and provider allowlist:** the exact `--canvas` id has a `public`, `internal`, or `restricted` classification, and the selected provider's configured allowlist permits that class.
2. **Artifact reachability:** `LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL` is configured and reachable by intended Canvus clients.
3. **Pricing approval:** an operator-approved `LAB_AGENT_PRICING_VERSION` is set and the selected model has an entry in `LAB_AGENT_MODEL_PRICING`.

### `canvus-mcp`

```bash
cd ~/dev/lab-in-the-loop/apps/canvus-mcp
uv run pytest -q
uv run ruff check canvus_mcp tests
uv run mypy canvus_mcp
```

### `lab-agent`

```bash
cd ~/dev/lab-in-the-loop/apps/lab-agent
uv run pytest -q
uv run ruff check lab_agent tests
uv run mypy lab_agent
```

## Troubleshooting

### Claude Code shows MCP server but tool calls fail

Likely cause: the session's tool schema was created before MCP registration.

Fix:

1. Stop Claude Code fully.
2. Ensure `canvus-mcp` is running.
3. Run `claude mcp list` from a shell.
4. Start a fresh Claude Code session.

### `canvus-mcp` cannot connect to Canvus

Check:

- `CANVUS_API_URL` includes `/api/v1` if required by the server.
- `CANVUS_API_KEY` is valid.
- `CANVUS_VERIFY_SSL=false` only for self-signed dev certs.
- Network can reach the server from local machine.

### Downloads appear in git status

Downloads and ingestion runtime artifacts should be ignored under:

```text
apps/canvus-mcp/downloads/
downloads/
apps/canvus-mcp/.state/
.state/
```

If new generated paths appear, add them to `.gitignore` before staging.

### Model keeps continuing loop

Controls:

- Strengthen prompt or decision criteria in `lab_agent/prompts.py`.
- Lower `LAB_AGENT_LOOP_MAX_ROUNDS` for demos.
- Ensure result artifacts contain enough signal to decide plateau/answer/uncertainty; for legacy canvases this means result Note text.

### No setup appears after connecting RagCluster to idea

Check:

- RagCluster widget title starts with configured marker, default `RAGCluster_`.
- Idea note marker matches configured idea convention, default `{idea: ...}`.
- Connector direction is `RAGCluster_ → idea note`, not reverse.
- `scan_experiment_workflow` returns the idea under `ideas_needing_setup`.

### No result appears after connecting setup to robot

Check:

- Setup Browser or legacy Note title starts with `[EXP:Setup`.
- Robot widget title starts with `Robot_`.
- Connector direction is `setup → robot`.
- There is no existing result connected for that setup.

### Command exits with `STARTUP FAILED: ...`

The durable ledger's open/migrate/integrity/audit-chain check failed before any canvas work started (deliberate fail-closed behavior). Diagnose with:

```bash
uv run lab-agent integrity
```

If integrity is clean but the file was missing or newly created, this is expected on first run (a fresh ledger is created and migrated automatically) — re-run the failing command. If `integrity` itself reports a problem, restore from the most recent backup (see "Restore drill" above) rather than deleting the ledger — deleting it discards attempt/lease/audit history, not just the "corrupt" part.

### A trigger never seems to be retried (quarantined)

After `LAB_AGENT_MAX_ATTEMPTS` consecutive failures, a trigger stops being retried automatically and is quarantined instead — this is intentional (prevents an unfixable trigger from looping forever). Check and clear it:

```bash
uv run lab-agent list-quarantined --canvas <canvas-id>
# inspect the fixed safe failure category in the output, fix the underlying cause if any, then:
uv run lab-agent reset --canvas <canvas-id> --trigger <trigger_id>
```

The next `once`/`watch` cycle picks the reset attempt back up.

### Second instance appears to do nothing

Only one runtime instance may hold the write lease for a given canvas (`LAB_AGENT_CANVAS_LEASE_TTL_SECONDS`, default 180s, renewed every cycle). A second instance started against the same `--canvas` while the first is live logs a lease-denied cycle and performs zero writes — this is expected multi-instance safety, not a bug. Confirm via:

```bash
uv run lab-agent integrity   # ledger is healthy
```

and check the audit log (or wait for the first instance's lease to expire/release) rather than starting a third instance.

## Operational safety

- Never commit `.env` or downloaded internal data.
- Treat every canvas write as user-visible.
- Use `once` first on a demo canvas before `watch` on an active canvas.
- Keep `LAB_AGENT_WET_LAB_EXECUTION_ENABLED=false` and `LAB_AGENT_PHASE8_EXECUTION_ENABLED=false` unless operating the reviewed local 7A dry-run milestone. The deterministic validation/approval infrastructure is a gate, and 7A outputs are **DRY RUN / MOCK — NOT MEASURED**; neither claims scientific validation, measured evidence, or real-lab authorization.
- Do not treat Canvas topology or Browser projections as approval evidence. Verify credentials through an `IdentityProvider`; credentials must never be persisted.
- For real execution, complete the external child plans: 7B real Flywheel/HPC analysis, 7C real knowledge store, 7D real lab/robot integration, production identity/credential approval, retention/locality policy, and hosted integration. Do not infer any of those from 7A contracts or deterministic adapters.
- Never commit the durable ledger (`LAB_AGENT_STATE_DB_PATH` and its `-wal`/`-shm` siblings, or any `*.db`/backup file) — it is git-ignored by default; if you must inspect it, treat it as operational data, not a document to paste elsewhere. Its audit log stores only ids/hashes/reasons/counts by design (never note text, model payloads, or credentials), but attempt/lease metadata can still reveal canvas ids and timing.
- Take a `lab-agent backup` before any manual maintenance on a canvas's triggers/artifacts, and periodically in production, since there is no automatic backup schedule built in.
- Treat token-bearing artifact URLs as secrets: do not copy them into tickets, logs, audit payloads, model prompts, or reports.
- Keep notification logs and operator output metadata-only: use safe counts/statuses and fixed failure categories, never SMTP credentials, raw SMTP responses, email bodies beyond their already-minimal metadata, model content, or capability URLs.
- Keep `LAB_AGENT_ARTIFACT_BIND_HOST` private unless an intentional ingress is in front of it. Production ingress must provide HTTPS/private network access; live external Canvus reachability and TLS verification are deployment gates, not guaranteed by local tests.
- Serve artifact CSS/JS from the same origin as the artifact HTML; do not add third-party assets without a CSP/security review.
- Treat the implementation-plan Phase 6 / roadmap Phase 4c ingestion DB, cache, raw files, and PDF password file as protected operational data. Do not copy their paths, raw bytes, or capability URLs into model input, audit data, tickets, or reports. There is no automatic ingestion retention/cleanup: the operator owns backup, retention, disk monitoring, and pruning policy.
- Keep this topology single-host. Evaluate an external queue/object-store migration only from measured sustained backlog/throughput, disk pressure, availability/SLO failure, or a multi-host requirement; the repository provides neither that infrastructure nor numerical migration thresholds.
- Use the migration script in dry-run mode first, then mirror apply only on a backed-up state DB. Phase 3 has no destructive migration path for legacy Notes.
