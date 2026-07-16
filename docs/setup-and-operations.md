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
LAB_AGENT_WATCH_POLL_SECONDS=30
LAB_AGENT_LOOP_MAX_ROUNDS=25
```

`LAB_AGENT_LOOP_MAX_ROUNDS` is only a runaway backstop. The model's `LoopDecision` is the intended stop condition.

## Run `lab-agent`

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

Stop with Ctrl-C. The watcher finishes the current operation before exiting if the process receives normal interruption.

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
7. After setup is created, connect `[EXP:Setup v001] → Robot_`.
8. After result is created, connect `[EXP:Result v001] → [EXP:Setup v001]` to request loop analysis.

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
- Ensure result notes contain enough signal to decide plateau/answer/uncertainty.

### No setup appears after connecting RagCluster to idea

Check:

- RagCluster widget title starts with configured marker, default `RAGCluster_`.
- Idea note marker matches configured idea convention, default `{idea: ...}`.
- Connector direction is `RAGCluster_ → idea note`, not reverse.
- `scan_experiment_workflow` returns the idea under `ideas_needing_setup`.

### No result appears after connecting setup to robot

Check:

- Setup note title starts with `[EXP:Setup`.
- Robot widget title starts with `Robot_`.
- Connector direction is `setup → robot`.
- There is no existing result connected for that setup.

## Operational safety

- Never commit `.env` or downloaded internal data.
- Treat every canvas write as user-visible.
- Use `once` first on a demo canvas before `watch` on an active canvas.
- Keep robot execution mock until real lab integration has explicit approval and safety gates.
- For wet-lab/Flywheel production, add human approval and in-silico validation gates before execution.
