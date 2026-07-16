# Lab-in-the-Loop

Lab-in-the-Loop is a connector-driven Canvus workflow for turning internal knowledge into experiment plans, running mock robot/lab rounds, interpreting results, and deciding whether to continue or close the loop.

The repository was split out from `rag-canvus` into `~/dev/lap-in-the-loop` so the experiment-loop system can evolve independently from the base RAG serving application.

## Architecture direction (proposed, pending owner confirmation)

A 2026-07-16 architecture-recovery review (see [changelog](docs/project-changelog.md)) concluded that Lab-in-the-Loop should evolve as an **independent, provider-neutral harness/orchestrator** — `lab-agent` today, a headless service in the target state — with Claude Code skill/MCP registration kept as an **optional developer/operator/demo interface**, not the production runtime or source of truth. Shared workflow logic, policy, grounding, schemas, and tool routing belong in the harness, not the Claude Code client.

This is the final recommendation of that review, but the transcript it came from contains **no explicit owner ratification** ("approved", "proceed") — treat it as proposed direction, not an implemented or approved fact, until the project owner confirms it. See [System architecture](docs/system-architecture.md) for the current-vs-target split and [Roadmap](docs/development-roadmap.md) for the phases this implies.

### Current implementation vs. target harness

What exists today is an MVP, not the target production harness:

- Model providers are the `openai`/`claude` adapter-factory choices in `lab-agent`; OpenAI-compatible `base_url` covers Ollama/vLLM/Azure-style endpoints, but there are no dedicated named adapters for them.
- Robot/lab execution is mock by design.
- Loop-connector idempotency (`processed_loops`) is in-memory and session-local — it does not survive a restart.
- There is no durable workflow state store, retrieval/knowledge-graph grounding, token/resource governance, Flywheel/in-silico integration, or multi-user observability yet. These are future-phase items (see roadmap).

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
docs / knowledge ─► RAGCluster_ ─► {idea: ...}
                                  │
                                  ▼
                            [EXP:Setup v001]
                                  │
                                  ▼
                              Robot_
                                  │
                                  ▼
                            [EXP:Result v001]
                                  │
                  user connects result ─► setup
                                  │
                                  ▼
                    model decides CONTINUE or STOP
```

- `canvus-mcp` scans the Canvus canvas, reads notes/widgets/PDFs, detects experiment triggers, and creates notes/connectors.
- `lab-agent` polls `scan_experiment_workflow`, grounds on RagCluster context, writes setup/result/closed notes, and asks the configured model for loop decisions.
- Robot execution is currently mock by design. Real lab/robot/Flywheel integration is a future phase.

## Quick start

### 1. Start the Canvus MCP server

```bash
cd ~/dev/lap-in-the-loop/apps/canvus-mcp
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
cd ~/dev/lap-in-the-loop/apps/lab-agent
cp .env.example .env
# Fill LAB_AGENT_MCP_URL and model API key
uv sync --extra dev
uv run lab-agent once --canvas <canvas-id>
uv run lab-agent watch --canvas <canvas-id>
```

## Canvas conventions

| Marker | Widget | Meaning |
|---|---|---|
| `RAGCluster_` | Image | Knowledge scope / retrieval cluster |
| `{idea: ...}` | Note | User experiment idea grounded in a RagCluster |
| `[EXP:Setup vNNN]` | Note | Experiment setup for round N |
| `Robot_` | Widget | Mock robot/lab execution target |
| `[EXP:Result vNNN]` | Note | Mock result for round N |
| `[EXP:Closed]` | Note | Loop closure decision and reason |

The active trigger graph is connector-defined. The agent only acts on connected nodes, not loose notes.

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
- The agent writes to the canvas only through `create_note` and `create_connector`.
- Grounding must come from internal Canvus/RagCluster context; ambiguous domain terms should be flagged instead of guessed.

## Current status

- Files extracted to `~/dev/lap-in-the-loop`: `apps/canvus-mcp` and `apps/lab-agent` migrated from `rag-canvus`.
- Documentation initialized from the Lab-in-the-Loop use case and existing app READMEs.
- `integrations/canvus-serving-experiment-prepare` preserves the serving-side `{exp:}` trigger/action work for optional re-application to `rag-canvus`.
- The local git repository has **no commits yet** — all files above are untracked (`git status` shows `??`). Extraction has produced files on disk; it has not produced a committed release.
