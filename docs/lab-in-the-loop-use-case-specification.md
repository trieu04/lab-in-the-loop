# Lab-in-the-Loop Use Case Specification

## Document control

| Field | Value |
|---|---|
| Status | Draft for review |
| Version | 1.3 |
| Date | 2026-07-23 |
| Source vision | [docs/notes/use-case-lab-in-the-loop.md](notes/use-case-lab-in-the-loop.md) (original meeting notes, excluded from normalization) |
| Scope | Canonical (normalized) use case specification for the entire Lab-in-the-Loop vision, with annotations of current implementation status in the `lap-in-the-loop` repository |
| Owner / Approver | Pending |
| Status notation | `[MVP]` = code path exists in this repository and confirmed from source; local verification is complete where stated, while live Canvus E2E demo verification remains Phase 2; `[MVP partial]` = a safe local/source subset exists but external/live capability remains unavailable; `[Future]` = not implemented; `[Proposed]` = proposed direction (harness-first architecture), not yet ratified by owner |

**Scope statement:** This document specifies the **entire target vision** of the Lab-in-the-Loop use case, inherited from the original meeting notes (`docs/notes/use-case-lab-in-the-loop.md`). Each requirement, flow, and data field is tagged with implementation status. The document does **not claim** that `[Future]`/`[Proposed]` capabilities are installed — only records them as part of the target specification to guide development. The harness-first architectural direction (independent, provider-neutral, decoupled from Claude Code skill) described in `docs/system-architecture.md` § "Target harness boundary (proposed)" and `docs/development-roadmap.md` remains a **proposal awaiting owner ratification**, not officially approved.

---

## 1. Overview and objectives

Lab-in-the-loop is a closed-loop workflow for drug discovery: AI uses internal knowledge to design experiments, experiments are validated (in-silico and/or human), executed (real lab or robotic), new data is analyzed automatically, results are interpreted and recorded into a versioned knowledge base, then the system proposes the next round of experiments.

```text
Internal Knowledge / Wiki
→ AI Experiment Design
→ Human / In Silico / Robotic Lab Execution
→ New Data
→ Automated Analysis
→ Knowledge Update
→ Next Experiment Design
```

The core distinction from a typical Q&A system: AI output does not stop at text — it is an **actionable experiment plan** ready to enter a real lab.

Canonical descriptor (bilingual, preserved from original vision):

> **Lab-in-the-loop is a closed-loop drug discovery workflow in which AI grounds experiment design in internal GSK knowledge, proposes actionable experiments, validates them through in silico simulation or human review, executes them through human/robotic lab and Flywheel analysis pipelines, and feeds the resulting data back into a versioned knowledge graph to generate the next round of experiment recommendations.**

---

## 2. Scope

### In-scope

- Entire 9-step loop: knowledge grounding → experiment design → validation → lab execution → data generation → automated analysis → interpretation → knowledge update → redesign.
- 3 stable use cases: `UC-LITL-02` (top-level), `UC-LITL-01` (supporting), `UC-LITL-03` (supporting/safety gate).
- Target approval/safety gate chain (5 gates, 9 decision states).
- Current data contract (`ExperimentSetup`, `ExperimentResult`, `LoopDecision`) and gaps versus target schema.
- Error/exception handling: insufficient evidence, ambiguous acronym, schema failure, MCP/provider unavailable, in-silico revise/reject, human rejection, resource/budget rejection, lab/Flywheel failure, conflicting result, duplicate/retry, loop backstop.
- NFR: security, data locality, model independence, traceability, reproducibility, reliability, scalability, observability, cost governance.

### Out-of-scope

- Detailed UI/UX specification of canvas widget (covered in `docs/experiment-workflow.md`).
- Operational/deployment configuration details (covered in `docs/setup-and-operations.md`).
- Official ADR/architecture decisions — this document does not create new ADRs.
- Integration with `canvus-serving` `{exp: ...}` (a separate action; see `docs/canvus-serving-integration.md`) — not part of the main Lab-in-the-Loop loop.
- Ratification of the harness-first architectural direction — that is an owner decision; this document describes it as `[Proposed]`.

---

## 3. Terminology and status notation

| Notation | Meaning |
|---|---|
| `[MVP]` | Code path exists in `apps/canvus-mcp` + `apps/lab-agent`, confirmed directly from source; local verification is complete where noted, while live Canvus E2E demo verification remains Phase 2 |
| `[MVP partial]` | Implementation exists but covers only part of the vision requirement |
| `[Future]` | In roadmap/vision but not yet implemented |
| `[Proposed]` | Proposed architectural direction (harness-first), not yet ratified |
| `TBD` | No measurable evidence yet to set a specific target |

Business terminology note: "Phase 3" in the original vision (§ source) is an external GSK program milestone — distinct from "Phase 3" in `docs/development-roadmap.md` of this repository ("Harness contracts and persistent loop state").

---

## 4. System boundary

```text
Canvus Server (durable canvas state)
  │ REST API
  ▼
apps/canvus-mcp  ── MCP tools: scan / read / write canvas ──►
  │ MCP streamable HTTP
  ▼
apps/lab-agent   ── watcher/orchestrator: read to ground, model propose, orchestrator write ──►
  │
  ├─ OpenAI adapter
  ├─ Claude adapter
  ├─ governed gateway (task routing, locality/pricing, normalized usage, stop policy)
  ├─ artifact service (capability-protected Browser HTML)
  └─ local SQLite WAL ledger (attempts, leases, model/canvas intents, budget reservations, audit/outbox, artifact records/tokens/widgets)

apps/canvus-mcp ingestion runtime
  ├─ separate SQLite/WAL ledger (assets/sources/jobs/units/leases/attempts/chunks)
  ├─ protected SHA-256 raw cache and bounded local extractors
  └─ authenticated canvas-scoped status/chunk MCP reads
```

Current system boundary `[MVP]` consists of the 2 runtime apps plus separate local SQLite/WAL ledgers: `lab-agent` persists attempts, leases, side-effect intents/outbox, audit, canonical generated-artifact records, and Phase 7 append-only canvas-scoped validation/approval evidence; `canvus-mcp` persists bounded local-source ingestion assets, sources, jobs, units, leases, attempts, chunks, and cancellation state. Phase 7 hashes typed proposals/results with `litl-canonical-json-v1`, runs only a visibly non-scientific deterministic dry run by default, and projects durable ordered approvals into Browser artifacts. The real in-silico adapter, Flywheel, and real lab execution remain unavailable. Canvas Notes, titles, connectors, and author text are non-authorizing topology only; credentials are verified by an `IdentityProvider` and never persisted.

---

## 5. Actor catalog

