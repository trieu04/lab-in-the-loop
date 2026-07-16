# Canvus-Serving Integration

## Overview

This repository primarily runs Lab-in-the-Loop through `canvus-mcp` and `lab-agent`. It also preserves an optional `canvus-serving` integration extracted from the original `rag-canvus` working tree.

The integration adds a serving-side action for:

```text
RAGCluster_ ─► Note `{exp: ...}` ─► OpenAI experiment-prep result note
```

It is stored under:

```text
integrations/canvus-serving-experiment-prepare/
├── canvus-serving-experiment-prepare.patch
└── apps/canvus-serving/
    ├── app/actions/experiment_prepare.py
    └── tests/test_experiment_prepare.py
```

## Purpose

The serving integration is a lightweight bridge for users who want the existing `canvus-serving` watcher to react to an experiment marker without running the separate `lab-agent` loop.

It is not the full closed-loop workflow. It only generates an initial experiment-preparation note from `{exp: ...}` content.

## Behavior

1. User creates a Note containing:

   ```text
   {exp: Design a short lung fibrosis experiment}
   ```

2. User connects:

   ```text
   RAGCluster_ → Note `{exp: ...}`
   ```

3. `canvus-serving` scanner sees a RagCluster-to-Note connector.
4. Parser extracts the `{exp: ...}` payload.
5. Queue enqueues `experiment_prepare` with dedup key:

   ```text
   canvas_id|note_id|exp_hash
   ```

6. Action calls OpenAI with fixed prefix:

   ```text
   Please give a short mockup medical experiment prepare, <exp content>
   ```

7. Action creates a result Note and connects source note → result note.
8. Source note status is updated best-effort from `Analyzing...` to `Completed`.

## Required serving changes

The patch includes changes to these `canvus-serving` files:

```text
apps/canvus-serving/.env.example
apps/canvus-serving/app/actions/query_runner.py
apps/canvus-serving/app/config.py
apps/canvus-serving/app/jobs/queue.py
apps/canvus-serving/app/scanner/engine.py
apps/canvus-serving/app/scanner/parser.py
apps/canvus-serving/pyproject.toml
apps/canvus-serving/uv.lock
apps/canvus-serving/app/actions/experiment_prepare.py
apps/canvus-serving/tests/test_experiment_prepare.py
```

## Configuration

Added env vars:

```bash
CANVUS_OPENAI_API_KEY=
CANVUS_OPENAI_MODEL=gpt-4o-mini
CANVUS_OPENAI_BASE_URL=
```

`CANVUS_OPENAI_BASE_URL` is optional and supports OpenAI-compatible endpoints.

## Tests included

`tests/test_experiment_prepare.py` covers:

- `{exp: ...}` parser extraction.
- Ignoring legacy `{{ query }}` syntax.
- Empty/oversized marker rejection.
- Dedup key generation.
- Duplicate job suppression.
- Action success with mocked OpenAI.
- Missing payload fields.
- Missing API key.
- Scanner dispatch for RagCluster → Note `{exp: ...}`.

## Known issues to fix before applying upstream

The patch's `query_runner.py` hunk has **two distinct problems**, both unrelated to the `experiment_prepare` action but present in the same working-tree diff:

1. **Patch path/name mismatch.** The patch points `_ASSETS_DIR` at a file named:

   ```text
   sample_640×426.jpeg
   ```

   (underscore separator, Unicode multiplication sign `×`). This does **not** match this repo's actual extracted asset, [`assets/images/sample-640x426.jpeg`](../assets/images/sample-640x426.jpeg) (hyphen separator, ASCII `x`). Applying the patch verbatim to `rag-canvus` will look up a filename that does not exist there unless a file with that exact Unicode name is also placed in `canvus-serving`'s own assets directory. Rename the patch's filename references to match the canonical `sample-640x426.jpeg` before applying, or place a same-named copy where `canvus-serving` expects it.

2. **MIME mismatch.** The upload keeps:

   ```python
   content_type="image/png"
   ```

   for a `.jpeg` file. Before committing that part upstream, either change the MIME to `image/jpeg` or revert to the original PNG asset.

Fix both before applying the patch to `rag-canvus`.

## Test status

`integrations/canvus-serving-experiment-prepare/apps/canvus-serving/tests/test_experiment_prepare.py` is preserved here as extracted source, alongside `integrations/canvus-serving-experiment-prepare/apps/canvus-serving/app/actions/experiment_prepare.py`. **It cannot run standalone in this repo** — it imports from `app.db.migrations`, `app.jobs.queue`, `app.scanner.engine`, and `app.scanner.parser`, which are part of the full `canvus-serving` app tree, not part of `lap-in-the-loop`. Validate it only after applying the patch to the parent `rag-canvus`/`canvus-serving` tree (see "Applying the patch" below) and running it from there. No test run has been performed against this extracted copy.

## Applying the patch

From the original `rag-canvus` repo:

```bash
cd ~/dev/rag-canvus
git apply ~/dev/lap-in-the-loop/integrations/canvus-serving-experiment-prepare/canvus-serving-experiment-prepare.patch
```

Then run:

```bash
cd apps/canvus-serving
uv sync
uv run pytest -q tests/test_experiment_prepare.py
uv run ruff check app tests
```

Review the diff before staging. Do not commit `.env` or generated assets unless explicitly intended.

## Relationship to the full loop

| Capability | Serving `{exp:}` integration | `lab-agent` workflow |
|---|---:|---:|
| Generate initial experiment text | Yes | Yes |
| Ground through RagCluster connectors | Partial | Yes |
| Mock robot result | No | Yes |
| Detect result → setup loop | No | Yes |
| Continue/stop decision | No | Yes |
| Model-agnostic provider abstraction | No, OpenAI only | Yes, OpenAI/Claude |
| Canvas writes via MCP tools | No, direct serving client | Yes |

Use the serving integration for quick experiment-prep demos. Use `lab-agent` for the actual Lab-in-the-Loop autonomous workflow.
