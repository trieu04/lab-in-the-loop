# Lab-in-the-Loop

Lab-in-the-Loop is a connector-driven Canvus workflow for turning internal knowledge into experiment plans, running mock robot/lab rounds, interpreting results, and deciding whether to continue or close the loop.

The repository was split out from `rag-canvus` into `~/dev/lab-in-the-loop` so the experiment-loop system can evolve independently from the base RAG serving application.

## Architecture direction (proposed, pending owner confirmation)

A 2026-07-16 architecture-recovery review (see [changelog](docs/project-changelog.md)) concluded that Lab-in-the-Loop should evolve as an **independent, provider-neutral harness/orchestrator** — `lab-agent` today, a headless service in the target state — with Claude Code skill/MCP registration kept as an **optional developer/operator/demo interface**, not the production runtime or source of truth. Shared workflow logic, policy, grounding, schemas, and tool routing belong in the harness, not the Claude Code client.

This is the final recommendation of that review, but the transcript it came from contains **no explicit owner ratification** ("approved", "proceed") — treat it as proposed direction, not an implemented or approved fact, until the project owner confirms it. See [System architecture](docs/system-architecture.md) for the current-vs-target split and [Roadmap](docs/development-roadmap.md) for the phases this implies.

### Current implementation vs. target harness

What exists today is an MVP, not the target production harness:

- Model providers are the `openai`/`claude` adapter-factory choices in `lab-agent`. A provider-neutral governed gateway selects the configured provider/model by task stage (`setup`, `mock_result`, or `loop_decision`) only after data-locality authorization; OpenAI-compatible deployments remain the `openai` adapter, not dedicated named adapters.
- Every production model call is intent-guarded, locality-authorized, price-checked, durably reserved, and usage-accounted before a later canvas write. The gateway records normalized provider usage as `exact`, `estimated`, or `unavailable`; estimates are conservative governance inputs, not provider invoices.
- Loop/trigger idempotency, retry, quarantine, single-writer canvas leasing, model-call intents, durable budget reservations, the audit trail, and canonical generated-artifact records survive restarts via a local SQLite ledger (`.state/lab_agent.db`). Run envelopes reset for each trigger; canvas totals and active reservations reconstruct after restart. This is local-disk, single-host scoped; a shared/replicated store for multi-host or multi-writer deployment is still future work.
- The typed validation/approval gate, execution modes, terminal-stop outbox, and SMTP delivery plumbing exist locally. The default validation adapter is a deterministic structural dry run, execution remains disabled unless explicitly enabled, and the legacy path remains a model-generated `MOCK_RESULT`.
- Phase 8 is complete only for milestone **7A**: typed dry-run execution/analysis/knowledge contracts and append-only local persistence, deterministic memory-only lab/Flywheel/knowledge adapters, approval-bound restart-safe orchestration, and safe Browser projections. `LAB_AGENT_PHASE8_EXECUTION_ENABLED` remains `false` by default; this milestone produces **DRY RUN / MOCK — NOT MEASURED** evidence only. It has no provider API, network, robot, wet-lab, raw provider body, credential, capability URL, or measured-scientific-evidence path. Real Flywheel/HPC (7B), knowledge store (7C), lab/robot (7D), production identity/credential approval, retention/locality policy, and hosted integration remain external gates.


## What this repo contains

```text
apps/
├── canvus-mcp/     # MCP server exposing Canvus read/write/workflow tools
└── lab-agent/      # Model-agnostic watcher/orchestrator that drives the loop
docs/
├── notes/
│   └── use-case-lab-in-the-loop.md              # Original meeting vision/source (excluded from normalization)
├── lab-in-the-loop-use-case-specification.md    # Canonical English use-case specification
├── system-architecture.md
├── experiment-workflow.md
├── setup-and-operations.md
├── canvus-serving-integration.md
├── code-standards.md
├── development-roadmap.md
└── project-changelog.md
integrations/
└── canvus-serving-experiment-prepare/  # Patch/source extracted from rag-canvus canvus-serving
assets/
└── images/sample-640x426.jpeg
```