| ID | Actor | Role | Status | Evidence |
|---|---|---|---|---|
| ACT-LITL-01 | Scientist / Researcher | Pose scientific questions, choose knowledge scope, initialize `{idea: ...}` | `[MVP]` note `{idea:...}` written by user on canvas | `experiment-workflow.md` "Example happy path" step 3 |
| ACT-LITL-02 | Lab scientist / technician | Review validation evidence; may activate a future mock execution branch | `[MVP partial]` credential-verified scientist approval gate; no real lab execution | Original UC §2.2, §12 Gate 1/3 |
| ACT-LITL-03 | Data / imaging scientist | Prepare R/Python analysis pipeline, read analysis results | `[Future]`, no code path | Original UC §2.3 |
| ACT-LITL-04 | Engineer / platform team | Build canvas workflow, connectors, model orchestration | `[MVP]` — author of `canvus-mcp`/`lab-agent` | Original UC §2.4 |
| ACT-LITL-05 | PI / Lab lead / project lead | Approve a validated proposal after scientist approval | `[MVP partial]` credential-verified lab-lead evidence gate; it does not dispatch real wet-lab work | Original UC §2.5, §12 Gate 3 |
| ACT-LITL-06 | AI harness/orchestrator (`lab-agent`) | Orchestrate loop, call model, write canvas | `[MVP]` | `system-architecture.md` "Runtime apps" |
| ACT-LITL-07 | Model provider (Claude/OpenAI/Ollama/vLLM/internal) | Generate setup/result/decision | `[MVP partial]` only 2 named adapters `openai`/`claude`; Ollama/vLLM only via `LAB_AGENT_OPENAI_BASE_URL` under `openai` provider | `lab_agent/adapters/factory.py` |
| ACT-LITL-08 | Canvus (canvas server) | Durable state store, source of truth for workflow | `[MVP]` | `system-architecture.md` § "State and idempotency" |
| ACT-LITL-09 | Internal knowledge sources (wiki/KG/vector DB/acronym dict) | Grounding context | `[MVP partial]`; RagCluster/read tools plus approved acronym dictionary exist; wiki/KG/vector DB are future adapters/external gates | `canvus_mcp/ragcluster.py`, `lab_agent/evidence.py`, `lab_agent/acronyms.py` |
| ACT-LITL-10 | In-silico / digital-twin service | Validate design before wet lab | `[MVP partial]` deterministic local structural dry-run adapter only; real service disabled/unimplemented | Original UC §8; roadmap Phase 5 |
| ACT-LITL-11 | Robotic/wet-lab system | Execute real experiments | `[Future]`; Phase 7 watcher is execution-disabled and no hardware/lab SDK exists | roadmap "Real robot integration: Future" |
| ACT-LITL-12 | Flywheel / imaging analysis platform | Auto-run analysis gear (e.g., lung fibrosis quantification) | `[Future]`, no wrapper | Original UC §9.4; roadmap Phase 6 |
| ACT-LITL-13 | Administrator / auditor | Audit log, quarantine reset/backup, observability, multi-user isolation | **`MVP partial / Future inferred`** — operator CLI and hash-chained audit exist; multi-user observability remains Future; actor is inferred, not explicitly listed in original UC | `lab_agent/admin.py`, roadmap Phase 7; no direct actor reference in vision |

---

## 6. Stakeholder interests

| Stakeholder | Interest |
|---|---|
| Scientist/Researcher | Shorten time from insight to experiment; reuse internal knowledge instead of starting over |
| Lab lead / PI | Control risk/cost before permitting wet lab execution; ensure audit trail exists |
| Data/imaging scientist | Standardized analysis pipeline, reliable results for interpretation |
| Engineer/platform team | Clear architecture, model-agnostic, easy to extend to Flywheel/in-silico |
| GSK (organization) | Convert 10 years of dispersed knowledge into reusable asset; create compound interest for knowledge |
| Compliance/security | No credential leaks; sensitive data stays within permitted provider boundary (data locality) |

---

## 7. Assumptions, dependencies, constraints

**Assumptions**

- Canvus canvas is the single source of truth for current workflow state `[MVP]`.
- User manipulates connectors intentionally (connector = state transition signal).
- Model is correctly configured via `LAB_AGENT_MODEL_PROVIDER` before execution.

**Dependencies**

- `apps/canvus-mcp` must run with valid credentials (`CANVUS_API_URL`, `CANVUS_API_KEY`) for `lab-agent` to function.
- Model adapter (`openai` or `claude`) must be available, selected by task-stage routing, locality-authorized for every evidence classification, and priced in the configured versioned table; otherwise governed dispatch is denied. OpenAI-compatible operation uses explicit matching `LAB_AGENT_OPENAI_BASE_URL`/`LAB_AGENT_PROVIDER_ENDPOINTS["openai"]`, not a named new provider `[MVP partial]`.
- Durable local operation depends on the SQLite WAL ledger at `LAB_AGENT_STATE_DB_PATH`; it is local-disk/single-host scoped and must be backed up/restored intentionally. The same DB contains generated-artifact records, versions, token hashes, Browser widget mappings, model-call intents, and budget reservations.
- Browser artifact writes depend on a configured `LAB_AGENT_ARTIFACT_PUBLIC_BASE_URL` reachable by intended Canvus clients; production requires HTTPS/private ingress. Live external reachability/TLS verification is a deployment gate, not proven by this repo.
- Local-source ingestion depends on `CANVUS_MCP_INGESTION_DB_PATH` and `CANVUS_MCP_INGESTION_CACHE_DIR`, static role tokens/exact canvas scopes, an optional restrictive PDF password file, and an independently managed worker. The MCP server does not run a worker, has no worker CLI, and has no ingestion backup/restore/integrity CLI.
- Flywheel, knowledge/versioning, and wiki/KG/vector retrieval services remain `[Future]`. Phase 7 has a typed in-silico boundary and deterministic dry-run adapter, but a real adapter remains disabled/unimplemented and fails closed.

**Constraints**

- Model receives only read tools (`READ_TOOLS` in `lab_agent/tool_bridge.py`); successful reads are wrapped as `untrusted_data` and captured in a per-run evidence ledger; all writes go through orchestrator (`lab_agent/nodes.py`) — strict read/write separation.
- Must not hard-code dependence on any specific provider (`code-standards.md` § "Provider and harness boundaries").
- Governed dispatch requires a configured HTTPS endpoint/classification allowlist and known versioned model pricing. `scan_experiment_workflow` copies the canvas classification into every actionable idea/setup/loop entry, and `lab-agent` supplies it to the trigger governance context before the first governed model call. Unknown/restricted classifications, endpoint/provider omissions, missing price, or failed reservation deny dispatch; external organization approval, rate maintenance, live endpoint/SDK/API validation, and invoice reconciliation remain operator gates.
- Usage is normalized as `exact`, `estimated`, or `unavailable`; Claude exact input includes cache-create/read tokens. Estimates cover provider-visible messages, tools, response schema, schema name, and output cap for governance only — never provider invoices.
- Durable idempotency, model-call intents/reservations, and artifact storage are scoped to one local host and one active writer per canvas; run accounting resets per trigger while canvas totals/active holds survive restart. Multi-host/shared-store deployment is a future Postgres-or-equivalent migration trigger.
- Capability URLs are bearer secrets; full token-bearing URLs must not be logged, audited, printed, pasted into model context, or copied into reports. Provider prompts, responses, secrets, and raw error text likewise do not enter durable intent/audit records.
- Harness-first architectural direction is **`[Proposed]`**, not yet ratified — new designs should not assume approval.

---

## 8. Use-case map and relationships

```text
                     ┌───────────────────────────────────────┐
                     │  UC-LITL-02 (top-level)                │
                     │  Close the loop from design to new data│
                     └───────────────────────────────────────┘
                        │ include                  │ extend (safety gate,
                        ▼                           │  insert between design & lab execution)
        ┌───────────────────────────────┐           ▼
        │ UC-LITL-01 (supporting)        │  ┌───────────────────────────────┐
        │ Design next experiment from    │  │ UC-LITL-03 (supporting)        │
        │ internal knowledge             │  │ In silico before wet lab       │
        └───────────────────────────────┘  └───────────────────────────────┘
```

- **UC-LITL-02** is the most complete use case, covering the entire 9-step loop.
- **UC-LITL-01** is the entry point/`include` of UC-LITL-02 — creates experiment design and may perform preliminary screening; any handoff to execution must go through UC-LITL-03, post-silico scientist review, and lab-lead approval.
- **UC-LITL-03** is an `extend` inserted between "Experiment design" and "Lab execution" of UC-LITL-02, when in-silico validation is needed before permitting wet lab.

No additional use case per canvas node type is created (§ vision section 7 lists 10 node types — those are artifacts/state within one use case, not 10 separate use cases).

---

## 9. Functional requirements catalog

