# Lab-in-the-Loop Use Case Specification

## Document control

| Field | Value |
|---|---|
| Status | Draft for review |
| Version | 1.0 |
| Date | 2026-07-16 |
| Source vision | [docs/notes/use-case-lab-in-the-loop.md](notes/use-case-lab-in-the-loop.md) (original meeting notes, excluded from normalization) |
| Scope | Canonical (normalized) use case specification for the entire Lab-in-the-Loop vision, with annotations of current implementation status in the `lap-in-the-loop` repository |
| Owner / Approver | Pending |
| Status notation | `[MVP]` = code path exists in this repository and confirmed from source; does not imply local/E2E verification completed; `[Future]` = in roadmap/target architecture but not yet implemented; `[Proposed]` = proposed direction (harness-first architecture), not yet ratified by owner |

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
| `[MVP]` | Code path exists in `apps/canvus-mcp` + `apps/lab-agent`, confirmed directly from source; local test/E2E verification still on roadmap Phase 1–2 |
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
  └─ Claude adapter
```

Current system boundary `[MVP]` consists only of the 2 runtime apps above. Target components — Knowledge Retrieval Service, independent Execution Orchestrator, Flywheel wrapper, Knowledge Update Service, In-silico service — are all `[Future/Proposed]`, not yet existing as separate modules (see `docs/system-architecture.md` § "Target harness boundary (proposed)").

---

## 5. Actor catalog

| ID | Actor | Role | Status | Evidence |
|---|---|---|---|---|
| ACT-LITL-01 | Scientist / Researcher | Pose scientific questions, choose knowledge scope, initialize `{idea: ...}` | `[MVP]` note `{idea:...}` written by user on canvas | `experiment-workflow.md` "Example happy path" step 3 |
| ACT-LITL-02 | Lab scientist / technician | Review experiment design; run real lab or connect mock | `[MVP mock]` connects only `Setup → Robot_`; real review gate is `[Future]` | Original UC §2.2, §12 Gate 1/3 |
| ACT-LITL-03 | Data / imaging scientist | Prepare R/Python analysis pipeline, read analysis results | `[Future]`, no code path | Original UC §2.3 |
| ACT-LITL-04 | Engineer / platform team | Build canvas workflow, connectors, model orchestration | `[MVP]` — author of `canvus-mcp`/`lab-agent` | Original UC §2.4 |
| ACT-LITL-05 | PI / Lab lead / project lead | Approve wet lab, resource, budget | `[Future]` — no gate code | Original UC §2.5, §12 Gate 3 |
| ACT-LITL-06 | AI harness/orchestrator (`lab-agent`) | Orchestrate loop, call model, write canvas | `[MVP]` | `system-architecture.md` "Runtime apps" |
| ACT-LITL-07 | Model provider (Claude/OpenAI/Ollama/vLLM/internal) | Generate setup/result/decision | `[MVP partial]` only 2 named adapters `openai`/`claude`; Ollama/vLLM only via `LAB_AGENT_OPENAI_BASE_URL` under `openai` provider | `lab_agent/adapters/factory.py` |
| ACT-LITL-08 | Canvus (canvas server) | Durable state store, source of truth for workflow | `[MVP]` | `system-architecture.md` § "State and idempotency" |
| ACT-LITL-09 | Internal knowledge sources (wiki/KG/vector DB/acronym dict) | Grounding context | `[Future]`; currently only `RagCluster` connector graph | `canvus_mcp/ragcluster.py` |
| ACT-LITL-10 | In-silico / digital-twin service | Validate design before wet lab | `[Future]` | Original UC §8; roadmap Phase 5 |
| ACT-LITL-11 | Robotic/wet-lab system | Execute real experiments | `[Future]`; currently `Robot_` is only a mock widget | roadmap "Real robot integration: Future" |
| ACT-LITL-12 | Flywheel / imaging analysis platform | Auto-run analysis gear (e.g., lung fibrosis quantification) | `[Future]`, no wrapper | Original UC §9.4; roadmap Phase 6 |
| ACT-LITL-13 | Administrator / auditor | Audit log, observability, multi-user isolation | **`Conditional/Future inferred`** — this actor is **not explicitly listed in original UC**; inferred from Audit Log requirement (§15) and roadmap Phase 7 | roadmap Phase 7; no direct actor reference in vision |

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
- Model adapter (`openai` or `claude`) must be available; OpenAI-compatible endpoint (`LAB_AGENT_OPENAI_BASE_URL`) used for Ollama/vLLM/internal model `[MVP partial]`.
- Target components (Flywheel, in-silico, knowledge/versioning service) are **not yet in existence** — all use cases involving them are `[Future]`.

**Constraints**

- Model receives only read tools (`READ_TOOLS` in `lab_agent/tool_bridge.py`); all writes go through orchestrator (`lab_agent/nodes.py`) — strict read/write separation.
- Must not hard-code dependence on any specific provider (`code-standards.md` § "Provider and harness boundaries").
- `LAB_AGENT_LOOP_MAX_ROUNDS` is the only loop backstop currently available — no real cost/token threshold yet.
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
| FR-LITL-001 | Knowledge grounding from internal knowledge scope before design generation | Must | `[MVP partial]` — only RagCluster context, no real wiki/KG/vector DB |
| FR-LITL-002 | AI generates experiment design (`ExperimentSetup`) from idea + context | Must | `[MVP]` |
| FR-LITL-003 | Validate design via in-silico/digital-twin before permitting wet lab | Must (safety) | `[Future]` |
| FR-LITL-004 | Scientist review/approval gate for experiment design | Must (safety) | `[Future]` — no approve/reject button in code |
| FR-LITL-005 | Lab-lead approval gate for resource/budget before wet lab | Must (safety) | `[Future]` |
| FR-LITL-006 | Execute experiment (human lab or robotic lab) | Must | `[MVP mock]` — `Robot_` widget, no real robot/lab connection |
| FR-LITL-007 | Generate raw data from execution (imaging/omics/table/assay) | Must | `[MVP mock]` — model emits mock `ExperimentResult`, labeled mock |
| FR-LITL-008 | Automated analysis via Flywheel/HPC pipeline | Must | `[Future]` — no Flywheel wrapper |
| FR-LITL-009 | Interpret results against original hypothesis | Must | `[MVP mock form]` — `[EXP:Result vNNN]` generated by model |
| FR-LITL-010 | Update versioned knowledge base (no overwrite) | Must | `[Future]` — no Knowledge Update Service |
| FR-LITL-011 | Propose next round of experiments (redesign) | Must | `[MVP]` when `LoopDecision.proceed=true` |
| FR-LITL-012 | Decide to continue/stop loop (`LoopDecision`) | Must | `[MVP]` |
| FR-LITL-013 | Backstop to prevent infinite loop (max round, cost threshold) | Must (safety) | `[MVP partial]` — only `LAB_AGENT_LOOP_MAX_ROUNDS`, no real cost/token threshold |
| FR-LITL-014 | Operate model-provider-agnostic (Claude/OpenAI/Ollama/vLLM/internal) | Must | `[MVP partial]` — 2 named adapters; others via OpenAI-compatible `base_url` |
| FR-LITL-015 | Respond explicitly when internal evidence is insufficient ("insufficient evidence") | Should | `[Future]` |
| FR-LITL-016 | Handle ambiguous acronyms: detect → retrieve dict → ask confirmation | Should | `[Future]` — currently only "don't guess, lower confidence" rule |
| FR-LITL-017 | Handle Flywheel job failure: show failed, preserve data path, allow rerun | Should | `[Future]` |
| FR-LITL-018 | Create conflict note when new data contradicts old knowledge | Should | `[Future]` |
| FR-LITL-019 | Idempotency: no duplicate setup/result/loop-processing | Must | `[MVP]` — session-local, not persistent across restart |
| FR-LITL-020 | Ingest large multimodal data with chunking/caching/resumable capability | Should | `[Future]` |
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
| Status | `[MVP partial]` — core happy path is implemented in code but lacks local/E2E verification; most of target flow remains `[Future]` |
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
| 1 | Knowledge grounding — retrieve wiki + historical data by context | `[MVP partial]` | Only RagCluster context; no real wiki/KG/vector DB |
| 2 | Experiment design — AI generates proposal | `[MVP]` | `lab-agent` creates `[EXP:Setup vNNN]` |
| 3 | In-silico validation — predict outcome, uncertainty, risk, recommendation | `[Future]` | No in-silico service or output schema |
| 4 | Scientist review — read in-silico results and approve/revise/reject | `[Future]` | No approval UI/transition logic |
| 5 | Lab-lead approval — review resource, budget, safety before wet lab | `[Future]` | No resource/budget gate |
| 6 | Wet-lab/robot execution | `[MVP mock]` | `Robot_` widget; not connected to real robot/lab |
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
| Experiment design has insufficient evidence | Return reason + suggestion for in-silico/retrieve more | `[Future]` — no explicit "cannot recommend" response schema |
| Ambiguous acronym (e.g., `BIA`) | Detect → retrieve dict → ask confirm → regenerate | `[Future]` — only "don't guess, lower confidence" rule |
| Flywheel job fails | Show failed, preserve data path, allow rerun, don't update KB | `[Future]` — Flywheel doesn't exist |
| New data conflicts old knowledge | Create conflict note, keep both hypotheses | `[Future]` — no knowledge store |
| Infinite loop | Max iteration, stop condition, cost threshold | `[MVP partial]` — only `LAB_AGENT_LOOP_MAX_ROUNDS`, no advanced duplicate/cost detection |
| MCP server unavailable | — | `[MVP]` CLI fails/logs warning, watcher continues polling |
| Model does not emit correct schema | — | `[MVP]` current run fails, canvas stays pending, retry in next cycle |
| Human rejection at gate | Reject design/result, request revise | `[Future]` — no gate to reject |
| Resource/budget rejection | Lab lead rejects due to insufficient resources/budget | `[Future]` — no budget field/gate |
| Duplicate/retry/idempotency failure | Don't create duplicate on retry | `[MVP]` — idempotency by connector presence; `[Future]` durable across restart |

**Business rules reference:** BR-LITL-001, BR-LITL-002, BR-LITL-003, BR-LITL-004, BR-LITL-006.

**Data inputs/outputs:** Input = RagCluster context, idea note text, setup note text, result note text. Output = `[EXP:Setup vNNN]`, `[EXP:Result vNNN]`, `[EXP:Closed]` — actual schema in `lab_agent/models/experiment.py` (see §13).

**Approval points:** Gates 1–5 (§12 vision) are defined but **only state framework (`DecisionState` enum) exists** — 6/9 states lack transition logic (see §11 Workflow state model).

**Implementation mapping:** `lab_agent/orchestrator.py` (`run_loop`, `generate_setup`, `run_on_robot`), `lab_agent/watch.py`, `canvus_mcp/tools/experiments.py` (`scan_experiment_workflow`, `detect_experiment_loops`).

**Acceptance criteria:**

| ID | Criterion | Status |
|---|---|---|
| AC-UC-LITL-02-001 | User note `{idea:...}` connected from `RAGCluster_` → agent creates `[EXP:Setup v001]` | `[MVP]` code path exists; E2E verification pending |
| AC-UC-LITL-02-002 | Connect setup→`Robot_` → agent creates `[EXP:Result v001]` clearly labeled mock | `[MVP]` code path exists; E2E verification pending |
| AC-UC-LITL-02-003 | Connect result→setup → agent decides CONTINUE (`[EXP:Setup v002]`) or STOP (`[EXP:Closed]`) | `[MVP]` code path exists; E2E verification pending |
| AC-UC-LITL-02-004 | No duplicate setup/result created when watcher scan repeats in same session | `[MVP]` logic exists; local verification pending |
| AC-UC-LITL-02-005 | Node Experiment Design connects to Flywheel Data Node; Flywheel job runs (or mock) and returns Analysis output to canvas (MVP 2 — Flywheel-connected demo) | `[Future]` not achieved |
| AC-UC-LITL-02-006 | Simulation returns predicted outcome + uncertainty; only proceed to lab if human approves (MVP 3 — in-silico gate) | `[Future]` not achieved |

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

**Preconditions:** Knowledge scope exists (RagCluster + feeder) `[MVP]`; optional constraints (budget/assay type/disease area/available equipment) `[Vision]` — confirmed **no corresponding fields** in current `ExperimentSetup` (see §13.4 and §18).

**Minimal guarantee:** If context is insufficient, system does not create unsupported design (caution principle in `code-standards.md`).

**Success guarantee (postconditions):** `[EXP:Setup vNNN]` created and connector attached from idea `[MVP]`.

**Main success flow:**
1. User selects knowledge scope on canvas `[MVP]`.
2. User asks (via note `{idea: ...}`) "Design the next experiment" `[MVP]`.
3. System retrieves internal wiki + relevant documents `[MVP partial — only RagCluster]`.
4. Model creates experiment proposal (`ExperimentSetup`) `[MVP]`.
5. System attaches proposal to canvas as Experiment Design Node (`[EXP:Setup vNNN]`) `[MVP]`.
6. Optional preliminary scientist screening may request revise/reject before in-silico `[Future — no approve/reject button]`.
7. Proposal moves to UC-LITL-03 for in-silico validation; preliminary screening is not wet-lab authorization `[Future]`.

**Alternate/Exception flows:** Insufficient context found → return "insufficient internal evidence" `[Future]` (see FR-LITL-015).

**Business rules reference:** BR-LITL-001, BR-LITL-005.

**Data inputs/outputs:** Input = knowledge scope id, query text, optional constraints `[Vision]`. Output = `ExperimentSetup` with `rationale`, `inputs`, `conditions`, `steps`, `parameters`, `expected_readouts` (actual schema — see §13); vision requires explicit `hypothesis`/`risk`/`success-criteria`/`recommended-analysis-pipeline` — these fields **don't have separate names** in current schema (see gap at §13.3).

**Approval points:** Preliminary design screening may allow revise/reject before in-silico `[Future]`, but does not authorize wet lab. Wet-lab authorization occurs only after UC-LITL-03, scientist review of in-silico results, and lab-lead approval.

**Implementation mapping:** `lab_agent/orchestrator.py:generate_setup`, `canvus_mcp/tools/experiments.py` (`ideas_needing_setup`).

**Acceptance criteria:**

| ID | Criterion | Status |
|---|---|---|
| AC-UC-LITL-01-001 | User asks → receive `[EXP:Setup vNNN]` with complete rationale/inputs/conditions/steps/parameters/expected_readouts structure | `[MVP]` code path exists; E2E verification pending |
| AC-UC-LITL-01-002 | Clear approve button/track before moving to execution | `[Future]` not achieved |
| AC-UC-LITL-01-003 | System returns "insufficient internal evidence" when context is inadequate | `[Future]` not achieved |

---

### 10.3 UC-LITL-03 — In silico before wet lab (supporting, extension/safety gate)

| Field | Value |
|---|---|
| ID | UC-LITL-03 |
| Name | In silico before wet lab |
| Level | Supporting (`extend` — inserted between "Experiment design" and "Lab execution" of UC-LITL-02) |
| Scope | Digital-twin/in-silico validation before permitting wet lab |
| Status | `[Future]` entirely — not a single line of code in `apps/canvus-mcp`/`apps/lab-agent` implements in-silico |
| Priority | Must (safety) |
| Primary actor | ACT-LITL-10 (In-silico/digital-twin service) |
| Supporting actors | ACT-LITL-01 (Scientist, review), ACT-LITL-05 (Lab lead, after) |
| Goal | Block expensive wet lab with a simulation validation step before permitting real execution |

**Trigger:** An `[EXP:Setup]` has been created and requires decision routing before wet lab (vision §8).

**Preconditions:** Setup exists. A preliminary design screening may have occurred, but is not mandatory in the wet-lab authorization chain and does not authorize wet lab execution.

**Minimal guarantee:** No experiment proceeds directly to wet lab bypassing in-silico when gate is activated.

**Success guarantee (postconditions):** Output JSON has structure:

```json
{
  "predicted_outcome": "...",
  "confidence": 0.72,
  "key_assumptions": [],
  "risk_flags": [],
  "recommended_changes": [],
  "decision": "revise_before_wet_lab"
}
```

where `decision ∈ {proceed, revise_before_wet_lab, reject}`.

**Main success flow:**
1. AI Experiment Design exists (from UC-LITL-01/02).
2. Digital Twin / In Silico Simulation runs on the design.
3. Simulation returns predicted outcome + confidence/uncertainty and recommendation: proceed, revise, or reject.
4. Scientist reviews in-silico results; revise/reject returns to design step.
5. Lab lead approves resource, budget, safety for scientist-approved proposal.
6. Wet lab is permitted only when both scientist review and lab-lead approval are complete.

All 6 steps above are `[Future]`.

**Alternate/Exception flows:**
- `decision = revise_before_wet_lab` → return to UC-LITL-01 for design adjustment `[Future]`.
- `decision = reject` → don't move to wet lab, record reason `[Future]`.

**Business rules reference:** BR-LITL-007 (don't send every experiment straight to wet lab — Professor Do's proposal, strong consensus to include in SOW).

**Why it matters:** If lab-in-the-loop runs on wrong design, consequences can include wasted chemicals, antibodies, animal models, robot/lab time, generation of useless data, and corruption of knowledge graph update.

**Data inputs/outputs:** Input = created `ExperimentSetup`. Output = in-silico result JSON (above) — **no corresponding fields in current schema**, completely a gap versus real `ExperimentResult` (see §13.3).

**Approval points:** In-silico threshold → scientist approval of simulation outcome → lab-lead approval of resources/safety. All are `[Future]`; no transition logic or approval UI currently.

**Implementation mapping:** None — 100% Future; roadmap Phase 5 confirms "Status: Future".

**Acceptance criteria (MVP 3, vision §14):**

| ID | Criterion | Status |
|---|---|---|
| AC-UC-LITL-03-001 | Simulation returns predicted outcome + uncertainty | `[Future]` not achieved |
| AC-UC-LITL-03-002 | System recommends proceed/revise/reject | `[Future]` not achieved |
| AC-UC-LITL-03-003 | Only proceed to lab if human approves | `[Future]` not achieved |

---

## 11. Workflow state model

Two **distinct state layers** should not be conflated:

### 11.1 Layer 1 — Current canvas marker `[MVP]`

Connector presence is the state transition signal (`system-architecture.md` § "State and idempotency": "canvas is the durable source of workflow state; agent treats connector presence as the state transition signal").

```text
{idea: ...} (no setup yet)
     → [EXP:Setup vNNN]           (Status: RUNNING)
        → Robot_ (mock execution)
           → [EXP:Result vNNN]    (Status: ANALYSIS_COMPLETE)
              → { [EXP:Setup vNNN+1] (Status: RUNNING)  |  [EXP:Closed] (Status: CLOSED) }
