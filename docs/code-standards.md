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

`scan_experiment_workflow` and `detect_experiment_loops` are called directly by the orchestrator/watcher (`lab_agent/watch.py`), not exposed to the model's tool-use loop. `download_image`/`download_asset` exist on `canvus-mcp` but are not currently in the model-facing allowlist. See [experiment workflow](experiment-workflow.md) → "Tool contract" for the full reconciliation.

### Orchestrator-only writes

Writes are controlled by application code (`lab_agent/nodes.py`):

- `create_note`
- `create_connector`

`create_browser` and `create_image` exist as `canvus-mcp` write tools but are not called by `lab-agent`'s orchestrator today; they remain available for direct/manual use only (e.g. the Claude Code skill).

Do not let an unconstrained model decide arbitrary write tool calls.

## Provider and harness boundaries

- Model providers are adapters (`lab_agent/adapters/factory.py`: `openai`, `claude`); no workflow logic may hard-code a specific provider or assume a Claude Code license is available.
- A 2026-07-16 architecture review proposed (pending owner confirmation) that shared workflow logic, policy, grounding, and schemas belong in an independent harness/orchestrator (`lab-agent`, evolved), with the Claude Code skill/MCP registration kept as an optional developer/operator/demo interface only. See [system architecture](system-architecture.md) → "Target harness boundary (proposed)". Do not add business logic to a Claude Code skill that the headless `lab-agent` path cannot also exercise.

## Structured output

Model outputs that drive workflow state should be schema-bound:

- `ExperimentSetup`
- `ExperimentResult`
- `LoopDecision`

If a provider returns invalid schema, fail visibly and leave the canvas state pending so a retry can happen safely.

## Grounding and scientific caution

- Do not invent domain facts.
- Do not guess acronym meanings.
- State uncertainty in generated setup/result notes.
- Prefer lower confidence over unsupported specificity.
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

## Documentation updates

Update docs when changing:

- canvas markers;
- MCP tool names or schemas;
- environment variables;
- loop semantics;
- provider configuration;
- safety/idempotency behavior;
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