| ID | Requirement | Priority | Status |
|---|---|---|---|
| FR-LITL-001 | Knowledge grounding from internal knowledge scope before design generation | Must | `[MVP partial]` — RagCluster/read-tool context with per-run evidence ledger; no real wiki/KG/vector DB |
| FR-LITL-002 | AI generates experiment design (`ExperimentSetup`) from idea + context | Must | `[MVP]` — setup writes are gated by evidence/citation/ambiguity validation |
| FR-LITL-003 | Validate design via in-silico/digital-twin before permitting wet lab | Must (safety) | `[MVP partial]` — typed deterministic dry-run result, canonical hashes, and durable evidence exist; real scientific validation is disabled/unimplemented |
| FR-LITL-004 | Scientist review/approval gate for experiment design | Must (safety) | `[MVP partial]` — credential-verified, durable scientist approval/reject evidence; no Canvas approval UI or topology authorization |
| FR-LITL-005 | Lab-lead approval gate for resource/budget before wet lab | Must (safety) | `[MVP partial]` — credential-verified lab-lead gate after scientist; real resource/lab integration remains unavailable |
| FR-LITL-006 | Execute experiment (human lab or robotic lab) | Must | `[MVP mock partial]` — default watcher execution is disabled; an explicitly enabled Phase 8-compatible branch creates only model-generated `MOCK_RESULT`, with no real robot/lab connection |
| FR-LITL-007 | Generate raw data from execution (imaging/omics/table/assay) | Must | `[MVP mock]` — model emits mock `ExperimentResult`, labeled mock |
| FR-LITL-008 | Automated analysis via Flywheel/HPC pipeline | Must | `[Future]` — no Flywheel wrapper |
| FR-LITL-009 | Interpret results against original hypothesis | Must | `[MVP mock form]` — `[EXP:Result vNNN]` generated by model |
| FR-LITL-010 | Update versioned knowledge base (no overwrite) | Must | `[Future]` — no Knowledge Update Service |
| FR-LITL-011 | Propose next round of experiments (redesign) | Must | `[MVP]` when `LoopDecision.proceed=true` |
| FR-LITL-012 | Decide to continue/stop loop (`LoopDecision`) | Must | `[MVP]` |
| FR-LITL-013 | Backstop to prevent infinite loop (max round, cost threshold) | Must (safety) | `[MVP]` — model decision plus distinct max-round, token, cost, wall-time, no-progress, locality, and reservation closures; no writes follow a terminal closure |
| FR-LITL-014 | Operate model-provider-agnostic (Claude/OpenAI/Ollama/vLLM/internal) | Must | `[MVP partial]` — provider-neutral task-stage routing over 2 named adapters; other compatible deployments use the explicitly approved OpenAI-compatible endpoint, not a dedicated adapter |
| FR-LITL-015 | Respond explicitly when internal evidence is insufficient ("insufficient evidence") | Should | `[MVP]` — writes deduplicated `[EXP:Needs Input]`; executable setup remains pending |
| FR-LITL-016 | Handle ambiguous acronyms: detect → approved dictionary → ask confirmation | Should | `[MVP partial]` — local approved dictionary and Needs Input exist; external wiki/KG/vector lookup remains Future |
| FR-LITL-017 | Handle Flywheel job failure: show failed, preserve data path, allow rerun | Should | `[Future]` |
| FR-LITL-018 | Create conflict note when new data contradicts old knowledge | Should | `[Future]` |
| FR-LITL-019 | Idempotency: no duplicate setup/result/closed/connector loop-processing | Must | `[MVP]` — durable local SQLite attempts + side-effect intents/outbox; local-disk, single-host scope |
| FR-LITL-020 | Ingest large multimodal data with chunking/caching/resumable capability | Should | `[MVP partial]` — complete local-source contract: SHA-256/extractor-version dedup, leased resumable units, bounded chunks/status, authenticated canvas scope, and evidence/locality integration. Video and spreadsheets other than CSV/TSV remain unsupported/external gates. |
| FR-LITL-021 | Represent workflow as directed node + connector on canvas | Must | `[MVP]` |
| FR-LITL-022 | Clearly label all simulated results as "mock" | Must | `[MVP]` |

---

## 10. Detailed use cases

### 10.1 UC-LITL-02 — Close the loop from design to new data (top-level)

| Field | Value |
|---|---|
| ID | UC-LITL-02 |
| Name | Close the loop from design to new data |
| Level | Top-level (goal-level) |
| Scope | Entire Lab-in-the-Loop loop |
| Status | `[MVP partial]` — core happy path is implemented and covered by local tests; live Canvus E2E demo remains Phase 2; most of target flow remains `[Future]` |
| Priority | Must |
| Primary actor | ACT-LITL-01 (Scientist/Researcher) |
| Supporting actors | ACT-LITL-02, ACT-LITL-03, ACT-LITL-05, ACT-LITL-06, ACT-LITL-07, ACT-LITL-08, ACT-LITL-10, ACT-LITL-11, ACT-LITL-12 |
| Goal | From internal knowledge, AI proposes experiment, experiment is executed (silico/human/robot), new data is analyzed, knowledge is updated with version, and next round is proposed |

**Trigger:** User creates/connects note `{idea: ...}` from a `RAGCluster_` scope on canvas `[MVP]`; or user directly asks a scientific question via a separate chat surface `[Future — no surface outside canvas note]`.

**Preconditions:**
- Knowledge scope with loaded documents exists (`RAGCluster_` + feeder) `[MVP]`; real wiki/KG/vector DB `[Future]`.
- Model adapter available per `LAB_AGENT_MODEL_PROVIDER` `[MVP]`.

**Minimal guarantee:** No node/connector is overwritten if idempotency rule is followed (§12 Business rules).

**Success guarantee (postconditions):** Canvas has node sequence `idea → setup → robot/result → (loop back) → setup vNNN+1` or `→ closed` `[MVP]`; knowledge base has new version `[Future — no Knowledge Update Service yet, currently only canvas notes]`.

**Main success flow** (canonicalized target flow; expands vision validation step into explicit gates):

| # | Step | Status | Notes |
|---|---|---|---|
| 1 | Knowledge grounding — retrieve wiki + historical data by context | `[MVP partial]` | RagCluster/read tools with per-run evidence ledger; no real wiki/KG/vector DB |
| 2 | Experiment design — AI generates proposal | `[MVP]` | `lab-agent` creates `[EXP:Setup vNNN]` only after citation/evidence/ambiguity gates pass |
| 3 | In-silico validation — predict outcome, uncertainty, risk, recommendation | `[MVP partial]` | Typed `InSilicoResult`, canonical hash, durable evidence, and deterministic dry run; it is not scientific validation and the real adapter is disabled |
| 4 | Scientist review — read in-silico results and approve/revise/reject | `[MVP partial]` | Credential-bearing `IdentityProvider` verification and ordered durable evidence; Canvas UI/topology is non-authorizing |
| 5 | Lab-lead approval — review resource, budget, safety before wet lab | `[MVP partial]` | Credential-bearing durable gate after scientist; it does not claim live resource or lab integration |
| 6 | Wet-lab/robot execution | `[Future]` | Default Phase 7 watcher dispatch is disabled; Phase 8-compatible opt-in remains model-generated `MOCK_RESULT` only, with no hardware/lab SDK |
| 7 | Data generation | `[MVP mock]` | Model emits mock `ExperimentResult`, labeled mock |
| 8 | Automated analysis (Flywheel/HPC) | `[Future]` | No Flywheel wrapper |
| 9 | Result interpretation | `[MVP mock form]` | `[EXP:Result vNNN]` generated by model |
| 10 | Knowledge update (versioned) | `[Future]` | No knowledge/versioning service |
| 11 | Next-round/close decision | `[MVP]` | `proceed=true` → new setup; `proceed=false` → `[EXP:Closed]` |

**Alternate flows:**
- **STOP decision:** model returns `LoopDecision.proceed=false` → create `[EXP:Closed]` with `Decision: STOP` and `Reason`, then connect result→closed `[MVP]` (`lab_agent/orchestrator.py`, `lab_agent/render.py`).
- **Manual re-trigger:** user manually connects result→setup to force a new round `[MVP]` (roadmap Phase 3 "Manual canvas edits can intentionally trigger a new round").

**Exception flows:**