## Core workflow

```text
docs / knowledge ─► RAGCluster_ ─► {idea: ...} | {idea+auto: ...}
                                         │
                                         ▼
                                   [EXP:Setup v001]
                                         │
                         typed validation → scientist → lab lead
                                         │
                            enabled only ─┴─► Robot_ → [EXP:Result v001]
                                         │
                         user connects result ─► setup → CONTINUE or STOP
```

- `canvus-mcp` scans the Canvus canvas, reads notes/widgets/PDFs, detects experiment triggers, and creates or updates Note/Browser/connector widgets.
- `lab-agent` polls `scan_experiment_workflow`, grounds on RagCluster context, validates each canonical setup, writes generated Setup/Result/Closed and generated Needs Input prompt/status artifacts as capability-protected HTML Browser widgets backed by canonical `ArtifactStore` records, and asks the configured model for loop decisions.
- `{idea: ...}` selects legacy/manual execution; `{idea+auto: ...}` selects automatic execution. Unsupported `{idea+...:}` modes on RagCluster-connected ideas fail closed and create a Needs Input request instead of dispatching a setup or robot path; loose/disconnected Notes are ignored.
- Robot execution is still a model-generated mock path: it is disabled by default and requires current validation plus scientist-then-lab-lead approval. Manual mode additionally needs one credential-verified activation for the first round. Real lab/robot/Flywheel integration is a future phase.

## Quick start

### 1. Start the Canvus MCP server

```bash
cd ~/dev/lab-in-the-loop/apps/canvus-mcp
cp .env.example .env
# Fill CANVUS_API_URL and CANVUS_API_KEY
uv sync --extra dev
uv run canvus-mcp
```

The default endpoint is:

```text
http://127.0.0.1:8931/mcp
```

### 2. Register MCP with Claude Code, optional

```bash
claude mcp add --transport http -s user canvus http://127.0.0.1:8931/mcp
```

Use user scope so the server is available from every project. Restart Claude Code after adding a new MCP server so the tool schema is rebuilt.

### 3. Run the lab agent

```bash
cd ~/dev/lab-in-the-loop/apps/lab-agent
cp .env.example .env
# Fill LAB_AGENT_MCP_URL, a model API key, LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL,
# provider endpoint/locality approval, and versioned model pricing.
# See docs/setup-and-operations.md for the governed configuration contract.
uv sync --extra dev
uv run lab-agent serve-artifacts      # separate process; private bind by default
uv run lab-agent once --canvas <canvas-id>
uv run lab-agent watch --canvas <canvas-id>
```

Browser artifact writes require `LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL` to be a base URL reachable by intended Canvus clients. `lab-agent` also exposes operator commands (`integrity`, `list-quarantined`, `reset`, `backup`, `notification-status`, `list-notification-quarantined`, `retry-notification`, and `quarantine-notification`) over its durable local ledger — see [setup and operations](docs/setup-and-operations.md) → "Durable harness operator commands". SMTP notification delivery is disabled unless its complete TLS and address-allowlist policy is configured.

## Canvas conventions

| Marker | Widget | Meaning |
|---|---|---|
| `RAGCluster_` | Image | Knowledge scope / retrieval cluster |
| `{idea: ...}` | Note | User experiment idea grounded in a RagCluster; legacy/manual execution mode |
| `{idea+auto: ...}` | Note | User experiment idea grounded in a RagCluster; automatic execution mode |
| `[EXP:Setup vNNN]` | Browser (generated) or legacy Note | Experiment setup for round N |
| `Robot_` | Widget | Mock robot/lab execution target |
| `[EXP:Result vNNN]` | Browser (generated) or legacy Note | Mock result for round N |
| `[EXP:Closed]` | Browser (generated) or legacy Note | Loop closure decision and reason |
| `[EXP:Needs Input]` | Browser (generated prompt/status) | Infrastructure marker for a generated human-decision request; the human response remains a Note |

