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
~/dev/lap-in-the-loop
├── apps/canvus-mcp
├── apps/lab-agent
├── docs
├── integrations
└── assets
```

## Configure `canvus-mcp`

```bash
cd ~/dev/lap-in-the-loop/apps/canvus-mcp
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

## Run `canvus-mcp`

```bash
cd ~/dev/lap-in-the-loop/apps/canvus-mcp
uv sync --extra dev
uv run canvus-mcp
```

Default MCP endpoint:

```text
http://127.0.0.1:8931/mcp
```

### Register with Claude Code (optional)

Registering `canvus-mcp` with Claude Code is only needed for the interactive/demo/operator Claude Code skill path. It is not required to run `canvus-mcp` + `lab-agent` as the primary workflow — skip this subsection entirely if you only need the headless `lab-agent` watcher.

HTTP transport:

```bash
claude mcp add --transport http -s user canvus http://127.0.0.1:8931/mcp
```

Stdio alternative:

```bash
claude mcp add -s user canvus -- \
  uv run --directory /home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp canvus-mcp --stdio
```

After registering, fully restart Claude Code. Resuming an old session may not rebuild the MCP tool schema.

## Configure `lab-agent`

```bash
cd ~/dev/lap-in-the-loop/apps/lab-agent
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

`LAB_AGENT_OPENAI_BASE_URL` can point at any OpenAI-compatible endpoint (e.g. a local Ollama or vLLM server exposing an OpenAI-compatible API) while keeping `LAB_AGENT_MODEL_PROVIDER=openai` — this is provider-neutral operation via the existing `openai` adapter, not a separate named adapter.

For Claude:

```bash
LAB_AGENT_MCP_URL=http://127.0.0.1:8931/mcp
LAB_AGENT_MODEL_PROVIDER=claude
LAB_AGENT_ANTHROPIC_API_KEY=sk-ant-...
LAB_AGENT_ANTHROPIC_MODEL=claude-sonnet-4-5
```

Runtime bounds:

```bash
LAB_AGENT_MAX_TOOL_STEPS=8
LAB_AGENT_MODEL_MAX_OUTPUT_TOKENS=4096  # Settings default; optional in .env
LAB_AGENT_WATCH_POLL_SECONDS=30
LAB_AGENT_LOOP_MAX_ROUNDS=25
```

`LAB_AGENT_LOOP_MAX_ROUNDS` is only a runaway backstop. The model's `LoopDecision` is the intended stop condition.

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

All six have safe defaults (shown above) — none are required to run `lab-agent`, and they may be omitted from `.env` unless overriding behavior. `LAB_AGENT_STATE_DB_PATH` is relative to the process's working directory unless given as an absolute path; keep it out of version control (the default `.state/` prefix is already git-ignored). The same SQLite file also stores canonical generated-artifact records, token hashes, and Browser widget mappings.

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
cd ~/dev/lap-in-the-loop/apps/lab-agent
uv sync --extra dev
uv run lab-agent once --canvas <canvas-id>
```

Continuous watcher:

```bash
uv run lab-agent watch --canvas <canvas-id>
```

Artifact HTML service, in a separate long-running process using the same state DB:

```bash
uv run lab-agent serve-artifacts
# or override bind only; public URL still comes from LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL
uv run lab-agent serve-artifacts --host 127.0.0.1 --port 8600
```

Health check:

```text
GET /healthz -> {"status":"ok"}
```

The health response is intentionally non-sensitive. It does not expose DB paths, artifact ids, tokens, or config.

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