| Exception | Vision requirement | Current status |
|---|---|---|
| Experiment design has insufficient evidence | Return reason + suggestion for in-silico/retrieve more | `[MVP]` — explicit Needs Input Browser artifact; no executable setup write |
| Ambiguous acronym (e.g., `BIA`) | Detect → retrieve dict → ask confirm → regenerate | `[MVP partial]` — approved local dictionary scan and Needs Input exist; external retrieval-backed dictionary remains Future |
| Flywheel job fails | Show failed, preserve data path, allow rerun, don't update KB | `[Future]` — Flywheel doesn't exist |
| New data conflicts old knowledge | Create conflict note, keep both hypotheses | `[Future]` — no knowledge store |
| Infinite loop | Max iteration, stop condition, cost threshold | `[MVP]` — max-round, token/cost, wall-time, and no-progress closures are distinct/audited; locality/reservation denial also closes before follow-on writes |
| MCP server unavailable | — | `[MVP]` CLI fails/logs warning, watcher continues polling |
| Model does not emit correct schema | — | `[MVP]` current run writes nothing; canvas stays pending; durable attempt is failed/backed off/quarantine-eligible |
| Provider call uncertain after submission | Do not duplicate charged/side-effecting call | `[MVP]` submitted/executed/ambiguous intent reconciles only with provider capability; otherwise it blocks safely with no blind redispatch |
| Human rejection at gate | Reject design/result, request revise | `[MVP]` — credential-bearing scientist or lab-lead rejection is durable evidence that projects `REJECTED`; no Canvas approval UI exists |
| Resource/budget rejection | Harness denies numeric/price-unavailable model dispatch; lab-lead resource approval remains separate | `[MVP partial]` — per-run/per-canvas token/cost reservations and unknown-price denial exist; no human lab-resource approval gate |
| Duplicate/retry/idempotency failure | Don't create duplicate on retry | `[MVP]` — connector graph checks plus durable `workflow_attempts` and side-effect intents; generated Browser artifacts use title tags and bucket probes, while legacy Note recovery remains available for migrated canvases |

**Business rules reference:** BR-LITL-001, BR-LITL-002, BR-LITL-003, BR-LITL-004, BR-LITL-006.

**Data inputs/outputs:** Input = RagCluster context, idea note text, setup artifact text, result artifact text. Output = `[EXP:Setup vNNN]`, `[EXP:Result vNNN]`, `[EXP:Closed]` Browser artifacts backed by `ArtifactStore`, with legacy generated Notes still readable during migration — actual schema in `lab_agent/models/experiment.py` plus artifact metadata/versioning in `lab_agent/models/artifact.py` (see §13).

**Approval points:** Phase 7 implements the validation and approval segment: a current typed validation `proceed` requires credential-verified scientist then lab-lead evidence to project `APPROVED_FOR_WET_LAB`. The projection remains `execution_enabled=false`; the remaining vision gates and real execution are future work.

**Implementation mapping:** `lab_agent/orchestrator.py` (`run_loop`, `generate_setup`, `run_on_robot`), `lab_agent/watch.py`, `canvus_mcp/tools/experiments.py` (`scan_experiment_workflow`, `detect_experiment_loops`).

**Acceptance criteria:**

| ID | Criterion | Status |
|---|---|---|
| AC-UC-LITL-02-001 | User note `{idea:...}` connected from `RAGCluster_` → agent creates `[EXP:Setup v001]` | `[MVP]` code path exists and local tests pass; live Canvus E2E verification pending |
| AC-UC-LITL-02-002 | Connect setup→`Robot_` → agent creates `[EXP:Result v001]` clearly labeled mock | `[MVP]` code path exists and local tests pass; live Canvus E2E verification pending |
| AC-UC-LITL-02-003 | Connect result→setup → agent decides CONTINUE (`[EXP:Setup v002]`) or STOP (`[EXP:Closed]`) | `[MVP]` code path exists and local tests pass; live Canvus E2E verification pending |
| AC-UC-LITL-02-004 | No duplicate setup/result/closed/connector side effects across repeated scans or restart/retry windows | `[MVP]` local regression tests pass; live Canvus E2E verification pending |
| AC-UC-LITL-02-005 | Node Experiment Design connects to Flywheel Data Node; Flywheel job runs (or mock) and returns Analysis output to canvas (MVP 2 — Flywheel-connected demo) | `[Future]` not achieved |
| AC-UC-LITL-02-006 | Validation returns typed predicted outcome + uncertainty; ordered approvals are required before an eligible execution state | `[MVP partial]` — deterministic dry run only, durable evidence/identity gates implemented, `execution_enabled=false`, and no real scientific/lab execution |

---

### 10.2 UC-LITL-01 — Design next experiment from internal knowledge (supporting, include)

| Field | Value |
|---|---|
| ID | UC-LITL-01 |
| Name | Design next experiment from internal knowledge |
| Level | Supporting (user-goal, `include` of UC-LITL-02) |
| Scope | Create only one experiment proposal — no execution phase |
| Status | `[MVP]` for proposal generation; `[Future]` for explicit review→approval gate |
| Priority | Must |
| Primary actor | ACT-LITL-01 (Scientist) |
| Supporting actors | ACT-LITL-06 (AI harness), ACT-LITL-07 (Model provider), ACT-LITL-08 (Canvus) |
| Goal | From a knowledge scope + question, create a well-grounded experiment proposal with evidence from internal knowledge |

**Trigger:** User selects knowledge scope on canvas and asks "Design the next experiment." (vision §4); currently realized `[MVP]` as note `{idea: ...}` connected from `RAGCluster_`.

**Preconditions:** Knowledge scope exists (RagCluster + feeder) `[MVP]`; optional constraints can be emitted as setup `constraints` strings `[MVP partial]`, while typed budget/assay/disease/equipment subfields remain `[Future]` (see §13.4 and §18).

**Minimal guarantee:** If context is insufficient, system does not create unsupported design (caution principle in `code-standards.md`).

**Success guarantee (postconditions):** `[EXP:Setup vNNN]` created and connector attached from idea when the grounding gate is executable `[MVP]`; otherwise a Needs Input artifact is created for insufficient evidence/ambiguity, or invalid citations write nothing and remain retryable.

**Main success flow:**
1. User selects knowledge scope on canvas `[MVP]`.
2. User asks (via note `{idea: ...}`) "Design the next experiment" `[MVP]`.
3. System retrieves internal context through RagCluster/read tools and records successful reads in a per-run evidence ledger `[MVP partial — no wiki/KG/vector DB]`.
4. Model creates experiment proposal (`ExperimentSetup`) with citations/evidence status/ambiguity flags `[MVP]`.
5. System validates citations and ambiguity before attaching proposal to canvas as Experiment Design Node (`[EXP:Setup vNNN]`) `[MVP]`.
6. The watcher produces a typed deterministic dry-run validation result `[MVP partial]`; it is explicitly not scientific validation.
7. Credential-verified scientist and then lab-lead decisions project the only eligible wet-lab state `[MVP partial]`; no Canvas text, connector, or author field can authorize the transition, and execution remains disabled.

**Alternate/Exception flows:** Insufficient context or unresolved ambiguity → write deduplicated `[EXP:Needs Input]` Browser artifact `[MVP]`; invalid/fabricated citations → write nothing and keep the attempt retryable (see FR-LITL-015/016).

**Business rules reference:** BR-LITL-001, BR-LITL-005.

**Data inputs/outputs:** Input = knowledge scope id, query text, optional constraints `[Vision]`, and retrieved evidence ledger `[MVP]`. Output = `ExperimentSetup` with `rationale`, `inputs`, `conditions`, `steps`, `parameters`, `expected_readouts`, plus additive grounding fields `hypothesis`, `success_criteria`, `constraints`, `confidence`, `citations`, `evidence_status`, and `ambiguity_flags` (actual schema — see §13). Vision fields for recommended analysis pipeline and typed budget/platform constraints remain gaps.

**Approval points:** A current typed validation `proceed` is followed by credential-verified scientist review and credential-verified lab-lead approval. Those durable, canvas-scoped records may project `APPROVED_FOR_WET_LAB`; they do not enable execution in Phase 7.

**Implementation mapping:** `lab_agent/orchestrator.py:generate_setup`, `canvus_mcp/tools/experiments.py` (`ideas_needing_setup`).

**Acceptance criteria:**

| ID | Criterion | Status |
|---|---|---|
| AC-UC-LITL-01-001 | User asks → receive `[EXP:Setup vNNN]` with complete rationale/inputs/conditions/steps/parameters/expected_readouts structure | `[MVP]` code path exists and local tests pass; live Canvus E2E verification pending |
| AC-UC-LITL-01-002 | Clear approve button/track before moving to execution | `[Future]` not achieved |
| AC-UC-LITL-01-003 | System returns "insufficient internal evidence" when context is inadequate | `[MVP]` local tests pass; writes Needs Input instead of executable setup |