```

Each note has a `Status: <DecisionState.value>` line added by `lab_agent/nodes.py:create_node` (via `state` parameter) — confirmed in `orchestrator.py` (`DecisionState.RUNNING`, `DecisionState.ANALYSIS_COMPLETE`, `DecisionState.CLOSED`).

### 11.2 Layer 2 — Target decision-state lifecycle (vision §12)

```text
DRAFT → NEEDS_REVIEW → APPROVED_FOR_IN_SILICO → APPROVED_FOR_WET_LAB
      → RUNNING → ANALYSIS_COMPLETE → KNOWLEDGE_UPDATE_PENDING → CLOSED | REJECTED
```

`lab_agent/models/states.py` declares all 9 `DecisionState` values (`StrEnum`). **Only 3/9 values are actually written to notes at runtime:** `RUNNING`, `ANALYSIS_COMPLETE`, `CLOSED`. The other six — `DRAFT`, `NEEDS_REVIEW`, `APPROVED_FOR_IN_SILICO`, `APPROVED_FOR_WET_LAB`, `KNOWLEDGE_UPDATE_PENDING`, `REJECTED` — **exist in enum but have no transition logic that assigns them**. This is the most concrete evidence for "no human-approval gate in code" in `system-architecture.md`.

Additionally, the current enum **lacks explicit states for `IN_SILICO_COMPLETE` or `NEEDS_POST_SILICO_REVIEW`**. The enum sequence above does not yet fully represent the canonical authorization order `AI design → in-silico → scientist review → lab lead approval → wet lab`; state model must be revised when implementing Phase 5–6.

| DecisionState | Has transition logic in `orchestrator.py`? | Notes |
|---|---|---|
| `DRAFT` | No | Framework for future gate/approval |
| `NEEDS_REVIEW` | No | Gate 1 (scientist review) — `[Future]` |
| `APPROVED_FOR_IN_SILICO` | No | Gate 1 output → in-silico — `[Future]` |
| `APPROVED_FOR_WET_LAB` | No | Gate 2/3 output — `[Future]` |
| `RUNNING` | **Yes** — assigned when creating `[EXP:Setup vNNN]` | `[MVP]` |
| `ANALYSIS_COMPLETE` | **Yes** — assigned when creating `[EXP:Result vNNN]` | `[MVP]` |
| `KNOWLEDGE_UPDATE_PENDING` | No | Gate 5 (Knowledge Update Service) — `[Future]` |
| `CLOSED` | **Yes** — assigned when `LoopDecision.proceed=false` or backstop | `[MVP]` |
| `REJECTED` | No | Human/lab-lead reject — `[Future]` |

### 11.3 Canvas node type mapping (vision §7 → current marker)

| Conceptual node (vision) | Current marker/implementation | Status |
|---|---|---|
| Knowledge Scope Node | Widget image `RAGCluster_` | `[MVP]` |
| Query Node | Note `{idea: ...}` | `[MVP]` |
| Experiment Design Node | Note `[EXP:Setup vNNN]` | `[MVP]` |
| Human Review Node | — | `[Future]` — no approve gate/button |
| In Silico Simulation Node | — | `[Future]` — see UC-LITL-03 |
| Lab Execution Node | Widget `Robot_` (mock) | `[MVP mock]` — not connected to real robot/lab |
| Flywheel Data Node | — | `[Future]` — no Flywheel wrapper |
| Analysis Gear Node | — | `[Future]` |
| Result Interpretation Node | Note `[EXP:Result vNNN]` (mock) | `[MVP mock form]` |
| Knowledge Update Node | — | `[Future]` — no knowledge/versioning service |
| Next Experiment Node | `[EXP:Setup vNNN+1]` on CONTINUE | `[MVP]` |

`[EXP:Closed]` is an additional marker in this repository (STOP decision + reason), with no direct corresponding node name in the 10 conceptual nodes list in vision §7. Marker matching is exact title-**prefix** check (`str.startswith`, `canvus_mcp/experiments.py`) — e.g., `Robot_Arm_1` matches `Robot_`, but `Robott_` does not.

---

## 12. Business rules

| ID | Rule | Status | Evidence |
|---|---|---|---|
| BR-LITL-001 | Model read-only (`READ_TOOLS`); only orchestrator writes (`create_note`/`create_connector`) — strict separation | `[MVP]` | `lab_agent/tool_bridge.py`, `lab_agent/nodes.py` |
| BR-LITL-002 | Idempotency: idea processed only if setup doesn't exist; setup runs only if result doesn't exist; loop connector processed once per watcher session | `[MVP]` session-local | `experiment-workflow.md` § Idempotency |
| BR-LITL-003 | Don't self-generate acronyms/domain terms without retrieving internal context; if ambiguous, lower confidence instead of guessing | `[MVP partial]` | `code-standards.md` § "Grounding and scientific caution" |
| BR-LITL-004 | Mock results always clearly labeled as mock | `[MVP]` | `lab_agent/prompts.py` (RESULT_SYSTEM), render output |
| BR-LITL-005 | Proposal must contain complete structure: rationale, inputs, conditions, steps, parameters, expected_readouts (actual current schema) | `[MVP]` | `lab_agent/models/experiment.py` |
| BR-LITL-006 | Don't auto-send to wet lab without approval; orchestrator doesn't auto-route to real wet lab | `[Future]` — because no wet-lab integration exists to gate | vision §9.3, roadmap Phase 6 |
| BR-LITL-007 | Don't send every experiment directly to wet lab — must validate via in-silico first (Professor Do's proposal, consensus to include in SOW) | `[Future]` | vision §8 |
| BR-LITL-008 | Don't overwrite old knowledge version; always create new version with metadata (`version_id`, `source_experiment_id`, ...) | `[Future]` | vision §9.5 |
| BR-LITL-009 | Mandatory safe-order for wet-lab authorization: `AI design → in-silico validation → scientist review → lab lead approval → wet lab` | `[Future]` (chain lacks real gates, but canonical order is consistent across README/system-architecture/roadmap) | roadmap Phase 6, see §12 below |

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

Fields **required by vision but with no separate field name** in current schema — don't confuse with "already implemented":

| Target field (vision) | In actual schema? | Notes |
|---|---|---|
| `hypothesis` (explicit, separate from `rationale`) | No | Vision §4 requires separate hypothesis; currently merged into free-text `rationale` |
| Evidence citations (internal sources used) | No | Roadmap Phase 4: "Every setup can cite the internal notes/PDFs/widgets it used" — not achieved |
| `risk_flags` / explicit risk-uncertainty | No | Have `expected_readouts` but no separate risk field |
| `success_criteria` | No | Vision §4 requires; no field |
| `constraints` (budget/assay/disease area/equipment) | No | Vision §9.2 requires; no field in `ExperimentSetup` |
| In-silico output (`predicted_outcome`, `confidence`, `key_assumptions`, `risk_flags`, `recommended_changes`, `decision`) | No | 100% Future — see UC-LITL-03 |
| Audit/version metadata (`version_id`, `source_experiment_id`, `input_data_ids`, `analysis_job_ids`, `created_at`) | No | Vision §9.5; no Knowledge Update Service/Versioning Service |
| `confidence` (number, at `ExperimentSetup` level) | No | Vision §9.2 model output has `confidence: 0.68`; actual schema has no field |

### 13.5 Canvas note artifact (rendered text — not separate Pydantic schema)

| Note | First-line marker | Rendered content |
|---|---|---|
| `[EXP:Setup vNNN]` | `Idea: <idea_id>` / `Round: <n>` | Rendered from `ExperimentSetup` |
| `[EXP:Result vNNN]` | `Setup: <setup_id>` / `Round: <n>` | Rendered from `ExperimentResult` |
| `[EXP:Closed]` | — | Rendered from `LoopDecision` + backstop reason if any |

---

## 14. Non-functional requirements

| ID | Requirement | Target | Status |
|---|---|---|---|
| NFR-LITL-001 | Security: Canvus credential only in `.env` git-ignored; model receives only read tools; downloaded bytes don't enter model context by default | No credential/secret leaks to canvas or model context | `[MVP]` |
| NFR-LITL-002 | Data locality: some data must not be sent to external provider | TBD (no per-provider/endpoint control yet) | `[Future]` — roadmap Phase 4b |
| NFR-LITL-003 | Model independence: not Claude-only | 3+ providers run same workflow, no code changes | `[MVP partial]` — only 2 named adapters; Ollama/vLLM/internal via OpenAI-compatible `base_url` |
| NFR-LITL-004 | Traceability: each setup cites internal sources used | 100% of setups have citation | `[Future]` — roadmap Phase 4, not achieved |
| NFR-LITL-005 | Reproducibility/versioning: knowledge version never overwrites | Each update creates new version with full metadata | `[Future]` — no Versioning Service |
| NFR-LITL-006 | Reliability: retry/resume, idempotency durable across restart | Idempotency survives watcher restart | `[Future]` — currently only session-local `processed_loops` |
| NFR-LITL-007 | Performance/scalability: chunking/caching/resumable for large multimodal | Large ingest case (e.g., ~4 days) resumes after interruption | `[Future]` — roadmap Phase 4c |
| NFR-LITL-008 | Observability: structured log, metrics, health check, multi-user dashboard | TBD (no specific metrics yet) | `[Future]` — roadmap Phase 7; currently only basic warning logs |
| NFR-LITL-009 | Cost/token governance: budget/cost threshold, model routing by task | TBD (no real cost/token threshold yet) | `[Future]` — roadmap Phase 4b; currently only `LAB_AGENT_LOOP_MAX_ROUNDS` (round count, not cost) |
| NFR-LITL-010 | Accessibility/operability: CLI `once`/`watch`, clear `.env` config, troubleshooting docs | CLI runs with 2 commands, complete docs | `[MVP]` — `setup-and-operations.md` |

---

## 15. Security, safety, privacy, governance

| Trust boundary | Risk | Control | Status |
|---|---|---|---|
| Canvus credentials | Secret leak | Keep in `apps/canvus-mcp/.env`, git-ignored | `[MVP]` |
| MCP write tools | Unintended canvas mutation | Only orchestrator calls write tool; model receives only read tools | `[MVP]` |
| Downloaded PDF/image | Sensitive data | Write to `downloads/` directory ignored; bytes don't enter model context by default | `[MVP]` |
| Model output | Domain fact hallucination | Ground with RagCluster; flag ambiguous term; schema is structured | `[MVP partial]` |
| Loop autonomy | Runaway execution | Model stop decision + `LAB_AGENT_LOOP_MAX_ROUNDS` backstop | `[MVP partial]` |
| Wet-lab authorization | Unapproved experiment execution | 5-step canonical chain (below) + Gate 1-5 | `[Future]` — no gate code |

**Mandatory wet-lab authorization chain (canonical, consistent across README/system-architecture/roadmap):**

```text
AI design → in-silico validation → scientist review → lab lead approval → wet lab
```

**Comparison with 5-gate vision (§12):** Original vision meeting describes Gate 1 (scientist approves design) before in-silico, while canonical authorization chain places scientist review after in-silico results. This document standardizes as follows: an optional **preliminary design screening** before in-silico allows revise/reject of design; it **does not authorize wet lab**. Mandatory scientist review occurs after in-silico, followed by lab-lead approval. This standardization requires owner confirmation (see §18).

Post-silico review state is also missing from current `DecisionState`; this is a gap in state model, not evidence gates are implemented.

**Privacy/data governance:** Sensitive data is not sent to external model provider per data-locality requirement `[Future]`. No PII-handling specifically specified in original vision.

---

## 16. Traceability matrix

| Vision heading | Related UC/FR | Current implementation marker |
|---|---|---|
| §4 (Design next experiment) | UC-LITL-01, FR-LITL-002 | Note `{idea: ...}`, `RAGCluster_` widget, `ideas_needing_setup` (`canvus_mcp/tools/experiments.py`) |
| §5 (Close the loop) | UC-LITL-02, FR-LITL-001…013 | `lab_agent/orchestrator.py`, `lab_agent/watch.py` |
| §7 (Node types) | §11.3 mapping table | Full mapping table in vision §7 lines 278-292 |
| §8 (In silico) | UC-LITL-03, FR-LITL-003 | No module — 100% Future |
| §9.1 (Knowledge Retrieval) | FR-LITL-001 | `canvus_mcp/ragcluster.py` (RagCluster graph only) |
| §9.4 (Flywheel) | FR-LITL-008, FR-LITL-017 | None — distinct from `{exp:}` integration in `integrations/canvus-serving-experiment-prepare/`, unrelated to Flywheel |
| §10 (Model-agnostic) | FR-LITL-014, NFR-LITL-003 | `lab_agent/adapters/factory.py`, `openai_adapter.py`, `claude_adapter.py` |
| §12 (Gates/decision states) | §11 Workflow state model | `lab_agent/models/states.py` (enum has all 9, 6/9 lack transition) |
| §13 (Error cases) | Exception flows UC-LITL-02 | `experiment-workflow.md` § "Failure handling" |
| §14 (MVP acceptance criteria) | AC-UC-LITL-02-001…006 | §10.1 Acceptance criteria |

---

## 17. Release / implementation coverage matrix

| Phase (roadmap) | Content | Status | Related UC/FR |
|---|---|---|---|
| Phase 0 | Repository extraction | Files extracted; not yet committed | — |
| Phase 1 | Local verification (test/lint/mypy) | Pending | — |
| Phase 2 | Demo canvas operation (end-to-end mock loop) | Pending | UC-LITL-02 happy path |
| Phase 3 | Harness contracts, persistent loop state | Future | FR-LITL-019, NFR-LITL-006 |
| Phase 4 | Stronger grounding (wiki/KG/acronym) | Future | FR-LITL-001, FR-LITL-016, NFR-LITL-004 |
| Phase 4b | Token/resource governance, model routing | Future | FR-LITL-013, NFR-LITL-002, NFR-LITL-009 |
| Phase 4c | Async multimodal ingestion | Future | FR-LITL-020, NFR-LITL-007 |
| Phase 5 | In-silico validation gate | Future | UC-LITL-03, FR-LITL-003 |
| Phase 6 | In-silico + human approval + Flywheel + lab integration | Future | FR-LITL-004, FR-LITL-005, FR-LITL-006, FR-LITL-008, FR-LITL-010, BR-LITL-006, BR-LITL-009 |
| Phase 7 | Production hardening, multi-user, observability | Future | NFR-LITL-006, NFR-LITL-008, ACT-LITL-13 |

**MVP acceptance (vision §14) — status mapping:**

| MVP | Content | Status |
|---|---|---|
| MVP 1 — Text-only simulation | Idea → setup → mock approve → mock result → interpret → update version → suggest next | `[MVP]` core exists with code path; local/E2E verification pending; "update knowledge version" still `[Future]` |
| MVP 2 — Flywheel-connected demo | Setup connects Flywheel Data Node → job runs/mock → Analysis output → Result Interpretation → Knowledge Update versioned → Next Experiment | `[Future]` not achieved — no implementation evidence |
| MVP 3 — In silico gate | AI design → digital twin/simulation → predicted outcome + uncertainty → proceed/revise/reject → proceed only if human approves | `[Future]` not achieved |

---

## 18. Unresolved decisions

1. Should `ExperimentSetup` schema (`lab_agent/models/experiment.py`) add fields to match all 10 items in vision §4 Output (e.g., `hypothesis`, `success_criteria`, `constraints.budget_limit`, `available_platforms`)? Planner/implementer should cross-check field-level when detailed design planning begins.
2. Actor "Administrator/auditor" (ACT-LITL-13) is inferred from §15 Audit Log + roadmap Phase 7, not explicitly named in original vision. Owner should confirm whether this is a real human actor or just an implementation artifact (audit log) requiring no separate actor.
3. Target-harness architectural direction (`system-architecture.md` § "Target harness boundary", roadmap Phase 3-7) is **`[Proposed]`, not yet ratified by project owner**. This document describes it as part of target vision but does not treat it as approved.
4. Original vision places a scientist design-approval gate before in-silico, while canonical authorization chain places scientist review after in-silico. This spec treats pre-silico step as optional preliminary screening not authorizing wet lab and post-silico review as mandatory gate; owner should confirm this standardization before designing state machine/approval UI.
5. Vision §9.2 describes agent output with `confidence` number at `ExperimentSetup` level; actual schema currently has no such field. Decide whether to add when implementing Phase 4/5.

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