The active trigger graph is connector-defined. The agent only acts on connected nodes, not loose notes. User-authored `{idea: ...}` and human approval/review/input responses remain Notes; system-generated Setup/Result/Closed and generated Needs Input status/prompt artifacts use Browser widgets. Legacy generated Notes remain readable during migration. Browser artifacts use stable opaque `/artifacts/{opaque_id}` resource paths backed by `ArtifactStore`; token-bearing capability URLs may rotate and are repaired in place without recreating the Browser widget.

## Useful commands

```bash
# canvus-mcp
cd apps/canvus-mcp
uv run pytest -q
uv run ruff check canvus_mcp tests
uv run mypy canvus_mcp

# lab-agent
cd apps/lab-agent
uv run pytest -q
uv run ruff check lab_agent tests
uv run mypy lab_agent
```

## Documentation map

- [Use case (meeting vision / source)](docs/notes/use-case-lab-in-the-loop.md) — original meeting-notes vision document; kept as-is in `docs/notes/`, not the canonical spec.
- [Use case specification (canonical)](docs/lab-in-the-loop-use-case-specification.md) — standardized, detailed use case specification derived from the vision doc, with implementation-status annotations (`[MVP]`/`[Future]`/`[Proposed]`).
- [System architecture](docs/system-architecture.md) — components, boundaries, data flow.
- [Experiment workflow](docs/experiment-workflow.md) — canvas markers, triggers, idempotency, loop semantics.
- [Setup and operations](docs/setup-and-operations.md) — environment, commands, troubleshooting.
- [Canvus-serving integration](docs/canvus-serving-integration.md) — extracted patch and serving-side `{exp:}` action.
- [Code standards](docs/code-standards.md) — project conventions.
- [Roadmap](docs/development-roadmap.md) — implementation phases.
- [Changelog](docs/project-changelog.md) — migration and feature history.

## Security notes

- Do not commit `.env`, API keys, Canvus tokens, downloaded lab data, or generated outputs.
- `canvus-mcp` uses server-side credentials from its own `.env`; clients only need the MCP URL.
- The agent writes user/legacy Notes through `create_note`, generated artifacts through `create_browser`/`update_browser`, and graph edges through `create_connector`.
- Grounding must come from internal Canvus/RagCluster context; ambiguous domain terms should be flagged instead of guessed.
- Artifact Browser URLs are bearer capabilities: keep the public base URL reachable only by intended Canvus clients, use HTTPS/private ingress in production, and never log or paste token-bearing URLs.
- SMTP notifications send only terminal-closure metadata. Keep SMTP credentials in `.env`; configure encrypted transport and exact sender/recipient allowlists. Delivery is best-effort and logically deduplicated, with automatic retry only for known pre-submit/transient failures; ambiguous outcomes require reconciliation and no exactly-once or at-least-once inbox guarantee is made.

## Current status

- Files extracted to `~/dev/lab-in-the-loop`: `apps/canvus-mcp` and `apps/lab-agent` migrated from `rag-canvus`.
- Documentation initialized from the Lab-in-the-Loop use case and existing app READMEs.
- `integrations/canvus-serving-experiment-prepare` preserves the serving-side `{exp:}` trigger/action work for optional re-application to `rag-canvus`.
- The local git repository has committed history (`git log` shows the extraction commit and subsequent work) — this is no longer an uncommitted extraction. New work lands as conventional commits (`feat:`/`fix:`/`docs:`/`test:`/...); see [changelog](docs/project-changelog.md) for what has shipped.
- Phase 8 milestone 7A is locally complete: contracts-only dry-run lifecycle with mock evidence, not real execution or measured scientific truth. See [system architecture](docs/system-architecture.md) and [roadmap](docs/development-roadmap.md) for the retained external gates.