---

### 10.3 UC-LITL-03 — In silico before wet lab (supporting, extension/safety gate)

| Field | Value |
|---|---|
| ID | UC-LITL-03 |
| Name | In silico before wet lab |
| Level | Supporting (`extend` — inserted between "Experiment design" and "Lab execution" of UC-LITL-02) |
| Scope | Typed validation and ordered approval evidence before any eligible execution state |
| Status | `[MVP partial]` — deterministic local dry-run validation, durable evidence, and ordered human gates exist; real scientific validation and real wet-lab execution do not |
| Priority | Must (safety) |
| Primary actor | ACT-LITL-10 (in-silico adapter boundary) |
| Supporting actors | ACT-LITL-01 (Scientist), ACT-LITL-05 (Lab lead) |
| Goal | Fail closed unless one exact proposal has current validation and ordered credential-verified approval evidence |

**Trigger:** A canonical `[EXP:Setup]` Browser artifact is available for validation.

**Preconditions:** The setup payload parses as `ExperimentSetup`. Its SHA-256 proposal identity is computed through `litl-canonical-json-v1`, not from rendered canvas text. The real validation adapter remains unavailable; the default adapter is deterministic local dry-run behavior only.

**Minimal guarantee:** No Canvas Note, title, connector, Browser label, or author text can create approval evidence. A credential is verified through `IdentityProvider` and never persisted.

**Success guarantee (postconditions):** The SQLite ledger holds an append-only, canvas-scoped typed validation record and, if the validation decision is `proceed`, may hold one scientist and one lab-lead decision bound to the exact proposal and validation-result hashes. The Browser Validation and Approval Status artifacts project that evidence. `APPROVED_FOR_WET_LAB` remains an eligibility projection with `execution_enabled=false` in Phase 7.

**Main success flow:**
1. The watcher loads the canonical Setup payload and derives its versioned proposal hash.
2. `DeterministicInSilicoAdapter` returns a typed `InSilicoResult` with predicted outcome, confidence, uncertainty, assumptions, risk flags, recommended changes, and decision `[MVP partial]`. The rendered artifact explicitly says **DETERMINISTIC DRY RUN — NOT SCIENTIFIC VALIDATION**.
3. The result is append-only durable evidence and projects `NEEDS_SCIENTIST_REVIEW` for a `proceed`, `NEEDS_REVIEW` for a `revise`, or `REJECTED` for a `reject`.
4. A scientist approval/rejection requires credential verification for the scientist role. An approve projects `NEEDS_LAB_LEAD_APPROVAL`.
5. A lab-lead approval/rejection requires credential verification for the lab-lead role. A second approval projects `APPROVED_FOR_WET_LAB`.
6. Phase 7 does not execute work after that projection: watcher execution is disabled by default. Any explicit later Phase 8-compatible path can produce only a model-generated `MOCK_RESULT`, not a real lab action.

**Alternate/exception flows:**
- A typed validation `revise` returns to review; `reject` projects `REJECTED` `[MVP]`.
- Wrong role/order, a duplicate role, a missing or invalid identity credential, validation metadata mismatch, or an outdated proposal/result hash fails closed `[MVP]`.
- Replay of a stable approval is idempotent even if identity-verification timestamps vary; incompatible replay fails. Stale validation/approval history remains retained but never authorizes edited content `[MVP]`.
- Adapter timeout, provider failure, or invalid schema writes only sanitized failure metadata and does not advance the gate `[MVP]`.

**Implementation mapping:** `lab_agent/models/validation.py`, `lab_agent/integrations/in_silico.py`, `lab_agent/orchestrator_validation.py`, `lab_agent/approval.py`, `lab_agent/approval_service.py`, `lab_agent/integrations/identity.py`, `lab_agent/state/validation_evidence.py`, and `lab_agent/state/gate_approvals.py`.

**Acceptance criteria (Phase 7 local/source gate):**

| ID | Criterion | Status |
|---|---|---|
| AC-UC-LITL-03-001 | A canonical Setup receives a typed, hash-bound validation result with predicted outcome and uncertainty | `[MVP partial]` — deterministic structural dry run only |
| AC-UC-LITL-03-002 | Validation chooses proceed/revise/reject and projects the corresponding safe state | `[MVP]` local/source gate |
| AC-UC-LITL-03-003 | Only current, credential-verified scientist then lab-lead approvals project `APPROVED_FOR_WET_LAB` | `[MVP]` local/source gate; topology is non-authorizing |
| AC-UC-LITL-03-004 | Default watcher operation cannot enable or dispatch wet-lab execution | `[MVP]` `wet_lab_execution_enabled=false`; no real hardware/lab SDK |

---

## 11. Workflow state model

Two **distinct state layers** should not be conflated:

### 11.1 Layer 1 — Current canvas marker `[MVP]`

Connector presence is the workflow transition signal (`system-architecture.md` § "State and idempotency"). The SQLite ledger is the durable operational record for attempts, leases, side-effect intents, and audit; it does not replace the canvas graph as the source of workflow truth.

```text
{idea: ...} (no setup yet)
     → [EXP:Setup vNNN] Browser artifact           (artifact state: RUNNING)
        → Robot_ (mock execution)
           → [EXP:Result vNNN] Browser artifact    (artifact state: ANALYSIS_COMPLETE)
              → { [EXP:Setup vNNN+1] Browser artifact (artifact state: RUNNING)  |  [EXP:Closed] Browser artifact (artifact state: CLOSED) }
```

Generated Browser artifacts carry `DecisionState` in their canonical `ArtifactStore` record and render it in the Browser HTML header. Legacy generated Notes may carry a `Status: <DecisionState.value>` line from the older `create_node` path during migration.

### 11.2 Layer 2 — Decision-state lifecycle and gate projection

```text
DRAFT → NEEDS_REVIEW → APPROVED_FOR_IN_SILICO → IN_SILICO_RUNNING
      → IN_SILICO_COMPLETE → NEEDS_SCIENTIST_REVIEW → NEEDS_LAB_LEAD_APPROVAL
      → APPROVED_FOR_WET_LAB
```

`lab_agent/models/states.py` declares the lifecycle. Phase 7 enforces the validation/approval segment through typed durable evidence, rather than accepting a Canvas marker as a transition. The canonical gate sequence is `AI design → validation → scientist → lab lead → eligible execution state`. `APPROVED_FOR_WET_LAB` is not an execution command: approval-status Browser projections retain `execution_enabled=false`, and the watcher default disables execution dispatch.

| DecisionState | Gate behavior | Notes |
|---|---|---|
| `DRAFT` / `NEEDS_REVIEW` | Existing setup/input workflow | Needs Input Browser artifact is still a request/status marker, not approval evidence |
| `APPROVED_FOR_IN_SILICO` | Transition contract | Used to enter validation; Canvas topology cannot assert it |
| `IN_SILICO_RUNNING` / `IN_SILICO_COMPLETE` | Implemented | Canonical typed validation result is required and hash-bound to the current proposal |
| `NEEDS_SCIENTIST_REVIEW` | Implemented | Current `proceed` validation result waits for credential-verified scientist evidence |
| `NEEDS_LAB_LEAD_APPROVAL` | Implemented | Current scientist approval waits for credential-verified lab-lead evidence |
| `APPROVED_FOR_WET_LAB` | Implemented eligibility projection | Requires current ordered evidence; Phase 7 keeps execution disabled |
| `RUNNING` / `ANALYSIS_COMPLETE` / `CLOSED` | Existing mock loop behavior | Direct legacy compatibility remains mock behavior and is not watcher-dispatched by default |
| `KNOWLEDGE_UPDATE_PENDING` | Future | No Knowledge Update Service |
| `REJECTED` | Implemented for gate reject outcomes | A validation/scientist/lab-lead reject projects it without execution |

### 11.3 Canvas node type mapping (vision §7 → current marker)

