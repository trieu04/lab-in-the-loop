# Experiment Workflow

## Overview

The Lab-in-the-Loop workflow is driven entirely by Canvus widgets and connectors. A connector is both a visual edge and an execution signal. The agent watches the canvas and reacts only when the required edge pattern exists.

```text
docs ─► RAGCluster_ ─► {idea: ...} | {idea+auto: ...} ─► [EXP:Setup v001] ─► [EXP:Validation]
                                                                                  │
                                                  scientist approval ─► lab-lead approval
                                                                                  │
     opt-in Phase 8 7A dry run only ─ manual first-round activation if manual ─► Execution → Analysis → Knowledge
                                                                                  │                (MOCK / NOT MEASURED)
                                                 legacy synthetic mock compatibility ─► Robot_ ─► [EXP:Result v001]
                                                                                                           │
                                                  ◄────────────── user connects result ──────────────────┘
```

The result-to-setup back-edge means: “analyse this result against this setup and decide whether to run another round.”

## Node conventions

| Marker | Widget type | Created by | Meaning |
|---|---|---|---|
| `RAGCluster_` | Image | User/system | Knowledge scope, usually fed by docs/PDFs/notes |
| `{idea: ...}` | Note | User | Experiment idea/request grounded in a RagCluster; legacy/manual execution mode |
| `{idea+auto: ...}` | Note | User | Experiment idea/request grounded in a RagCluster; automatic execution mode |
| `[EXP:Setup vNNN]` | Browser (generated) or legacy Note | Agent | Structured experiment design; one widget per idea, each round appended as version `vNNN` |
| `Robot_` | Any widget/title marker | User/system | Mock execution target |
| `[EXP:Result vNNN]` | Browser (generated) or legacy Note | Agent | Clearly mock result; one widget per setup, each round appended as version `vNNN` |
| `[EXP:Closed]` | Browser (generated) or legacy Note | Agent | Stop decision, reason, confidence, next action |
| `[EXP:Needs Input]` | Browser (generated prompt/status) | Agent | Infrastructure marker for grounded-input or execution-mode requests; it is not approval evidence |
| `[EXP:Validation]` | Browser (generated) | Agent | Projection of one typed, durable in-silico validation result |
| `[EXP:Validation] Approval Status` | Browser (generated) | Agent | Projection of durable approval evidence and its current gate state; never an approval input |
| `[EXP:Execution]` / `[EXP:Analysis]` / `[EXP:Knowledge]` / `[EXP:Conflict]` | Browser (generated) | Agent | Phase 8 7A projection of append-only dry-run lifecycle records; display-only and visibly **DRY RUN / MOCK — NOT MEASURED** |

Marker matching is an exact title-**prefix** check (`str.startswith`), confirmed in `canvus_mcp/experiment_widgets.py` (`_is_robot`, `_is_setup`, `_is_result`) — e.g. a widget titled `Robot_Arm_1` matches `Robot_`, but a typo like `Robott_` or a differently-cased marker does not. Setup/Result markers classify both generated Browser widgets and legacy Notes; idea markers are Note-only.

`{idea: ...}` is the backward-compatible manual mode. `{idea+auto: ...}` is the only automatic-mode spelling. Any other `{idea+<mode>: ...}` variant on a RagCluster-connected idea is surfaced as an unsupported-mode error; the watcher writes one deduplicated `[EXP:Needs Input]` request and does not create a setup or dispatch execution. Loose/disconnected Notes are ignored, including unsupported-looking mode text. Do not use a Canvas title, connector, or free-form text to override a mode.

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

### 2. Setup to validation and ordered approvals

```text
[EXP:Setup vNNN] ─► [EXP:Validation] ─► [EXP:Validation] Approval Status
```

The watcher validates each canonical Setup Browser payload with the local deterministic adapter. Its result is visibly labelled **“DETERMINISTIC DRY RUN — NOT SCIENTIFIC VALIDATION”**: it checks proposal structure only and does not call a scientific simulator or external provider. The typed real-adapter boundary remains disabled/unimplemented and fails closed.

