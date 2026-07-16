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

Copy `.env.example` to `.env`:

```bash
LAB_AGENT_MCP_URL=http://127.0.0.1:8931/mcp   # a running canvus-mcp server
LAB_AGENT_MODEL_PROVIDER=openai               # or "claude"
LAB_AGENT_OPENAI_API_KEY=sk-...
LAB_AGENT_OPENAI_MODEL=gpt-4o-mini
```

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

## Out of scope (future rounds)

Real robot/lab integration (currently mock), real vector/graph RAG retrieval,
Flywheel/in-silico gates, Ollama/vLLM adapters.