| Conceptual node (vision) | Current marker/implementation | Status |
|---|---|---|
| Knowledge Scope Node | Widget image `RAGCluster_` | `[MVP]` |
| Query Node | Note `{idea: ...}` | `[MVP]` |
| Experiment Design Node | Browser `[EXP:Setup vNNN]` backed by `ArtifactStore`; legacy Note readable | `[MVP]` |
| Human Review Node | Browser `[EXP:Needs Input]` prompt/status infrastructure; human response Note | `[MVP infrastructure only]` — no approve gate/button or transition workflow |
| In Silico Simulation Node | Browser `[EXP:Validation]` backed by a typed durable result | `[MVP partial]` — deterministic dry run only; real scientific adapter disabled/unimplemented |
| Lab Execution Node | Widget `Robot_` (mock compatibility) | `[Future]` — watcher dispatch disabled by default; no robot/lab SDK |
| Flywheel Data Node | — | `[Future]` — no Flywheel wrapper |
| Analysis Gear Node | — | `[Future]` |
| Result Interpretation Node | Browser `[EXP:Result vNNN]` backed by `ArtifactStore`; legacy Note readable | `[MVP mock form]` |
| Knowledge Update Node | — | `[Future]` — no knowledge/versioning service |
| Next Experiment Node | `[EXP:Setup vNNN+1]` on CONTINUE | `[MVP]` |

`[EXP:Closed]` is an additional marker in this repository (STOP decision + reason), with no direct corresponding node name in the 10 conceptual nodes list in vision §7. Marker matching is exact title-**prefix** check (`str.startswith`, `canvus_mcp/experiments.py`) — e.g., `Robot_Arm_1` matches `Robot_`, but `Robott_` does not.

---

## 12. Business rules

| ID | Rule | Status | Evidence |
|---|---|---|---|
| BR-LITL-001 | Model read-only (`READ_TOOLS`); only orchestrator writes (`create_note`, `create_browser`/`update_browser`, `create_connector`) — strict separation | `[MVP]` | `lab_agent/tool_bridge.py`, `lab_agent/nodes.py` |
| BR-LITL-002 | Idempotency: idea processed only if setup doesn't exist; setup runs only if result doesn't exist; every derived trigger is completed/failed/quarantined in the durable local ledger | `[MVP]` durable local/single-host | `experiment-workflow.md` § Idempotency; `lab_agent/state_store.py` |
| BR-LITL-003 | Don't self-generate acronyms/domain terms without approved evidence; unresolved terms across idea/setup/evidence excerpts trigger Needs Input instead of guessing | `[MVP]` for local dictionary scan; external dictionary sources remain Future | `code-standards.md` § "Grounding and scientific caution"; `lab_agent/grounding.py` |
| BR-LITL-004 | Mock results always clearly labeled as mock | `[MVP]` | `lab_agent/prompts.py` (RESULT_SYSTEM), render output |
| BR-LITL-005 | Proposal must contain complete structure plus evidence/citation status before writes: rationale, inputs, conditions, steps, parameters, expected_readouts, grounding fields | `[MVP]` | `lab_agent/models/experiment.py`, `lab_agent/grounding.py` |
| BR-LITL-006 | Don't auto-send to wet lab without approval; execution needs current validation plus ordered credential-verified approvals | `[MVP]` — default watcher dispatch is disabled; stale/topology-only evidence never authorizes | approval service, roadmap Phase 6 |
| BR-LITL-007 | Don't send every experiment directly to wet lab — validate before eligibility | `[MVP partial]` — deterministic structural dry run, not scientific validation; real adapter remains future | vision §8 |
| BR-LITL-008 | Don't overwrite old knowledge version; always create new version with metadata (`version_id`, `source_experiment_id`, ...) | `[Future]` | vision §9.5 |
| BR-LITL-009 | Mandatory safe-order for wet-lab authorization: `AI design → in-silico validation → scientist review → lab lead approval → wet lab` | `[MVP partial]` — typed/durable order is enforced; only the eligibility projection exists, not real wet-lab execution | approval service, roadmap Phase 6 |

---

## 13. Data contracts and artifacts

### 13.1 `ExperimentSetup` (actual schema — `lab_agent/models/experiment.py`)

| Field | Type | Required | Description |
|---|---|---|---|
| `rationale` | `str` | Yes | Why this setup follows from idea + knowledge |
| `inputs` | `list[str]` | No (default empty) | Materials/samples/compounds/reagents needed |
| `conditions` | `list[str]` | No | Conditions to maintain (temperature, time, concentration, ...) |
| `steps` | `list[str]` | No | Protocol steps in order |
| `parameters` | `list[str]` | No | Tunable parameters, as strings `'name=value'` |
| `expected_readouts` | `list[str]` | No | Measurements this run should produce |
| `hypothesis` | `str` | No (default `""`) | Testable hypothesis this setup evaluates |
| `success_criteria` | `list[str]` | No | Criteria that would count the result a success |
| `constraints` | `list[str]` | No | Known constraints/limits to respect |
| `confidence` | `float | None` | No | Model self-reported confidence, bounded 0.0–1.0 when present |
| `citations` | `list[EvidenceCitation]` | No (gate requires for executable writes) | Ledger source ids supporting setup claims |
| `evidence_status` | `EvidenceStatus | None` | No (gate treats `None` as not sufficient) | Explicit sufficiency assertion; only `sufficient` can pass |
| `ambiguity_flags` | `list[AcronymFlag]` | No | Acronym-like terms and dictionary resolution status |

Additive Phase 4 fields are defaulted for legacy parsing. Defaults do not make a setup executable; `lab_agent/grounding.py` must validate evidence sufficiency, citations, and ambiguity before writes.

### 13.2 `ExperimentResult` (actual schema)

| Field | Type | Required | Description |
|---|---|---|---|
| `summary` | `str` | Yes | One-line outcome summary |
| `observations` | `list[str]` | No | Main observations |
| `metrics` | `list[str]` | No | Quantitative results, as strings `'name=value'` |
| `quality_flags` | `list[str]` | No | Quality-control issues if any |

### 13.3 `LoopDecision` (actual schema)

| Field | Type | Required | Description |
|---|---|---|---|
| `proceed` | `bool` | Yes | `true` to run next round; `false` to stop |
| `reason` | `str` | Yes | Reason to continue/stop |
| `next_focus` | `str` | No (default `""`) | If proceed, what should next round change/explore |

### 13.4 Gap versus target/vision schema `[Future]`

Fields still required by the full vision but not yet implemented as separate typed contracts — don't confuse with the Phase 4 additive fields above:

| Target field (vision) | In actual schema? | Notes |
|---|---|---|
| `risk_flags` / explicit risk-uncertainty | No | Have `expected_readouts`, `constraints`, and ambiguity flags, but no separate risk field |
| `constraints.budget_limit`, `assay_type`, `disease_area`, `available_platforms` | Partial | `constraints` is a free-text list, not typed structured subfields |
| Recommended analysis pipeline | No | Vision §4/§9.4; no Flywheel/HPC wrapper |
| In-silico output (`predicted_outcome`, `confidence`, `assumptions`, `risk_flags`, `recommended_changes`, `decision`) | Yes | Typed `InSilicoResult`; deterministic dry run only, not scientific validation — see UC-LITL-03 |
| Audit/version metadata (`version_id`, `source_experiment_id`, `input_data_ids`, `analysis_job_ids`, `created_at`) | No | Vision §9.5; no Knowledge Update Service/Versioning Service |

### 13.5 Canvas generated artifacts (Browser current, Note legacy)

| Artifact | Model-readable marker | Rendered/canonical content |
|---|---|---|
| `[EXP:Setup vNNN]` | `Idea: <idea_id>` / `Round: <n>` | Browser widget backed by `ArtifactStore`; payload rendered from `ExperimentSetup`; legacy Note readable during migration |
| `[EXP:Result vNNN]` | `Setup: <setup_id>` / `Round: <n>` | Browser widget backed by `ArtifactStore`; payload rendered from `ExperimentResult`; legacy Note readable during migration |
| `[EXP:Closed]` | — | Browser widget backed by `ArtifactStore`; payload rendered from `LoopDecision` + backstop reason if any; legacy Note readable during migration |
| `[EXP:Needs Input]` | generated request/status marker | Browser widget backed by `ArtifactStore` with message/reason/context and `NEEDS_REVIEW` state; human response remains a Note; no approval transition workflow is implemented |

### 13.6 Local-source ingestion contract `[MVP partial]`