# Write a consistent hot backup of the ledger (safe to run against a live, running watcher).
uv run lab-agent backup --to <path>
```

`<trigger_id>` comes from `list-quarantined`'s output (e.g. `idea_setup:<widget_id>`, `setup_run:<widget_id>`, `loop:<connector_id>`). `reset` only succeeds on an attempt that is actually quarantined; it prints `ERROR: ...` and exits `1` otherwise — there is no bulk/blind reset.

### Restore drill

There is intentionally **no** command that overwrites a live ledger from a backup — an operator restoring from backup does so explicitly, outside `lab-agent`, so a live ledger is never silently clobbered. To verify a backup file is restorable:

```bash
cd ~/dev/lap-in-the-loop/apps/lab-agent
cp /path/to/backup.db /tmp/restore-drill.db
LAB_AGENT_STATE_DB_PATH=/tmp/restore-drill.db uv run lab-agent integrity
```

A clean `OK: SQLite integrity and audit chain verified.` confirms the backup is a valid, restorable ledger. To actually restore, stop `lab-agent` and `serve-artifacts`, replace the live `LAB_AGENT_STATE_DB_PATH` file with the backup copy (including its `-wal`/`-shm` siblings if present), and restart — attempts/leases/audit history and generated artifact records/tokens/widget mappings resume exactly as of the backup, so any trigger completed after the backup was taken is safely reprocessed (not silently lost) rather than duplicated, because completion state for anything *before* the backup is preserved.

## Seed a canvas

Minimum setup:

1. Create a `RAGCluster_` image widget.
2. Connect one or more PDF/note/doc widgets into the RagCluster.
3. Create a note like:

   ```text
   {idea: Design the next experiment for lung fibrosis using the connected internal knowledge.}
   ```

4. Connect `RAGCluster_ → idea note`.
5. Place a widget titled with `Robot_` for mock execution.
6. Start `lab-agent watch`.
7. After the setup Browser artifact is created, connect `[EXP:Setup v001] → Robot_`.
8. After the result Browser artifact is created, connect `[EXP:Result v001] → [EXP:Setup v001]` to request loop analysis.

## Legacy generated Note migration

Legacy canvases can contain generated Setup/Result/Closed Notes. They remain readable by the scanner and by `lab-agent` fallback reads. Migration is mirror-first and non-destructive.

Read-only inventory (default; no DB, Browser, connector, or Note writes):

```bash
python scripts/migrate-generated-notes-to-browser-artifacts.py --canvas <canvas-id>
```

Mirror apply (requires `LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL`; creates Browser mirrors and equivalent connectors; never deletes, archives, or edits the original Notes/connectors):

```bash
python scripts/migrate-generated-notes-to-browser-artifacts.py --canvas <canvas-id> --apply
```

The script prints ids/counts/status only. It never prints token-bearing capability URLs. Re-running is intended to converge through durable idempotency and in-place Browser repair. `--apply` is the operator's explicit confirmation for mirror writes; Phase 3 has no destructive archive/delete mode and no extra deletion confirmation prompt.

## Test and quality commands

### `canvus-mcp`

```bash
cd ~/dev/lap-in-the-loop/apps/canvus-mcp
uv run pytest -q
uv run ruff check canvus_mcp tests
uv run mypy canvus_mcp
```

### `lab-agent`

```bash
cd ~/dev/lap-in-the-loop/apps/lab-agent
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

Downloads should be ignored under:

```text
apps/canvus-mcp/downloads/
downloads/
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
- Keep robot execution mock until real lab integration has explicit approval and safety gates.
- For wet-lab/Flywheel production, add human approval and in-silico validation gates before execution.
- Never commit the durable ledger (`LAB_AGENT_STATE_DB_PATH` and its `-wal`/`-shm` siblings, or any `*.db`/backup file) — it is git-ignored by default; if you must inspect it, treat it as operational data, not a document to paste elsewhere. Its audit log stores only ids/hashes/reasons/counts by design (never note text, model payloads, or credentials), but attempt/lease metadata can still reveal canvas ids and timing.
- Take a `lab-agent backup` before any manual maintenance on a canvas's triggers/artifacts, and periodically in production, since there is no automatic backup schedule built in.
- Treat token-bearing artifact URLs as secrets: do not copy them into tickets, logs, audit payloads, model prompts, or reports.
- Keep `LAB_AGENT_ARTIFACT_BIND_HOST` private unless an intentional ingress is in front of it. Production ingress must provide HTTPS/private network access; live external Canvus reachability and TLS verification are deployment gates, not guaranteed by local tests.
- Serve artifact CSS/JS from the same origin as the artifact HTML; do not add third-party assets without a CSP/security review.
- Use the migration script in dry-run mode first, then mirror apply only on a backed-up state DB. Phase 3 has no destructive migration path for legacy Notes.
