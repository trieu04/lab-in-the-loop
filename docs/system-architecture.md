# System Architecture

## Overview

Lab-in-the-Loop turns a Canvus canvas into an experiment-control surface. The canvas stores knowledge scopes, ideas, experiment setup notes, mock robot results, and loop closure decisions. The system has two runtime apps:

1. `canvus-mcp` — MCP server exposing Canvus operations as model-callable tools.
2. `lab-agent` — watcher/orchestrator that drives the experiment loop through those tools.

The design deliberately separates reads from writes:

- Model/agentic phase: read canvas state, retrieve grounding context, reason over setup/result text, emit structured decisions.
- Orchestrator phase: create notes/connectors, enforce idempotency, and keep loop state deterministic.

## Current MVP vs. target harness (status note)

Everything below this line up to "Current limitations" describes the **current implementation** as it exists in code today: two runtime apps (`canvus-mcp`, `lab-agent`), OpenAI/Claude adapter-factory model choices, mock robot execution, and in-memory loop idempotency.

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
  └─ creates notes/connectors on behalf of orchestrators
  │
  │ MCP streamable HTTP
  ▼
apps/lab-agent
  ├─ polls scan_experiment_workflow
  ├─ dispatches pending workflow steps
  ├─ grounds model using read-only tools
  ├─ writes setup/result/closed nodes via orchestrated writes
  └─ decides continue/stop through the configured model adapter
  │
  ├─ OpenAI adapter
  └─ Claude adapter
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
| `lab_agent/cli.py` | CLI commands: `once`, `watch` |
| `lab_agent/config.py` | MCP URL, provider, model, bounds |
| `lab_agent/mcp_client.py` | MCP transport client |
| `lab_agent/watch.py` | Poll loop and pending-trigger dispatcher |
| `lab_agent/orchestrator.py` | Setup generation, mock robot result, loop continuation/closure |
| `lab_agent/orchestrator_support.py` | Orchestrator helpers: schema coercion (`coerce`), round→version formatting (`version`), `LoopSummary` |
| `lab_agent/nodes.py` | Canvas write surface: `create_node`, `connect`, `read_note_text` — the only place `lab-agent` calls `create_note`/`create_connector` |
| `lab_agent/tool_bridge.py` | Selects read-only tools exposed to the model |
| `lab_agent/loop.py` | Tool-use loop and structured-output emission |
| `lab_agent/prompts.py` | System prompts for setup/result/decision phases |
| `lab_agent/render.py` | Renders structured models into canvas-note text |
| `lab_agent/models/experiment.py` | `ExperimentSetup`, `ExperimentResult`, `LoopDecision` |
| `lab_agent/models/states.py` | `DecisionState` enum (UC §12 lifecycle: `DRAFT` … `CLOSED`/`REJECTED`) written into note bodies as a `Status:` line |
| `lab_agent/adapters/*` | OpenAI and Claude model adapters; `factory.py` selects by `settings.model_provider` |

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
model emits ExperimentSetup
        │
        ▼
lab-agent creates `[EXP:Setup v001]` note and connector idea → setup
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
lab-agent creates `[EXP:Result vNNN]` and connector robot → result
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
        ├─ STOP     → create `[EXP:Closed]`, connect result → closed
        └─ CONTINUE → create `[EXP:Setup vNNN+1]`, connect result → setup, run mock robot
```

## State and idempotency

The canvas is the durable source of workflow state. The agent treats connector presence as the state transition signal.

Idempotency rules:

- An idea is processed only if it has no existing setup.
- A setup is run only if it has no existing result.
- A detected loop connector is processed once per watcher session.
- Writes happen one node at a time: setup note, result note, closed note, connector.
- The watcher catches transient errors and continues polling.

## Trust boundaries

| Boundary | Risk | Control |
|---|---|---|
| Canvus credentials | Secret leakage | Kept in `apps/canvus-mcp/.env`, ignored by git |
| MCP write tools | Unintended canvas mutation | Orchestrator-only write usage; model gets read tools for grounding |
| Downloaded PDFs/images | Sensitive data | Written to ignored `downloads/`; bytes not in model context by default |
| Model output | Hallucinated domain facts | Ground via RagCluster; flag ambiguous terms; structured schemas |
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
- A durable workflow state machine, with idempotency, retry, resume, failure recovery, and audit/version history (today: in-memory `processed_loops`, no persistence).
- Policy and approval gates (today: none — mock robot only, no human-approval gate in code).
- Token/resource budgets, model routing, cost thresholds, loop limits, and stop conditions (today: only `LAB_AGENT_LOOP_MAX_ROUNDS` as a runaway backstop, plus the model's own `LoopDecision`).
- Retrieval/grounding against internal wiki, knowledge graph, acronym dictionary, documents, and experiment history (today: RagCluster connector context only, no KG/wiki/vector DB).
- Context packaging and evidence tracking, model adapter/router selection, and structured-output normalization (today: adapter factory + `orchestrator_support.coerce`, provider-specific but not policy-aware).
- Tool/action routing to Canvus, Flywheel, in-silico simulation, and robotic/human lab execution (today: mock only; no Flywheel/in-silico wiring).
- Async, chunked, cached, resumable multimodal ingestion rather than one model call per document (today: whole-file downloads via `canvus_mcp/downloads.py`; no chunking/caching/resume).

Under this proposal, the Claude Code skill/MCP registration becomes an **optional developer/operator/demo client** over the same harness — not the production runtime and not the owner of business logic, which stays in the harness and the shared `canvus-mcp` tool boundary. Explicit owner ratification of this direction is absent from the source transcript; do not treat it as approved.

## Current limitations

- Robot execution is mock only.
- Retrieval is canvas/RagCluster-oriented, not a full vector DB or knowledge graph runtime.
- Flywheel/in-silico gates are represented in docs and roadmap, not implemented as live integrations.
- Loop connector idempotency is per watcher session; persistent processed-loop state is a future improvement.
- No durable workflow state, retry/resume/audit, token/resource governance, or multi-user observability exists yet — these are the target-harness items above, not current behavior.

## References

- [Experiment workflow](experiment-workflow.md)
- [Setup and operations](setup-and-operations.md)
- [Canonical use case specification](lab-in-the-loop-use-case-specification.md)
- [Original meeting vision](notes/use-case-lab-in-the-loop.md)
- [Canvus-serving integration](canvus-serving-integration.md)