The implementation-plan Phase 6 (roadmap Phase 4c) contract is deliberately narrower than the full vision's "all multimodal" goal. `enqueue_ingestion` (trusted service) acquires an authorized Canvus PDF/image/asset with a streaming byte cap, verifies SHA-256, records exact canvas-scoped source provenance, and deduplicates work by `(asset_sha256, extractor_version)`. The separate SQLite/WAL ledger has checksummed migrations and durable assets/sources/jobs/units/leases/attempts/chunks/cancellations. Unit completion inserts chunks atomically; only the current lease generation may complete; expired work is reclaimed after restart without exceeding the poison-attempt limit. Retry/backoff, poison, cancellation, and operator retry preserve completed chunks. Verified raw cache publication uses private temporary files, fsync, digest verification, and no-replace promotion; buffered download paths are immutable hash-bound artifacts.

`get_ingestion_status` and paginated `read_ingestion_chunks` are reader operations. `retry_ingestion` and `cancel_ingestion` are operator-only. Responses expose bounded status/progress, chunk text, and scalar provenance/classification only — never raw bytes, cache paths, or capability URLs. Enqueue reads retain exact requested-source provenance; job-only reads that cannot select one source return a bounded source set with `unknown` classification. Reader/trusted-service/operator tokens are `SecretStr` configuration; HTTP calls require one exact Bearer credential and exact canvas scope, while stdio defaults to reader scope. Authorization denial is fixed and metadata-only. Existing non-ingestion reads/downloads may remain anonymous for backward compatibility.

Strict local extractors support UTF-8 text, CSV/TSV, JSON records, PNG/JPEG/GIF metadata, and bounded PDF pages; generic-source MIME is normalized before routing and PDF extraction runs in an isolated resource-limited child. PDF passwords are accepted only from the restrictive optional password-file contract. `malformed`, `encrypted`, `oversized`, and `unsupported` are typed outcomes. Video and spreadsheet formats other than CSV/TSV remain unsupported/external gates. `lab-agent` exposes only the two reader tools, treats status as non-citeable operational data, and admits only bounded chunks as untrusted evidence after ledger retention; its exact namespace allowlist, argument/result/call/transcript bounds, mutation exclusion, and Phase 5 locality checks apply before a later provider call.

---

## 14. Non-functional requirements

| ID | Requirement | Target | Status |
|---|---|---|---|
| NFR-LITL-001 | Security: Canvus credential only in `.env` git-ignored; model receives only read tools; downloaded bytes don't enter model context by default | No credential/secret leaks to canvas or model context | `[MVP]` |
| NFR-LITL-002 | Data locality: some data must not be sent to external provider | Every call requires endpoint + classification authorization before dispatch | `[MVP]` — unknown/restricted/unapproved classifications, endpoints, and providers deny; organization approval matrix remains operational |
| NFR-LITL-003 | Model independence: not Claude-only | 3+ providers run same workflow, no code changes | `[MVP partial]` — provider-neutral stage routing across 2 named adapters; OpenAI-compatible operation requires an explicit matching endpoint override |
| NFR-LITL-004 | Traceability: each setup cites internal sources used | 100% of executable setup writes have valid ledger citations or no write occurs | `[MVP]` local Phase 4 tests pass; future external retrieval adapters remain Future |
| NFR-LITL-005 | Reproducibility/versioning: knowledge version never overwrites | Each update creates new version with full metadata | `[Future]` — no Versioning Service |
| NFR-LITL-006 | Reliability: retry/resume, idempotency durable across restart | Idempotency survives watcher restart | `[MVP]` — local SQLite WAL ledger with attempts/leases/intents/audit; multi-host/shared-store durability remains `[Future]` |
| NFR-LITL-007 | Performance/scalability: chunking/caching/resumable for large multimodal | Bounded local-source work resumes after interruption without redoing completed units | `[MVP partial]` — Phase 4c completed locally on 2026-07-19. Current single-host bounds/defaults include 10 MiB sources and 1–4 worker concurrency; no automatic retention, queue/object store, multi-host deployment, numeric capacity threshold, or large-format/video proof is implemented. |
| NFR-LITL-008 | Observability: structured log, metrics, health check, multi-user dashboard | TBD (no specific metrics yet) | `[Future]` — roadmap Phase 7; currently structured logs plus durable audit events, but no metrics/dashboard/health-check stack |
| NFR-LITL-009 | Cost/token governance: budget/cost threshold, model routing by task | Configured run/canvas envelopes, known versioned pricing, and task-stage routing | `[MVP]` — durable reserve/commit/release; exact/estimated usage; unknown price denies even without cost caps; estimates are not invoices |
| NFR-LITL-010 | Accessibility/operability: CLI `once`/`watch`, admin commands, clear `.env` config, troubleshooting docs | Workflow and operator commands documented | `[MVP]` — `setup-and-operations.md` |

---

## 15. Security, safety, privacy, governance

| Trust boundary | Risk | Control | Status |
|---|---|---|---|
| Canvus credentials | Secret leak | Keep in `apps/canvus-mcp/.env`, git-ignored | `[MVP]` |
| MCP write tools | Unintended canvas mutation | Only orchestrator calls write tool; model receives only read tools | `[MVP]` |
| Downloaded PDF/image / ingested source | Sensitive data or raw-storage traversal | Streaming byte cap before persistence; ignored SHA-256 cache with `0700` dirs/`0600` files and descriptor/no-follow checks; raw bytes, paths, and capability URLs never enter model context | `[MVP]` local-source scope |
| Ingestion MCP access | Cross-canvas data exposure or unauthorized mutation | Strict single Bearer credential per HTTP call, static reader/trusted-service/operator roles, exact canvas allowlists, reader-only stdio default, metadata-only denial audit; old non-ingestion anonymous reads/downloads stay compatible | `[MVP]` |
| Retrieved evidence excerpts | Prompt injection or sensitive-data leakage | Successful read results are `untrusted_data`; status is operational/non-citeable, only bounded chunks can be evidence, and durable audit stores ids/hashes/reasons only and trims rows to payload cap | `[MVP]` |
| Artifact Browser URLs | Bearer capability leak or cross-canvas access | Private bind by default; reachable public base URL through HTTPS/private ingress in production; tokens stored as hashes; no access logs/full URL output; artifact/canvas scope checks; strict CSP/same-origin assets | `[MVP infrastructure]` — live reachability/TLS verification pending |
| Model output | Domain fact hallucination, fabricated citations, guessed acronyms | Ground with RagCluster/read tools; validate citations against per-run ledger; scan idea/setup/evidence excerpts against approved dictionary; schema is structured | `[MVP partial]` |
| Model provider dispatch | Unapproved data flow, unknown price, duplicate/ambiguous submission | Classify/authorize endpoint before dispatch; require known price, durable intent/reservation; typed retry and capability-aware reconciliation only | `[MVP]` local/source gates; organization approval, price maintenance, live API checks remain operational |
| Provider telemetry/errors | Raw prompt/response/secret/diagnostic persistence | Durable records contain fixed categories, digests, counts, and approved metadata only | `[MVP]` |
| Loop autonomy | Runaway execution or side effects after terminal stop | Model decision plus max-round/token/cost/wall-time/no-progress/locality/reservation closures; one closure then no further provider/canvas writes | `[MVP]` |
| Wet-lab authorization | Unapproved experiment execution | Current canonical validation hash plus scientist then lab-lead credential-verified approvals | `[MVP partial]` — durable eligibility gate exists; execution is disabled and no real lab integration exists |

**Mandatory wet-lab authorization chain (canonical, consistent across README/system-architecture/roadmap):**

```text
AI design → in-silico validation → scientist review → lab lead approval → wet lab
```

**Comparison with 5-gate vision (§12):** Original vision meeting describes Gate 1 (scientist approves design) before in-silico, while canonical authorization chain places scientist review after in-silico results. This document standardizes as follows: an optional **preliminary design screening** before in-silico allows revise/reject of design; it **does not authorize wet lab**. Mandatory scientist review occurs after in-silico, followed by lab-lead approval. This standardization requires owner confirmation (see §18).

