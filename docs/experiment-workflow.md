# Experiment Workflow

## Overview

The Lab-in-the-Loop workflow is driven entirely by Canvus widgets and connectors. A connector is both a visual edge and an execution signal. The agent watches the canvas and reacts only when the required edge pattern exists.

```text
docs ─► RAGCluster_ ─► {idea: ...} ─► [EXP:Setup v001] ─► Robot_ ─► [EXP:Result v001]
                                          ▲                              │
                                          └──── user connects result ────┘
```

The result-to-setup back-edge means: “analyse this result against this setup and decide whether to run another round.”

## Node conventions

| Marker | Widget type | Created by | Meaning |
|---|---|---|---|
| `RAGCluster_` | Image | User/system | Knowledge scope, usually fed by docs/PDFs/notes |
| `{idea: ...}` | Note | User | Experiment idea/request grounded in a RagCluster |
| `[EXP:Setup vNNN]` | Note | Agent | Structured experiment design for round N |
| `Robot_` | Any widget/title marker | User/system | Mock execution target |
| `[EXP:Result vNNN]` | Note | Agent | Clearly mock result for round N |
| `[EXP:Closed]` | Note | Agent | Stop decision, reason, confidence, next action |

Marker matching is an exact title-**prefix** check (`str.startswith`), confirmed in `canvus_mcp/experiments.py` (`_is_robot`, `_is_setup`, `_is_result`) — e.g. a widget titled `Robot_Arm_1` matches `Robot_`, but a typo like `Robott_` or a differently-cased marker does not.

## Connectors and triggers

### 1. Knowledge to idea

```text
RAGCluster_ ─► Note `{idea: ...}`
```

Trigger surfaced as `ideas_needing_setup` by `scan_experiment_workflow` when:

- source is a RagCluster marker widget;
- destination is an idea note;
- there is no setup already connected to that idea.

Agent action:

1. Read idea note.
2. Check RagCluster feeders and outputs.
3. Let the model use read-only tools for grounding.
4. Create `[EXP:Setup v001]`.
5. Connect idea → setup.

### 2. Setup to robot

```text
[EXP:Setup vNNN] ─► Robot_
```

Trigger surfaced as `setups_needing_run` when:

- setup is connected to a robot marker;
- setup has no corresponding result yet.

Agent action:

1. Read setup note.
2. Ask model for a mock result consistent with the setup.
3. Create `[EXP:Result vNNN]` with first lines `Setup: <setup_id>` and `Round: <round_number>` (`lab_agent/orchestrator.py:run_on_robot`).
4. Connect robot → result.

### 3. Result to setup loop

```text
[EXP:Result vNNN] ─► [EXP:Setup vNNN]
```

Trigger surfaced as `loops` by `scan_experiment_workflow` / `detect_experiment_loops` when:

- result note connects back to a setup note;
- detector resolves the loop connector id;
- detector can infer setup/result round and related idea/RagCluster/robot where possible.

Agent action:

1. Read current setup and result.
2. Ask model for `LoopDecision`.
3. If STOP: create `[EXP:Closed]`, connect result → closed.
4. If CONTINUE: create next setup, connect result → next setup, run mock robot, create next result.
5. Repeat until model stops or the safety backstop is reached.

## Required note body markers

### Setup note

First lines:

```text
Idea: <idea_widget_id>
Round: <round_number>
```

Then rendered experiment setup sections:

- hypothesis
- rationale
- inputs/materials
- conditions
- method/protocol steps
- parameters
- expected readouts
- risks/uncertainties
- success criteria

### Result note

First lines:

```text
Setup: <setup_widget_id>
Round: <round_number>
```

Then rendered result sections:

- mock summary
- observations
- metrics
- quality flags
- interpretation
- caveats

### Closed note

Contains rendered decision:

- decision: stop
- reason
- confidence
- what was learned
- recommended human next step

## Grounding rules

- Ground setups in retrieved internal knowledge: RagCluster feeders, notes, PDFs, widget context.
- Do not invent domain-specific meanings for ambiguous acronyms.
- If a term is unclear, state uncertainty and lower confidence.
- Internet/general knowledge is not a substitute for internal context.
- Mock robot results must be labelled as mock and remain consistent with the setup.

## Idempotency

`canvus-mcp` and `lab-agent` cooperate to avoid repeated work:

- `scan_experiment_workflow` returns only pending forward triggers.
- Existing setup/result connectors prevent reprocessing.
- `lab-agent` keeps a session-local `processed_loops` set keyed by `loop_connector_id`.
- Node creation happens after analysis, not speculatively.

Known gap: processed-loop memory is not persisted across process restarts. A future version should store processed connector ids or round provenance durably.

## Poll cadence

`lab-agent watch` loops forever:

```text
scan → process ideas → process setups → process loops → sleep → repeat
```

Default interval:

```text
LAB_AGENT_WATCH_POLL_SECONDS=30
```

Use `lab-agent once` for smoke tests or scripted operation.

## Failure handling

| Failure | Expected behavior |
|---|---|
| MCP server unavailable | CLI fails/connect cycle logs warning |
| Canvus transient API error | watcher logs warning and continues next cycle |
| Model cannot emit schema | current run fails; next cycle can retry pending canvas state |
| Ambiguous domain input | setup/result includes caveat rather than invented fact |
| Loop keeps continuing | `LAB_AGENT_LOOP_MAX_ROUNDS` closes via backstop reason |

## Example happy path

1. User adds a `RAGCluster_` image widget.
2. User connects documents/PDFs/notes into the RagCluster.
3. User adds note: `{idea: Design next lung fibrosis micro-CT experiment}`.
4. User connects `RAGCluster_ → idea note`.
5. `lab-agent watch` creates `[EXP:Setup v001]`.
6. User connects setup to a `Robot_` widget.
7. `lab-agent watch` creates `[EXP:Result v001]`.
8. User connects result back to setup.
9. Agent analyses and either creates `[EXP:Closed]` or next setup/result round.

## Tool contract

Two different layers call `canvus-mcp` tools, and they are not the same tool set:

**Orchestrator/watcher-level calls** (`lab_agent/watch.py`, `lab_agent/nodes.py`) — called directly, never exposed to the model's tool-use loop:

- `scan_experiment_workflow` — polled once per watch cycle to find pending triggers/loops.
- `detect_experiment_loops` — used indirectly through `scan_experiment_workflow`'s `loops` field.
- `get_note` — used by `nodes.read_note_text`.

**Model-facing read/grounding tools** — the actual allowlist the model's tool-use loop can call is `lab_agent/tool_bridge.py:READ_TOOLS`:

- `scan_server`
- `list_canvases`
- `check_ragcluster_connections`
- `check_widget_connections`
- `get_note`
- `get_widget`
- `download_pdf`

`download_image` and `download_asset` exist as `canvus-mcp` tools (used by the Claude Code skill / direct MCP clients) but are **not** in `lab-agent`'s model-facing allowlist today; only `download_pdf` is. Any tool call outside this allowlist is rejected by `tool_bridge.execute_tool_calls` with an explicit error.

**Orchestrator-only write tools** (`lab_agent/nodes.py`):

- `create_note`
- `create_connector`

`canvus-mcp` also exposes `create_browser` and `create_image` write tools, but `lab-agent`'s orchestrator does not call either today — `nodes.py` only ever calls `create_note` and `create_connector`. They remain available on the MCP server for direct/manual use (e.g. via the Claude Code skill) and are not part of the automated write path described in this document.

The model-facing tool loop receives only the read-tool allowlist above; it never receives write tools. The orchestrator is the only caller of write tools.
