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
| `[EXP:Setup vNNN]` | Browser (generated) or legacy Note | Agent | Structured experiment design for round N |
| `Robot_` | Any widget/title marker | User/system | Mock execution target |
| `[EXP:Result vNNN]` | Browser (generated) or legacy Note | Agent | Clearly mock result for round N |
| `[EXP:Closed]` | Browser (generated) or legacy Note | Agent | Stop decision, reason, confidence, next action |
| `[EXP:Needs Input]` | Browser (generated prompt/status) | Agent | Infrastructure marker for requesting human input; the human response is a Note |

Marker matching is an exact title-**prefix** check (`str.startswith`), confirmed in `canvus_mcp/experiment_widgets.py` (`_is_robot`, `_is_setup`, `_is_result`) — e.g. a widget titled `Robot_Arm_1` matches `Robot_`, but a typo like `Robott_` or a differently-cased marker does not. Setup/Result markers classify both generated Browser widgets and legacy Notes; `{idea: ...}` is Note-only.

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
3. Let the model use only read tools for grounding; successful reads are captured in a per-run `EvidenceLedger` with deterministic source ids.
4. Validate the emitted setup before any write: evidence must be `sufficient`, citations must resolve to the current ledger, and the approved acronym dictionary must clear the original idea text, emitted setup fields, and all bounded evidence excerpts.
5. If executable, create `[EXP:Setup v001]` as a generated Browser artifact backed by `ArtifactStore` and connect idea → setup.
6. If evidence is insufficient or ambiguity remains, create one deduplicated `[EXP:Needs Input]` Browser artifact instead; invalid citations write nothing and stay retryable.

### 2. Setup to robot

```text
[EXP:Setup vNNN] ─► Robot_
```

Trigger surfaced as `setups_needing_run` when:

- setup is connected to a robot marker;
- setup has no corresponding result yet.

Agent action:

1. Read setup text from the canonical artifact payload, falling back to legacy Note text during migration.
2. Ask model for a mock result consistent with the setup.
3. Create `[EXP:Result vNNN]` as a generated Browser artifact whose canonical payload includes `Setup: <setup_id>` and `Round: <round_number>` text for later model reads.
4. Connect robot → result Browser widget.

### 3. Result to setup loop

```text
[EXP:Result vNNN] ─► [EXP:Setup vNNN]
```

Trigger surfaced as `loops` by `scan_experiment_workflow` / `detect_experiment_loops` when:

- result widget connects back to a setup widget;
- **the setup's round is not greater than the result's round** (`round(setup) <= round(result)`, per the `vNNN` suffix in each note's title) — this excludes the orchestrator's own round-advance edge;
- detector resolves the loop connector id;
- detector can infer setup/result round and related idea/RagCluster/robot where possible.

The round check matters because a `result_N -> setup_{N+1}` connector (drawn by the orchestrator itself when it advances the loop to the next round, step 4 below) is graph-isomorphic to a real user-drawn `result_N -> setup_N` loop trigger. Both generated Browser widgets and legacy Notes use the same `[EXP:Result]` / `[EXP:Setup]` title markers. Only the same-round or backward case (`round(setup) <= round(result)`) is a genuine "iterate this experiment" signal; a strictly forward edge (`round(setup) > round(result)`) is the loop's own advance and must not be re-detected as a new actionable loop on the next scan. See `canvus_mcp/experiments.py:detect_experiment_loops`.

Agent action:

1. Read current setup and result.
2. Ask model for `LoopDecision`.
3. If STOP: create `[EXP:Closed]`, connect result → closed.
4. If CONTINUE: create next setup, connect result → next setup, run mock robot, create next result.
5. Repeat until model stops or the safety backstop is reached.

## Generated artifact payload markers

Generated Setup/Result/Closed and generated Needs Input prompt/status artifacts are Browser widgets. Their model-readable text lives in the canonical `ArtifactStore` payload; legacy Notes still expose Setup/Result/Closed text directly during migration. Human-authored responses to approval/review/input requests remain Notes.

### Setup artifact

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

### Result artifact

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

### Closed artifact

Contains rendered decision:

- decision: stop
- reason
- confidence
- what was learned
- recommended human next step

Closed artifacts are classified in `scan_experiment_workflow`'s `closeds` bucket by title prefix (`[EXP:Closed]`). That bucket is used for connector-independent crash recovery of terminal generated widgets, including legacy Notes.

### Needs Input artifact

`write_needs_input_node` can create a generated `[EXP:Needs Input]` Browser artifact with a message, reason, reason hash, optional context, round, and `NEEDS_REVIEW` artifact state. Grounding uses it for explicit insufficient-evidence or unresolved-ambiguity outcomes, keyed by `(canvas, predecessor_id, reason_hash)` so repeated polls for the same reason converge on one Browser artifact and connector. This is infrastructure for a request/status marker only. The implementation does not yet provide future approval workflow transitions, approve/reject buttons, or wet-lab gate enforcement; the human-authored response remains a separate Note.

## Implementation-plan Phase 6 ingestion as grounding (roadmap Phase 4c)

Local-source ingestion is a supporting pre-grounding path, not a canvas workflow transition. A trusted service can enqueue an authorized Canvus PDF/image/asset for a canvas; a separate local worker processes deterministic leased units and exposes progress/chunks through MCP. `lab-agent` never enqueues, retries, or cancels ingestion.

```text
trusted service enqueue → protected local raw cache → leased extraction units
  → get_ingestion_status (operational only) / read_ingestion_chunks (bounded evidence)
  → EvidenceLedger → Phase 5 locality authorization → later provider call
```

`get_ingestion_status` carries no citeable source text and is not evidence. `read_ingestion_chunks` returns only completed, bounded chunks in deterministic pages; `lab-agent` wraps their text as `untrusted_data` with the chunk-read response's allowlisted scalar provenance (`canvas_id`, job id) and canvas classification. A chunk becomes citeable only after the evidence ledger successfully retains it. Enqueue reads carry exact requested-source provenance; ambiguous job-only reads return a bounded source set with `unknown` classification. Raw bytes, cache paths, and capability URLs are not model-visible. Unknown classification fails closed before a later provider call.

The worker/cache/ledger are single-host local state. A stale lease generation cannot commit; chunks and completion are atomic; expired work is reclaimable after restart without bypassing the poison-attempt ceiling; retry/backoff, poison, cancellation, and graceful stop are durable behavior. Cache publication is verified and atomic, and generic-source MIME is normalized before extraction routing. Supported extraction is strict UTF-8 text, CSV/TSV, JSON records, PNG/JPEG/GIF metadata, and PDF pages; PDF page text runs in an isolated resource-limited child. Malformed/encrypted/oversized/unsupported outcomes are typed; video and non-CSV/TSV spreadsheet formats remain unsupported/external gates.

## Grounding rules

- Ground setups in retrieved internal knowledge: RagCluster feeders, notes, PDFs, widget context, and authorized completed ingestion chunks. Future wiki/KG/vector sources are adapters or external gates, not current hard dependencies.
- Successful model-facing read-tool results are wrapped as `untrusted_data` and recorded in a bounded, per-run `EvidenceLedger`; the model never receives write tools.
- Setups may be written only after citation ids resolve against the current ledger; fabricated, missing, or mixed-invalid citations write nothing and remain retryable.
- Do not invent domain-specific meanings for ambiguous acronyms. The deterministic scan checks the original idea, emitted setup fields, and every bounded retrieved evidence excerpt against the approved dictionary.
- If evidence is insufficient or ambiguity remains, create an explicit Needs Input artifact; do not silently lower confidence and proceed.
- Internet/general knowledge is not a substitute for internal context.
- Mock robot results must be labelled as mock and remain consistent with the setup.

## Governance at model-call boundaries

`scan_experiment_workflow` copies the canvas classification into every actionable `ideas_needing_setup`, `setups_needing_run`, and `loops` entry. `lab-agent` copies that metadata into the trigger's governance context before its first governed model call.

Before the agent sends a setup, mock-result, or loop-decision request, the provider-neutral gateway selects a configured provider by task stage, but only after all source/evidence classifications are authorized for that provider's approved HTTPS endpoint. `unknown`/restricted or unapproved content, missing endpoint/classification authorization, unknown model pricing, or a reservation that would exceed a configured envelope closes the trigger/loop without provider dispatch. A later routing preference is available only before dispatch; an uncertain submitted request is never sent to a second provider.

The gateway persists a request-digest model-call intent plus an idempotent SQLite budget hold before dispatch. Run accounting resets for each trigger; canvas totals and active reservations are rebuilt after restart. Exact provider counts are normalized when supplied (Claude input includes cache-create/read tokens). Otherwise the agent records a conservative estimate over messages, tools, response schema, schema name, and the configured output cap. Estimates govern limits but are not invoices. Known typed pre-submission transient failures may retry when due; deterministic, ambiguous, and untyped outcomes never receive a blind redispatch. Durable failure/audit data contains only safe categories, digests, counts, and approved metadata — not raw prompts, responses, secrets, or provider error text.

## Idempotency

`canvus-mcp` and `lab-agent` cooperate to avoid repeated work:

- `scan_experiment_workflow` returns only pending forward triggers.
- Existing setup/result connectors prevent reprocessing.
- `detect_experiment_loops` filters out forward round-advance edges (`round(setup) > round(result)`, see "Result to setup loop" above) so the orchestrator's own advance connector is never mistaken for a new actionable loop.
- `lab-agent` persists every derived trigger (`idea_setup:<id>`, `setup_run:<id>`, `loop:<connector_id>`) in the SQLite `workflow_attempts` ledger; completed attempts block reprocessing across restarts.
- Node/connector creation uses side-effect intents plus live canvas probes before mutation, so a crash after a write lands can reconcile the existing effect instead of duplicating it.
- Generated Browser widgets carry short idempotency tags in their titles (and the live Browser label round-trip also preserves the same marker under `name` when needed); legacy Notes with the same markers remain discoverable. `scan_experiment_workflow` exposes closed artifacts in the `closeds` bucket so terminal `[EXP:Closed]` recovery does not depend on a connector already existing. Needs-input Browser artifacts use the same durable Browser infrastructure and `needs_inputs` recovery bucket.
- Node creation happens after analysis, not speculatively.

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
| Model cannot emit schema | current run writes nothing; the attempt is durably failed/backed off and can retry while canvas state remains pending |
| MCP write tool returns error payload or no id | write helper raises `MCPToolError`; the side-effect intent is marked failed and the workflow attempt remains retryable/quarantine-eligible |
| Evidence not asserted sufficient | write one deduplicated `[EXP:Needs Input]` Browser artifact; executable setup remains pending |
| Ambiguous domain term/acronym | deterministic dictionary-backed scan over idea + setup + evidence excerpts writes Needs Input; no guessed expansion is accepted |
| Invalid/fabricated citation | setup writes nothing; attempt remains retryable/backoff/quarantine-eligible |
| Locality authorization denied | no provider receives the call; render/audit a `locality_denial` closure and make no follow-on provider/canvas write |
| Missing/unknown model pricing or a pre-dispatch budget hold denied | no provider receives the call; render/audit a `reservation_denial` closure and make no follow-on provider/canvas write |
| Known pre-submission provider transient | release the durable hold and retry only at `next_retry_at`, up to `LAB_AGENT_MODEL_CALL_MAX_ATTEMPTS` |
| Submitted, executed, ambiguous, or untyped provider outcome | capability-aware reconciliation only; unsupported reconciliation blocks safely with no blind redispatch |
| Ingestion source malformed/encrypted/oversized/unsupported | worker persists a typed terminal unit failure; chunks are not fabricated; operator retry/cancel remains canvas-scoped |
| Ingestion worker interrupted | expired lease is reclaimed by a later worker; current-generation atomic completion prevents duplicate/stale chunks |
| Ingestion read/auth/result bound denied | fixed sanitized denial/error; no mutation, raw-path disclosure, evidence insertion, or provider call follows |
| Loop keeps continuing | close with the first distinct terminal reason: maximum rounds, token budget, cost budget, wall time, or no-progress; model stop remains its own reason |

## Example happy path

1. User adds a `RAGCluster_` image widget.
2. User connects documents/PDFs/notes into the RagCluster.
3. User adds note: `{idea: Design next lung fibrosis micro-CT experiment}`.
4. User connects `RAGCluster_ → idea note`.
5. `lab-agent watch` creates `[EXP:Setup v001]` as a Browser artifact.
6. User connects setup to a `Robot_` widget.
7. `lab-agent watch` creates `[EXP:Result v001]` as a Browser artifact.
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
- `get_ingestion_status`
- `read_ingestion_chunks`

`download_image` and `download_asset` exist as `canvus-mcp` tools (used by the Claude Code skill / direct MCP clients) but are **not** in `lab-agent`'s model-facing allowlist today; only `download_pdf` is. `enqueue_ingestion`, `retry_ingestion`, and `cancel_ingestion` are also excluded: model allowlisting is not ingestion authentication. Any tool call outside this exact allowlist, including an arbitrary namespace suffix, is rejected by `tool_bridge.execute_tool_calls` with a fixed sanitized error.

**Orchestrator-only write tools** (`lab_agent/nodes.py`):

- `create_note` — still used for user/legacy Note paths; generated Setup/Result/Closed/Needs Input prompt artifacts are no longer created as Notes.
- `create_browser` — creates generated artifact Browser widgets.
- `update_browser` — repairs marker/title/name/location and rotates token-bearing capability URLs in place without recreating the widget or breaking connectors.
- `create_connector`

`canvus-mcp` also exposes `create_image`; it remains available for direct/manual use and is not part of the automated generated-artifact write path.

The model-facing tool loop receives only the read-tool allowlist above; it never receives write tools. Successful read results are returned to the model inside an `untrusted_data` envelope with `tool`, `source_id`, and `content`; blocked/error results are not captured as evidence. The orchestrator is the only caller of write tools.