Validation and approvals are durable, canvas-scoped SQLite evidence. A proposal and result are bound by typed canonical SHA-256 hashes using `litl-canonical-json-v1`. The only authorization order is: validation `proceed` → credential-verified scientist decision → credential-verified lab-lead decision. Replays are idempotent despite volatile identity-verification timestamps; proposal edits or a different validation hash retain prior history but make that history stale and non-authorizing.

Canvas Notes, titles, connectors, and author text are topology/projection data only. They cannot authenticate, create, or advance an approval. Credentials are supplied to an `IdentityProvider` for verification and are never persisted. The production identity provider is disabled/unimplemented; the static development provider is explicitly non-production.

### 3. Approved setup to Phase 8 7A dry-run lifecycle or legacy mock result

```text
[EXP:Setup vNNN] ─► Robot_ (legacy marker) ─► Phase 8 execution → analysis → knowledge projections
```

A setup-to-robot connector alone never runs execution. `wet_lab_execution_enabled` and `phase8_execution_enabled` both default to `false`. When an operator explicitly enables Phase 8, the factory accepts `dry_run` only and requires the exact current canonical proposal hash, validation-result hash, validation `proceed`, credential-verified scientist approval, and credential-verified lab-lead approval before lifecycle work starts.

Mode then determines first-round activation: `{idea+auto: ...}` may dispatch once those gates are current; `{idea: ...}` requires an additional credential-verified, durable manual activation for round 1. That activation is bound to the canvas, setup, current proposal hash, and validation-result hash, is idempotent across restarts, and is not a Canvas-widget action. The repository exposes the approval/activation service boundary but no approval or activation CLI; wire a reviewed identity-bearing operator service before relying on this path. An edited proposal or changed validation result makes prior approvals and activation non-authorizing.

Phase 8 milestone 7A creates deterministic memory-only execution, analysis, knowledge, and possible conflict records. It visibly labels every projection **DRY RUN / MOCK — NOT MEASURED** and uses opaque mock artifact references. It performs no provider API or network call, robot/wet-lab action, Flywheel/HPC job, real knowledge-store write, credential handling, raw provider-text retention, capability-URL projection, or measured-evidence production. `sandbox` and `real` modes fail closed. Before retry, it authoritatively reconciles submitted work by idempotency key; atomic durable intent claiming prevents duplicate concurrent submits, and terminal recovery settles the intent.

The separate legacy branch can still create a model-generated `[EXP:Result vNNN]` **MOCK_RESULT** artifact when `wet_lab_execution_enabled` is explicitly enabled. That preserved synthetic compatibility path is not Phase 8 real execution and does not establish measured scientific truth. Result-to-setup loop decisions are dispatched separately by the watcher through the governed single-decision loop boundary.

### 4. Result to setup loop

```text
[EXP:Result vNNN] ─► [EXP:Setup vNNN]
```

Trigger surfaced as `loops` by `scan_experiment_workflow` / `detect_experiment_loops` when:

