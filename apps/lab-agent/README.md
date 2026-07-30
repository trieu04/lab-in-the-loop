# lab-agent

A **model-agnostic experiment-loop agent** that drives the
[`canvus-mcp`](../canvus-mcp) tools through an LLM tool-use loop to run the
connector-driven Lab-in-the-Loop workflow on a Canvus canvas.

## The workflow it drives

The canvas is the control surface; the agent watches it and reacts to connectors.

```
docs ─► RAGCluster_        (knowledge base — ingest handled elsewhere)
RAGCluster_ ─► {idea:…}     user's idea note
        │  (agent grounds on the cluster + idea)
        ▼
   [EXP:Setup v001] ─► Robot_ ─► [EXP:Result v001]
        ▲                                  │
        └──────── user connects result ►setup = LOOP
                                           ▼
      agent runs rounds: setup → robot → result → analyse →
      CONTINUE (next setup) … or STOP (LLM decides) ─► [EXP:Closed]
```

- **Idea → Setup:** the agent grounds on the RagCluster's feeders and emits an
  `ExperimentSetup` (inputs, conditions, steps, parameters) → `[EXP:Setup vNNN]`.
- **Setup → Robot → Result:** a `Robot_` widget is a **mock** run; the agent
  writes an `[EXP:Result vNNN]` note (plausible mock observations/metrics).
- **Result → Setup (a loop):** detected by the new canvus-mcp tool
  `detect_experiment_loops` / `scan_experiment_workflow`. The agent then runs
  rounds automatically. After each round it asks the model for a `LoopDecision`
  (continue/stop) — **the model is the stop condition** (UC step 3);
  `LAB_AGENT_LOOP_MAX_ROUNDS` is only a runaway backstop.

**Design principle — agentic reads, orchestrated writes:** the model only reads
(to ground); the orchestrator performs every canvas write.

**Model-agnostic (UC §10):** the loop depends only on the `ModelAdapter` protocol
(`adapters/base.py`). `openai` and `claude` ship today; select with
`LAB_AGENT_MODEL_PROVIDER` or `--provider`.

## Configure

Copy `.env.example` to `.env`. The MCP URL below is suitable for a local
`canvus-mcp` development server; governed **model-provider** endpoints are a
separate authorization boundary. OpenAI permits approved HTTP(S) URLs, while
other providers require HTTPS.

```bash
LAB_AGENT_MCP_URL=http://127.0.0.1:8931/mcp   # a running canvus-mcp server
LAB_AGENT_MODEL_PROVIDER=openai               # or "claude"
LAB_AGENT_OPENAI_API_KEY=sk-...
LAB_AGENT_OPENAI_MODEL=gpt-4o-mini
```

For a governed model dispatch, also configure the exact canvas classification,
an approved provider endpoint, the provider's permitted classifications, and a
complete versioned price entry for the selected model. The shipped empty pricing
table is intentional: it blocks dispatch rather than treating calls as free. A
local OpenAI-compatible server may use HTTP or HTTPS; configure the same URL in
both `LAB_AGENT_OPENAI_BASE_URL` and the `openai` entry in
`LAB_AGENT_PROVIDER_ENDPOINTS`, and keep plain HTTP on a trusted network.

## Runtime classification

### Mandatory governed dispatch gates — no bypass

Every model call from `once` or `watch` uses the governed adapter. Before any
provider receives content, the runtime requires all of the following:

- classified canvas/source evidence authorized for the selected provider;
- an explicitly approved provider endpoint (OpenAI HTTP(S), other providers HTTPS);
- complete, known input/output pricing under a versioned rate table;
- a durable model-call intent and token/cost reservation; and
- deterministic task-stage routing, with fallback only before submission.

MCP results and arguments, model tool calls, transcripts, and evidence are
bounded. Durable retries and reconciliation avoid blind redispatch after an
uncertain submission. Numeric token, cost, and wall-time budgets are optional
limits, not overrides: omitting one means no limit for that dimension, but
never bypasses locality, endpoint, pricing, intent, or reservation checks.
Pricing estimates support reservations and later reconciliation; they are not
provider invoices.

### Core runtime

The core runtime is Canvus plus `canvus-mcp`, a governed OpenAI or Claude
provider, `lab-agent once` or `watch`, and the single-host SQLite ledger
(leases, attempts, intents, audit, and generated-artifact records). Generated
Browser artifacts also require a reachable public artifact URL and the artifact
server when clients must view them. In authenticated deployments, MCP writes
need appropriately scoped trusted-service authorization.

For production, configure a named `LAB_AGENT_TENANT_ID` and an explicit
`LAB_AGENT_ALLOWED_CANVAS_IDS` allowlist. The `default` tenant with an empty
allowlist is retained only as legacy, unbound development behavior.

### Optional limits and extensions

Routing preferences, loop/concurrency tuning, lease/retry tuning, and numeric
run/canvas token, cost, or wall-time ceilings are optional limits or runtime
tuning. Extensions are independent:

- deterministic in-silico structural validation is **on by default** and may
  be disabled for new scheduling with `LAB_AGENT_IN_SILICO_VALIDATION_ENABLED=false`;
- legacy mock-result execution is off by default;
- the Phase 8 lifecycle is off by default and supports only deterministic
  `dry_run` mode;
- SMTP is off by default; and
- the separate ingestion worker, LightRAG, and real lab, Flywheel, and
  knowledge-store integrations are optional or unavailable as noted below.

Disabling in-silico validation skips only new validation scheduling. Existing
validation and approval evidence stays durable and can reconcile or proceed
through the independent approval and execution gates. The deterministic adapter
checks proposal structure (for example, steps, readouts, and review fields); it
is neither scientific simulation nor measured evidence. Loops and approval
reconciliation have no feature flag. `canvus-mcp` initializes ingestion storage
and cache even if no separate ingestion worker is running.

## Run

Start a `canvus-mcp` server (`cd ../canvus-mcp && uv run canvus-mcp`), then:

```bash
uv sync --extra dev
uv run lab-agent watch --canvas <canvas-id>   # poll forever, drive the workflow
uv run lab-agent once  --canvas <canvas-id>   # a single poll cycle
```

Seed a canvas with a `RAGCluster_` image (+ a doc), an idea note
`{idea: Combine A with B}` connected `RagCluster → idea`, and a `Robot_` widget.
Watch `[EXP:Setup v001]` → `[EXP:Result v001]` appear; then connect
`result → setup` and the agent auto-runs rounds until the model stops.

## Tools it consumes

Read-only (grounding, exposed to the model): `scan_server`, `list_canvases`,
`check_ragcluster_connections`, `check_widget_connections`, `get_note`,
`get_widget`, `download_pdf`.

Workflow detection: `scan_experiment_workflow`, `detect_experiment_loops`.

Write (orchestrator-only): `create_note`, `create_connector`.

## Test

```bash
uv run pytest && uv run ruff check lab_agent && uv run mypy lab_agent
```

## Out of scope / unavailable extensions

Real robot/lab execution, real scientific or digital-twin in-silico validation,
real vector/graph RAG retrieval (including LightRAG), Flywheel/HPC analysis,
and a real knowledge-store integration are not installed. Ollama/vLLM have no
dedicated adapters; an OpenAI-compatible deployment uses the governed `openai`
adapter and therefore accepts an approved HTTP(S) endpoint for OpenAI.

Before any real or sandbox execution adapter is introduced, production
eligibility must be enforced by execution authorization. Current reachable
execution adapters remain legacy mock or Phase 8 dry-run only; neither creates
measured evidence.