`DecisionState` includes the post-silico review states, and Phase 7 enforces their validation-to-scientist-to-lab-lead evidence sequence. Preliminary pre-silico screening remains optional and non-authorizing.

**Privacy/data governance:** Model dispatch applies fail-closed source/evidence classification and provider endpoint authorization before content leaves the process `[MVP]`; unknown/restricted/unapproved classifications deny. The repository does not itself approve an organization's provider/locality matrix, validate live endpoints/SDKs, or establish PII policy; those remain external operator controls.

---

## 16. Traceability matrix

| Vision heading | Related UC/FR | Current implementation marker |
|---|---|---|
| §4 (Design next experiment) | UC-LITL-01, FR-LITL-002 | Note `{idea: ...}`, `RAGCluster_` widget, `ideas_needing_setup` (`canvus_mcp/tools/experiments.py`) |
| §5 (Close the loop) | UC-LITL-02, FR-LITL-001…013 | `lab_agent/orchestrator.py`, `lab_agent/watch.py` |
| §7 (Node types) | §11.3 mapping table | Full mapping table in vision §7 lines 278-292 |
| §8 (In silico) | UC-LITL-03, FR-LITL-003 | `models/validation.py`, `orchestrator_validation.py`, deterministic dry-run adapter; real adapter is Future |
| §9.1 (Knowledge Retrieval) | FR-LITL-001 | `canvus_mcp/ragcluster.py` plus `lab_agent/evidence.py` ledger; wiki/KG/vector DB remain Future |
| §9.4 (Flywheel) | FR-LITL-008, FR-LITL-017 | None — distinct from `{exp:}` integration in `integrations/canvus-serving-experiment-prepare/`, unrelated to Flywheel |
| §10 (Model-agnostic) | FR-LITL-014, NFR-LITL-003 | `lab_agent/adapters/factory.py`, `openai_adapter.py`, `claude_adapter.py` |
| §12 (Gates/decision states) | §11 Workflow state model | `lab_agent/models/states.py`, `approval_service.py`, and durable gate evidence implement the validation and ordered-approval segment; real execution remains Future |
| §13 (Error cases) | Exception flows UC-LITL-02 | `experiment-workflow.md` § "Failure handling" |
| §14 (MVP acceptance criteria) | AC-UC-LITL-02-001…006 | §10.1 Acceptance criteria |

---

## 17. Release / implementation coverage matrix

| Phase (roadmap) | Content | Status | Related UC/FR |
|---|---|---|---|
| Phase 0 | Repository extraction | Complete — committed (`git log`) | — |
| Phase 1 | Local verification (test/lint/mypy) | Complete — original stabilization baseline passed; see [development roadmap](development-roadmap.md) Phase 1 | — |
| Phase 2 | Demo canvas operation (end-to-end mock loop) | Pending | UC-LITL-02 happy path |
| Phase 3 | Harness contracts, persistent loop state, generated Browser artifacts | Partially complete — durable local state and generated Browser artifact infrastructure shipped; cross-implementation harness contracts and live public-base/TLS deployment gates remain Future/Pending | FR-LITL-019, FR-LITL-021, NFR-LITL-006, NFR-LITL-010 |
| Phase 4 | Stronger grounding (ledger/citations/acronym/Needs Input) | Complete — 314/314 `lab-agent` tests, focused `canvus-mcp` marker tests 8/8, reviewer score 9.6/10 SEALED; wiki/KG/vector sources remain Future adapters | FR-LITL-001, FR-LITL-015, FR-LITL-016, NFR-LITL-004 |
| Phase 4b | Token/resource governance, model routing | Complete for local/source gates on 2026-07-19 — routing/locality/pricing/budgets/intents/reconciliation/stops verified; `lab-agent` 406/406, `canvus-mcp` 37/37, governance matrix 131/131 across four runs without flakes, endpoint suite 15/15, reviewer cycle 3 9.7/10 SEALED; organization approval matrix, maintained prices, and live provider checks remain operational | FR-LITL-013, NFR-LITL-002, NFR-LITL-009 |
| Phase 4c | Async multimodal ingestion | Complete for local-source implementation — 2026-07-19; final evidence sealed 2026-07-20. `canvus-mcp` full/focused suites 134/77 passed; `lab-agent` full/focused suites 480/122 passed. Ruff, mypy, compileall, locks, builds, workflow parity, tracked/untracked whitespace, and Phase 7 isolation passed; final inspection is 9.7/10 SEALED with `criticalCount: 0`. Deprecation warnings remain; statement/branch coverage is unclaimed. Hosted CI, live credentials, large-format validation, retention/capacity policy, and queue/object storage remain external gates. | FR-LITL-020, NFR-LITL-007 |
| Phase 5 | In-silico validation gate | Complete for Phase 7 local/source gate infrastructure — deterministic dry run, canonical hashes, durable validation evidence; real scientific adapter remains Future | UC-LITL-03, FR-LITL-003 |
| Phase 6 | In-silico + human approval + Flywheel + lab integration | Partially complete — Phase 7 durable scientist/lab-lead gates and execution-default deny are implemented; Flywheel, real in-silico, and real lab integration remain Future | FR-LITL-004, FR-LITL-005, FR-LITL-006, FR-LITL-008, FR-LITL-010, BR-LITL-006, BR-LITL-009 |
| Phase 7 | Production hardening, multi-user, observability | Future | NFR-LITL-006, NFR-LITL-008, ACT-LITL-13 |

**MVP acceptance (vision §14) — status mapping:**

| MVP | Content | Status |
|---|---|---|
| MVP 1 — Text-only simulation | Idea → setup → mock approve → mock result → interpret → update version → suggest next | `[MVP]` core exists with code path; local verification complete (Phase 1), E2E demo (Phase 2) still pending; "update knowledge version" still `[Future]` |
| MVP 2 — Flywheel-connected demo | Setup connects Flywheel Data Node → job runs/mock → Analysis output → Result Interpretation → Knowledge Update versioned → Next Experiment | `[Future]` not achieved — no implementation evidence |
| MVP 3 — In silico gate | AI design → typed validation → predicted outcome + uncertainty → ordered approvals → eligibility projection | `[MVP partial]` — deterministic dry run and durable gate infrastructure only; no real scientific simulation or laboratory execution |

---

## 18. Unresolved decisions

1. Should `ExperimentSetup` add typed subfields for constraints/risk/analysis planning (e.g., `constraints.budget_limit`, `available_platforms`, recommended analysis pipeline), beyond the Phase 4 free-text `constraints` and grounding fields?
2. Actor "Administrator/auditor" (ACT-LITL-13) is inferred from §15 Audit Log + roadmap Phase 7, not explicitly named in original vision. Owner should confirm whether this is a real human actor or just an implementation artifact (audit log) requiring no separate actor.
3. Target-harness architectural direction (`system-architecture.md` § "Target harness boundary", roadmap Phase 3-7) is **`[Proposed]`, not yet ratified by project owner**. This document describes it as part of target vision but does not treat it as approved.
4. Original vision places a scientist design-approval gate before in-silico, while canonical authorization chain places scientist review after in-silico. This spec treats pre-silico step as optional preliminary screening not authorizing wet lab and post-silico review as mandatory gate; owner should confirm this standardization before designing state machine/approval UI.
5. Acronym dictionary ownership and update cadence need a named domain owner before live scientific use; the current seed dictionary is intentionally conservative.

---

## 19. References

- [Original use case (vision)](notes/use-case-lab-in-the-loop.md)
- [System architecture](system-architecture.md)
- [Development roadmap](development-roadmap.md)
- [Experiment workflow](experiment-workflow.md)
- [Code standards](code-standards.md)
- [Setup and operations](setup-and-operations.md)
- [Canvus-serving integration](canvus-serving-integration.md)
- [Project changelog](project-changelog.md)
- `apps/lab-agent/lab_agent/models/experiment.py`
- `apps/lab-agent/lab_agent/models/states.py`
- `apps/lab-agent/lab_agent/orchestrator.py`
- `apps/lab-agent/lab_agent/tool_bridge.py`
- `apps/lab-agent/lab_agent/nodes.py`
- Research report: `plans/reports/researcher-260716-1136-lab-in-loop-use-case-spec.md`