- result widget connects back to a setup widget;
- **the setup's round is not greater than the result's round** (`round(setup) <= round(result)`, per the `vNNN` suffix in each note's title) — this excludes legacy forward round-advance edges;
- detector resolves the loop connector id;
- detector can infer setup/result round and related idea/RagCluster/robot where possible.

The round check preserves compatibility with legacy canvases that may already contain an orchestrator-created `result_N -> setup_{N+1}` advance edge. That edge is graph-isomorphic to a genuine user-drawn `result_N -> setup_N` loop trigger, so only the same-round or backward case (`round(setup) <= round(result)`) is actionable. The current safe single-decision boundary does not create a new forward edge in the same call, but the detector still filters existing legacy forward edges. See `canvus_mcp/experiments.py:detect_experiment_loops`.

Agent action:

1. Read current setup and result.
2. Ask model for `LoopDecision`.
3. If STOP **and** at least `loop_min_rounds` experiments exist: create `[EXP:Closed]`, connect result → closed, and emit the terminal audit/outbox event. An early STOP before the minimum is overridden so the loop biases toward more than one experiment; a governance or max-rounds backstop is never overridden and stops immediately.
4. If CONTINUE (or an early STOP was overridden), the preserved legacy synthetic mock-loop branch may ground and write a successor Setup/Result in the same call. It remains a compatibility path only and produces no measured scientific truth.
5. The Phase 8 7A lifecycle is separate: it rechecks current proposal/validation/approval hashes and manual activation before dry-run work, then records only mock/dry-run lineage. Do not treat either branch as real execution, real Flywheel/HPC analysis, a real knowledge-store update, or a substitute for the external child-plan gates.

### Terminal closures and notifications

A notification is eligible only after a genuine terminal closure has a durable `[EXP:Closed]` artifact id and a matching durable `loop_stopped` event. Eligible reasons are the model decision and the distinct maximum-rounds, token-budget, cost-budget, wall-time, no-progress, locality-denial, or reservation-denial closures. Setup validation, pending approval, invalid execution mode, disabled execution, a failed model call, and a non-terminal loop iteration are not notification events.

When SMTP is enabled, the closure enqueues one metadata-only durable outbox row keyed by canvas, trigger, closure, round, and reason. Delivery runs after workflow processing and after the canvas lease is released; it does not reopen or mutate a closed workflow. The logical key and deterministic SMTP `Message-ID` suppress duplicate logical sends across replay. Delivery is best-effort and logically deduplicated: only known pre-submit/transient failures retry automatically. A partial-recipient refusal or uncertain post-lease/post-submit result is held for reconciliation and never automatically resent; it becomes quarantined when its reconciliation window expires. Neither exactly-once nor at-least-once inbox delivery is guaranteed.

## Generated artifact payload markers

Generated Setup/Result/Closed, Needs Input, Validation, Approval Status, and Phase 8 Execution/Analysis/Knowledge/Conflict artifacts are Browser widgets. Their model-readable text lives in the canonical `ArtifactStore` payload; legacy Notes still expose Setup/Result/Closed text directly during migration. Validation and Approval Status widgets are projections of durable evidence, not an approval interface. Phase 8 projections expose safe ids, hashes, roles, status, and the **DRY RUN / MOCK — NOT MEASURED** label; they deliberately omit artifact `logical_uri`, capability URLs, raw provider bodies, credentials, and measured-receipt details. Human-authored Notes, titles, connectors, author text, and Canvus scan buckets remain non-authorizing display/topology data only.

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

`write_needs_input_node` can create a generated `[EXP:Needs Input]` Browser artifact with a message, reason, reason hash, optional context, round, and `NEEDS_REVIEW` artifact state. Grounding uses it for explicit insufficient-evidence or unresolved-ambiguity outcomes, keyed by `(canvas, predecessor_id, reason_hash)` so repeated polls for the same reason converge on one Browser artifact and connector. New generated Browser artifacts are positioned near their source/predecessor widget when Canvus geometry is available: setups near ideas, results near setups, closed nodes to the right of their predecessor (normally a result), and Needs Input prompts below their predecessor; missing, malformed, or unusable geometry falls back to the deterministic legacy grid. This is infrastructure for a request/status marker only. The Needs Input artifact itself cannot authorize approval transitions, provide approve/reject controls, or enable wet-lab execution; those gates require credential-bearing service APIs and current durable evidence, while the human-authored response remains a separate Note.

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
- `detect_experiment_loops` filters out legacy forward round-advance edges (`round(setup) > round(result)`, see "Result to setup loop" above) so an existing compatibility edge is never mistaken for a new actionable loop.
- `lab-agent` persists every derived trigger (`idea_setup:<id>`, `setup_run:<id>`, `loop:<connector_id>`) in the SQLite `workflow_attempts` ledger; completed attempts block reprocessing across restarts.
- Node/connector creation uses side-effect intents plus live canvas probes before mutation, so a crash after a write lands can reconcile the existing effect instead of duplicating it.
- Phase 8 execution/analysis submission uses append-only run rows plus the existing `side_effect_intents` identity. A `BEGIN IMMEDIATE` prepare/claim prevents concurrent duplicate submit, and an active/submitted/ambiguous row is authoritatively reconciled by idempotency key before any retry. `workflow_attempts` continues to schedule watcher work and retry/backoff; it is not replaced by Canvus display buckets.
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
| Validation adapter fails, times out, or returns an invalid typed result | fail closed; append only safe failure metadata and do not advance the gate |
| Approval comes from a Canvas Note/title/connector/author field | ignored for authorization; only credential-bearing `IdentityProvider` verification can create durable approval evidence |
| Approval is replayed after verification timestamp changes | idempotent when the stable approval content matches; volatile verification/decision timestamps do not authorize a changed proposal or validation result |
| Proposal is edited or validation changes | prior validation/approval history remains append-only but is stale and cannot authorize the edited proposal |
| Unsupported `{idea+<mode>: ...}` marker | write one deduplicated Needs Input request; do not generate a setup or dispatch execution |
| Wet-lab execution is disabled (default) | watcher does not dispatch the legacy `setups_needing_run` mock-result path |
| Phase 8 execution is disabled (default) | deterministic dry-run lifecycle does not construct or run; no execution/analysis/knowledge projection is produced |
| Phase 8 uses `sandbox` or `real` mode | factory fails closed; only `dry_run` is installed for 7A |
| Phase 8 submit is active, submitted, or ambiguous | reconcile authoritatively by idempotency key before any retry; unsupported reconciliation blocks safely rather than duplicating submission |
| Manual mode, first round not activated | do not dispatch; emit the manual-activation Needs Input request only after current durable approval gates pass |
| Model stops after a single experiment | override the early stop and run at least `loop_min_rounds` experiments (default 2) before honoring a model stop; governance/backstop reasons are never overridden |
| Loop keeps continuing | close with the first distinct terminal reason: maximum rounds, token budget, cost budget, wall time, no-progress, locality denial, or reservation denial; model stop remains its own reason |
| SMTP transient delivery failure | keep the closed workflow unchanged; retry from the durable outbox with backoff until quarantined at the notification attempt limit |
| SMTP rejection or invalid durable notification metadata | quarantine the logical notification; do not send it |
| SMTP outcome is ambiguous after submit | hold outside the normal send queue through its reconciliation window, then quarantine; never blindly resend |

## Example happy path

1. User adds a `RAGCluster_` image widget.
2. User connects documents/PDFs/notes into the RagCluster.
3. User adds either `{idea: Design next lung fibrosis micro-CT experiment}` (manual) or `{idea+auto: Design next lung fibrosis micro-CT experiment}` (automatic).
4. User connects `RAGCluster_ → idea note`.
5. `lab-agent watch` creates `[EXP:Setup v001]`, performs the deterministic dry-run validation, and renders Validation/Approval Status Browser projections.
6. A credential-bearing approval service records current scientist approval followed by current lab-lead approval; Canvas text and connectors do not approve.
7. With execution explicitly enabled, auto mode may dispatch the mock result path. Manual mode requires a separate credential-verified first-round activation.
8. The watcher creates `[EXP:Result v001]` as a Browser artifact, and the user connects result back to setup.
9. The agent analyses and either creates `[EXP:Closed]` or the next setup/result round. A qualifying terminal closure is durably queued for SMTP only when SMTP is configured.

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
- `update_browser` — repairs marker/title/name and rotates token-bearing capability URLs in place without recreating the widget, breaking connectors, or undoing manual placement.
- `create_connector`

`canvus-mcp` also exposes `create_image`; it remains available for direct/manual use and is not part of the automated generated-artifact write path.

The model-facing tool loop receives only the read-tool allowlist above; it never receives write tools. Successful read results are returned to the model inside an `untrusted_data` envelope with `tool`, `source_id`, and `content`; blocked/error results are not captured as evidence. The orchestrator is the only caller of write tools.
