# Research: Safe Execution & Integration Architecture

Scope: in-silico validation, scientist review, lab-lead approval, Flywheel/HPC analysis, real lab/robot execution, versioned knowledge update, observability, multi-user isolation. Research/planning only — no wet-lab action, no code/doc changes.

Date: 2026-07-16. Sources: repo docs (`lab-in-the-loop-use-case-specification.md`, `system-architecture.md`, `development-roadmap.md`, `experiment-workflow.md`, scout report) + code (`states.py`, `orchestrator.py`, `nodes.py`, `experiments.py`, `adapters/base.py`, `adapters/factory.py`, `orchestrator_support.py`, `models/experiment.py`, `config.py`) + 5 web searches (Temporal/AWS human-approval patterns, hexagonal architecture, 21 CFR Part 11, saga/compensation, multi-tenant orchestration). Full URLs in Sources.

## Summary

Recommend: (1) keep the canonical gate order already converged on in 3 independent repo docs — `AI design → in-silico → scientist review → lab-lead approval → wet lab` (BR-LITL-009) — with the vision's pre-silico gate reinterpreted as an **optional, non-authorizing** design screen; (2) implement the state machine as **coarse `DecisionState` + a separate durable, append-only `GateApproval` evidence log**, not an enum-only model and not an external workflow engine (Temporal/Step Functions) — the project's scale (one canvas-poll process, no message broker) doesn't justify that infra yet, but the evidence-log shape is compatible with migrating to one later; (3) model in-silico/Flywehel/lab/knowledge-versioning integrations as `Protocol` adapters exactly like the existing `ModelAdapter` (`adapters/base.py` + `factory.py`), each with a `Mock*Adapter` for tests until real endpoints exist; (4) fix `orchestrator_support.coerce()` — it currently defensively fills missing required fields (bool→`False`, str→`""`), which is fine for `LoopDecision.proceed` but unsafe for any new gate/decision schema and must fail closed there; (5) partition all new durable state (idempotency, evidence, leases) by `canvas_id` as the tenant key, matching Phase 7's "per-canvas isolation" language and the existing `canvas_id`-threaded call signatures.

## 1. Canonical approval ordering

Three repo docs (`system-architecture.md` "Target harness boundary", `development-roadmap.md` Phase 6, `lab-in-the-loop-use-case-specification.md` §15) independently state the same chain:

```text
AI design → in-silico validation → scientist review → lab-lead approval → wet lab
```

The **only** conflict is with the original meeting vision, which places a scientist gate *before* in-silico (§18.4 "unresolved decision #4" in the spec). Two ways to resolve, compared:

| Option | Description | Fit |
|---|---|---|
| A — Vision-literal | Scientist gate blocks entry to in-silico; no separate mandatory post-silico human gate | Contradicts BR-LITL-009 and 3 other docs; cheaper (1 human gate) but ships wet-lab authorization without a human ever seeing the simulation output — fails the "why it matters" rationale in UC-LITL-03 (wasted chemicals/animals/robot time) |
| B — Canonical (recommended) | Optional non-authorizing design screen before in-silico (fast, cheap reject of obviously bad designs) + mandatory scientist review of *simulation output* + mandatory lab-lead resource/budget/safety approval, in that order | Matches BR-LITL-009, UC-LITL-03 step 4-6, and NFR-LITL-008 traceability; costs one more state but each state maps 1:1 to an FR |

**Recommendation:** Option B. This is the standing majority position in-repo; treat as the default and flag for owner ratification per spec §18.4 (see Risks §11).

## 2. State-machine / gate design

Three implementation shapes, compared:

| | A. Enum-only (extend `DecisionState`, write more values) | B. Enum + durable evidence log (recommended) | C. External workflow engine (Temporal / AWS Step Functions) |
|---|---|---|---|
| Durability | Canvas note text only — matches current pattern | Canvas note (human-readable) + local durable store (evidence, queryable without graph re-scan) | Full durable execution history, built-in |
| Auditability | Weak — no structured actor/reason/hash per decision | Strong — one `GateApproval` record per gate with actor, reason, proposal hash | Strong, but coupled to engine's own history format |
| New infra | None | None (SQLite/local file, same tier as Phase 3's proposed idempotency store) | New service dependency (self-hosted Temporal or AWS account), new deploy/ops surface |
| Fits current stack | Yes | Yes — mirrors existing `ModelAdapter` Protocol pattern and Pydantic conventions | No — current orchestrator is a plain async Python loop (`orchestrator.py`), not a deterministic-workflow-code model |
| Migration cost later | Low | Low — evidence log's shape (proposal hash, gate id, decision, actor, timestamp) is exactly what Temporal/Step-Functions-style approval records look like, so B is a strict subset of C's data model | N/A (destination) |
| Team/ops cost now | Low | Low | High — new runtime to operate, learn, secure |

**Recommendation:** B. YAGNI against C at current scale (single watcher process, no cross-team execution fleet yet); KISS keeps the durable bit to "one evidence record per gate decision," not a new orchestration runtime. Revisit C only if Phase 7 multi-user scale or cross-process execution (e.g. a separate robot-scheduling service) makes in-process polling insufficient — the AWS/Temporal research below (§3, §5) still directly informs option B's design even without adopting the engines themselves ([Temporal AI patterns](https://go.temporal.io/platform-hub/ai-engineering/ai-patterns), [AWS Step Functions human-approval tutorial](https://docs.aws.amazon.com/step-functions/latest/dg/tutorial-human-approval.html)).

### Revised `DecisionState` sequence (supersedes current 9-value enum)

```text
DRAFT
  → NEEDS_DESIGN_SCREENING (optional, config-skippable, non-authorizing)
  → IN_SILICO_PENDING → IN_SILICO_COMPLETE
  → NEEDS_SCIENTIST_REVIEW → NEEDS_LAB_LEAD_APPROVAL → APPROVED_FOR_WET_LAB
  → RUNNING
  → ANALYSIS_PENDING → ANALYSIS_COMPLETE
  → KNOWLEDGE_UPDATE_PENDING
  → CLOSED | REJECTED   (REJECTED reachable from any gate; carries which gate + reason)
```

Rationale for the delta vs. current `states.py`: current `NEEDS_REVIEW`/`APPROVED_FOR_IN_SILICO` is ambiguous about ordering (spec §11.2 flags this as the concrete evidence of the gap); `IN_SILICO_COMPLETE` and a post-silico review state don't exist yet (spec explicitly calls this out). Because only 3/9 current values have transition logic and zero production canvases exist, this is the correct moment for a clean revision rather than an additive patch — no live data to migrate.

`DecisionState` stays a coarse, human-readable label rendered into the note body (same as today, `nodes.create_node(..., state=...)`). It answers "what stage is this note in," not "who approved what and why" — that's the evidence log's job (§4). This is the DRY split: don't duplicate approval semantics between the enum and the log.

## 3. Typed adapter contracts (ports & adapters)

Mirror the existing `ModelAdapter` shape exactly: `Protocol` + `runtime_checkable`, Pydantic/dataclass I/O types, `factory.py`-style selection by a `Literal` settings field, real network/SDK code only inside concrete adapters. This is the same shape the [hexagonal-architecture research](https://docs.aws.amazon.com/prescriptive-guidance/latest/hexagonal-architectures/overview.html) recommends: ports named for business capability, not vendor concepts; adapter selection at the composition root (`factory.py`), never a conditional in orchestrator logic ([Cockburn, "Hexagonal architecture"](https://alistair.cockburn.us/hexagonal-architecture)).

```python
# lab_agent/adapters/in_silico.py
class InSilicoResult(BaseModel):
    predicted_outcome: str
    confidence: float = Field(ge=0, le=1)
    key_assumptions: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    recommended_changes: list[str] = Field(default_factory=list)
    decision: Literal["proceed", "revise_before_wet_lab", "reject"]

@runtime_checkable
class InSilicoAdapter(Protocol):
    async def simulate(self, setup: ExperimentSetup, *, timeout_s: float) -> InSilicoResult: ...
```

```python
# lab_agent/adapters/flywheel.py  (Flywheel / HPC analysis)
class JobHandle(BaseModel):
    job_id: str
    submitted_at: str

class JobStatus(BaseModel):
    job_id: str
    state: Literal["queued", "running", "succeeded", "failed"]
    detail: str = ""

class AnalysisResult(BaseModel):
    job_id: str
    metrics: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)   # link back to canvus_mcp downloads.py convention

@runtime_checkable
class FlywheelAdapter(Protocol):
    async def submit_job(self, result_or_setup: ExperimentResult, *, pipeline: str) -> JobHandle: ...
    async def get_status(self, handle: JobHandle) -> JobStatus: ...
    async def fetch_analysis(self, handle: JobHandle) -> AnalysisResult: ...
    async def rerun(self, handle: JobHandle) -> JobHandle: ...   # FR-LITL-017: allow rerun on failure
```

```python
# lab_agent/adapters/lab_execution.py  (robot / wet lab)
class DryRunReport(BaseModel):
    feasible: bool
    warnings: list[str] = Field(default_factory=list)
    estimated_resource_use: list[str] = Field(default_factory=list)

class ExecutionHandle(BaseModel):
    execution_id: str
    dispatched_at: str

class ExecutionStatus(BaseModel):
    execution_id: str
    state: Literal["queued", "running", "completed", "failed", "aborted"]
    detail: str = ""

@runtime_checkable
class LabExecutionAdapter(Protocol):
    async def dry_run(self, setup: ExperimentSetup) -> DryRunReport: ...
    async def dispatch(self, setup: ExperimentSetup, *, authorization: GateApproval) -> ExecutionHandle: ...
    async def get_status(self, handle: ExecutionHandle) -> ExecutionStatus: ...
    async def abort(self, handle: ExecutionHandle, *, reason: str) -> ExecutionStatus: ...
```

```python
# lab_agent/adapters/knowledge.py  (versioned KB write)
class VersionRecord(BaseModel):
    version_id: str
    source_experiment_id: str
    created_at: str

class ConflictNote(BaseModel):
    conflicting_version_id: str
    summary: str

@runtime_checkable
class KnowledgeAdapter(Protocol):
    async def get_conflicts(self, entry: "KnowledgeEntry") -> list[ConflictNote]: ...
    async def write_version(self, entry: "KnowledgeEntry", *, source_experiment_id: str) -> VersionRecord: ...
```

Selection mirrors `adapters/factory.py::get_adapter`: add `in_silico_provider: Literal["mock", "..."]`, `flywheel_provider`, `lab_execution_provider`, `knowledge_provider` fields to `Settings` (each defaulting to `"mock"`), one `get_*_adapter(settings)` factory function per port, real implementations imported lazily inside the branch (same lazy-import style already used for `OpenAIAdapter`/`ClaudeAdapter`). External implementations stay pluggable and untouched by orchestrator code — orchestrator only calls the Protocol.

## 4. Durable authorization evidence

Add one `GateApproval` record per gate decision, independent of and mirrored from the canvas note text:

```python
class GateApproval(BaseModel):
    gate: Literal["design_screening", "in_silico", "scientist_review", "lab_lead_approval"]
    subject_id: str            # setup/result widget id
    canvas_id: str             # tenant key, see §7
    decision: Literal["approve", "revise", "reject"]
    actor_id: str
    actor_role: Literal["scientist", "lab_lead", "system"]
    reason: str
    proposal_hash: str         # sha256 of the ExperimentSetup content this decision was made against
    decided_at: str
    expires_at: str | None = None
```

`proposal_hash` closes the "approve then quietly modify" gap the AWS agentic-approval research flags explicitly: validate the hash immediately before execution so a stale approval can't authorize a since-edited setup ([AWS Agentic AI Well-Architected, approval guidance](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)). Store append-only in the same local durable store proposed for Phase 3 idempotency (SQLite, keyed by `(canvas_id, subject_id, gate)`); never delete or overwrite a row, only append newer decisions for the same key — this gives NFR-LITL-004/005 traceability without inventing a second schema-of-record. Regulatory-grade e-signature (21 CFR Part 11: unique non-reusable signer id, two-factor, human-readable printed signature meaning) is a heavier, distinct requirement — see Risk #8; `GateApproval` as specified satisfies audit-trail intent (`actor_id`, timestamp, reason, before/after via `proposal_hash`) but not full e-signature identity assurance ([21 CFR Part 11](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11), [FDA data-integrity guidance](https://www.fda.gov/media/119570/download)).

**Fail-closed requirement:** `orchestrator_support.coerce()` currently fills missing required fields defensively (`bool → False`, `str → ""`). Confirmed unsafe if reused for `GateApproval`/`InSilicoResult`/`LoopDecision`-class schemas — a malformed `decision` field must not silently coerce to `""`/first-enum-value. Recommend a second, strict `coerce_or_fail()` used for every gate/decision/approval schema (raise, leave note `Status:` at a `*_FAILED`/pending state, let the next watcher cycle retry) — matches the doc's own "fail-visible/leave-pending policy," which `coerce()`'s current default-fill behavior already contradicts per the scout report (finding #3).

## 5. Dry-run / abort / rollback semantics

Physical wet-lab actions are not transactionally reversible — the research is explicit that **cancellation ≠ rollback ≠ compensation**, and compensation is domain-specific, can itself fail, and does not restore exact prior state ([Azure compensating-transaction pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction); [Garcia-Molina & Salem, "Sagas," 1987](https://doi.org/10.1145/38713.38742)). Apply per adapter:

- **`dry_run()`** (`LabExecutionAdapter`, and effectively `InSilicoAdapter.simulate()` itself acts as a dry run relative to wet lab): validate feasibility, no side effects, no dispatch. Every `dispatch()` call must be preceded by a passing `dry_run()` in the same round — enforced by orchestrator, not by trusting the caller.
- **`abort()`**: best-effort cancellation of in-flight/queued work only. Once `ExecutionStatus.state == "running"` on a *physical* action, abort semantics degrade to "stop what can still be stopped" — must not be presented to users as "undo."
- **Compensation** applies only to reversible resource commitments (budget hold, instrument booking, reagent reservation) — not to completed physical steps. Compensating actions must themselves be idempotent/retryable and log a terminal "manual intervention required" state on failure, per the Azure pattern's explicit warning that compensation can fail ([Azure pattern doc](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction)).
- **Flywheel/HPC jobs** are the one adapter where true cancel-and-rerun is safe (`FlywheelAdapter.rerun`, FR-LITL-017) — no physical irreversibility.

## 6. Audit/event semantics & conflict handling

- Every orchestrator write already funnels through `nodes.py` (`create_node`/`connect`) — extend it to also emit a structured event (gate transitioned, adapter called, decision recorded) to the same durable store as `GateApproval`, tagged with `canvas_id`, `round`, `gate`, `actor`.
- **Provenance ambiguity (scout finding #1):** orchestrator-generated `result → setup` edges are structurally identical to user-authored loop-restart edges. Recommend tagging orchestrator-created notes/connectors with a machine-readable `GeneratedBy: lab-agent vX round N` marker in the body (same pattern already used for `Status:` lines) so `canvus_mcp/experiments.py` classification and any future audit query can distinguish system-generated advance-edges from user-intent edges. Resolves spec's open question #4 ("How to distinguish user-authored loop triggers from orchestrator-generated round edges").
- **Conflict handling (FR-LITL-018):** `KnowledgeAdapter.get_conflicts()` runs before `write_version()`. Non-empty conflicts → create a `[KB:Conflict]` note referencing both hypotheses (vision: "keep both hypotheses," don't silently overwrite) and hold `DecisionState` at a review-needed value instead of auto-advancing to `CLOSED`.
- **Concurrent access:** two watcher instances or a user editing mid-cycle on the same canvas is a real hazard once durable per-canvas state exists. Recommend a lightweight lease row (`canvas_id`, `holder_id`, `expires_at`) in the same store — a watcher renews its lease each poll cycle; a second watcher for the same `canvas_id` refuses to write while a live lease is held. Single-writer-per-canvas is the cheapest correct invariant given the current one-process-polls-all-canvases design.

## 7. Multi-user / canvas isolation

Research on multi-tenant orchestrators converges on: resolve tenant from a trusted source, thread it through every durable key, isolate credentials per tenant, partition workers/queues, fail closed on missing tenant context ([AWS SaaS tenant-isolation whitepaper](https://docs.aws.amazon.com/whitepapers/latest/saas-architecture-fundamentals/tenant-isolation.html); [Camunda multi-tenancy docs](https://docs.camunda.io/docs/components/concepts/multi-tenancy/)). Applied to this codebase, `canvas_id` is already the natural tenant key (every orchestrator function already takes `canvas_id` as a parameter):

- **Durable state keys:** every new store row (`processed_loops`, `GateApproval`, event log, lease) must be `(canvas_id, ...)` composite-keyed. Current `processed_loops` is a single in-memory `set` in `watch.py` — must become per-`canvas_id` before any multi-canvas concurrency is safe (today's session-local, single-canvas assumption is fine only because one watcher handles one canvas per run).
- **Credentials:** today one shared `canvus-mcp` `.env` keyset serves the whole server (NFR-LITL-001-compliant for a single tenant, insufficient for Phase 7). If multiple Canvus workspaces/orgs are in play, credential resolution needs a broker keyed by `canvas_id`/user, never a client-supplied override — matches the "pooled vs. siloed" isolation-tier framing from the research; recommend starting pooled (one canvus-mcp, tenant-partitioned data) and only moving to per-tenant `canvus-mcp` deployments if a specific tenant needs stronger isolation (e.g. data-locality per NFR-LITL-002).
- **Fail-closed invariant:** add a guard in `nodes.py` write helpers asserting the `canvas_id` passed matches the loop/task's bound `canvas_id` — cheap, catches accidental cross-canvas writes from a copy-paste bug before it becomes a real leak.
- **Model-adapter and external-adapter credentials** follow the same rule already in place for Canvus creds (BR-LITL-001): stay orchestrator-side, never enter model context, never get logged.

## 8. Observability boundaries

- Already-present `structlog` calls (e.g. `orchestrator.py`'s `log.info("experiment_loop_stopped", canvas_id=..., rounds=..., reason=...)`) are the right pattern — extend every new log line to carry `canvas_id`, `round`, `gate`, `adapter_name` as structured fields, not string interpolation.
- Metrics (NFR-LITL-008): scans, setups/results created, gate decisions (by outcome), adapter call latency/error counts, backstop triggers — a lightweight counter/histogram module is sufficient; **don't** adopt full OpenTelemetry tracing yet (YAGNI at current single-process scale). Revisit once real Flywheel/HPC/robot adapters cross a network boundary into separately-operated services (Phase 6+), where distributed tracing earns its keep.
- Health checks: MCP reachability already implicit in watcher failure logs; add explicit `/health`-style checks per adapter (in-silico, Flywheel, lab, knowledge) once real endpoints exist, so a down external system degrades to "pending" rather than silently stalling the loop.
- Per-canvas dashboards (Phase 7 "per-canvas loop progress across users") fall directly out of `canvas_id`-tagged logs/metrics — no separate observability data model needed if §7's tenant-key discipline is followed (DRY: one partition key for isolation and for dashboards).

## 9. FR/NFR/BR/UC → recommendation → files

| ID(s) | Recommendation | Files |
|---|---|---|
| UC-LITL-03, FR-LITL-003, AC-UC-LITL-03-* | `InSilicoAdapter` Protocol + `InSilicoResult` model + gate states | new `adapters/in_silico.py`; `models/states.py` |
| FR-LITL-004, BR-LITL-009 | `NEEDS_SCIENTIST_REVIEW` gate + `GateApproval(gate="scientist_review")` | `models/states.py`; new `models/gates.py`; `orchestrator.py` |
| FR-LITL-005, BR-LITL-009 | `NEEDS_LAB_LEAD_APPROVAL` gate + budget/resource fields on `ExperimentSetup` (closes §13.4 gap) | `models/experiment.py`; `models/gates.py`; `orchestrator.py` |
| FR-LITL-006, ACT-LITL-11 | `LabExecutionAdapter` Protocol, dry-run-before-dispatch enforced by orchestrator | new `adapters/lab_execution.py`; `orchestrator.py` |
| FR-LITL-008, FR-LITL-017, ACT-LITL-12 | `FlywheelAdapter` Protocol incl. `rerun()` | new `adapters/flywheel.py` |
| FR-LITL-010, BR-LITL-008, NFR-LITL-005 | `KnowledgeAdapter` Protocol, append-only `VersionRecord` | new `adapters/knowledge.py` |
| FR-LITL-018 | `get_conflicts()` before `write_version()`, `[KB:Conflict]` note | `adapters/knowledge.py`; `canvus_mcp/experiments.py` (new marker) |
| FR-LITL-013, NFR-LITL-009 | budget/token/round stop policies beyond `LAB_AGENT_LOOP_MAX_ROUNDS` | `config.py`; `orchestrator.py` |
| FR-LITL-019, NFR-LITL-006 | durable per-canvas idempotency store, lease for single-writer | new `state_store.py`; `watch.py` |
| NFR-LITL-004 | `proposal_hash` + `GateApproval` log | new `models/gates.py`; `state_store.py` |
| NFR-LITL-008, ACT-LITL-13 | structured metrics/health checks, `canvas_id`-tagged logs | `watch.py`, `orchestrator.py`, new `metrics.py` |
| Phase 7 multi-user isolation | `canvas_id`-partitioned keys everywhere; per-canvas lease | `state_store.py`; `watch.py`; `nodes.py` guard |
| §11.3 new node types (Human Review, In Silico, Flywheel Data, Analysis Gear, Knowledge Update) | extend marker classification | `canvus_mcp/canvus_mcp/experiments.py` (`ExpMarkers`); `canvus_mcp/tools/experiments.py` (`scan_experiment_workflow` output fields) |
| scout finding #1 (edge provenance) | `GeneratedBy:` note-body tag | `nodes.py`, `render.py` |
| scout finding #3 (`coerce` fail-open) | `coerce_or_fail()` for decision/gate/approval schemas | `orchestrator_support.py` |

## 10. Incremental milestones (mock/contract-test substitutes)

- **M1 (~Phase 3/5):** Add all 4 `Protocol`s + result models + `Mock*Adapter` (deterministic, in-memory, matches `MockInSilicoAdapter` etc. style of existing test fakes for MCP/model). Add a contract-test suite parametrized over adapters, run against mocks now; real adapters get the same suite once endpoints exist (skip/xfail until then). Wire mocks into `orchestrator.py` behind the revised `DecisionState` chain so the full 12-13-state lifecycle is exercisable end-to-end with zero external dependencies — directly satisfies the "or mock" framing already in AC-UC-LITL-02-005/006.
- **M2 (~Phase 5):** `GateApproval` evidence log + `proposal_hash` + `coerce_or_fail()` for gate schemas; approve/reject surface reuses the existing connector-as-signal convention (e.g. a scientist/lab-lead creates a specific note/connector pattern the watcher recognizes) since no separate UI exists yet.
- **M3 (~Phase 6a):** Swap in first real adapter — recommend **Flywheel/HPC first** (network job-submission API, no physical safety risk) over the robot adapter. Verify via the same contract-test suite plus a small number of real-endpoint integration tests.
- **M4 (~Phase 6b):** `LabExecutionAdapter` real implementation stays **feature-flagged off by default**; enabling requires explicit owner/safety sign-off (see Risk #3). `KnowledgeAdapter` real implementation + conflict handling.
- **M5 (~Phase 7):** `canvas_id`-partitioned durable store for idempotency/leases/evidence; metrics/health checks/dashboards; multi-canvas concurrent watcher support.

## 11. Safety/security risks & owner decisions

1. **Gate-ordering conflict** (vision pre-silico vs. canonical post-silico scientist review) — this report recommends canonical (Option B, §1) as default; still needs explicit owner ratification per spec §18.4.
2. **Harness-first architecture not ratified** — building the adapters as `Protocol`s inside `lab-agent` now is low-risk either way; ports don't care which process later hosts them, so this recommendation is reversible regardless of that decision.
3. **Real robot/wet-lab dispatch is the highest-severity risk** — irreversible physical/chemical/biological action. Require: (a) `dry_run()` pass, (b) both mandatory `GateApproval` records present and hash-valid, (c) a distinct human-triggered "go" action separate from the two approval gates (defense in depth — approval authorizes, a separate explicit action dispatches), (d) feature flag default-off until owner/safety sign-off.
4. **`coerce()` fail-open behavior** (scout finding #3) is a live bug risk for any new decision/gate schema — must not ship gate logic on top of the current defensive-fill `coerce()` unmodified.
5. **New adapter credentials** (Flywheel/HPC, robot API, knowledge store) need the same trust-boundary treatment as existing Canvus creds — env-only, git-ignored, orchestrator-only, never in model context (BR-LITL-001 extended).
6. **Budget/resource governance gap** — `ExperimentSetup` has no `budget`/`resource_requirements` field today (§13.4 gap); lab-lead approval gate has nothing concrete to approve against without it. Recommend adding these fields in the same change that adds the gate.
7. **Multi-tenant credential isolation for `canvus-mcp` itself** — one shared keyset today; Phase 7 needs an owner decision on pooled-vs-siloed tier (§7) before real multi-canvas rollout.
8. **21 CFR Part 11 / GxP e-signature** — not currently mandated in the spec but GSK/pharma context and the "Compliance/security" stakeholder row make it plausible. `GateApproval` as designed gives audit-trail-grade evidence but not full e-signature identity assurance (unique non-reusable id, 2-factor, non-repudiation). Owner should decide MVP bar now — building the evidence log cleanly now doesn't block a later upgrade path, but retrofitting identity assurance after the fact is costlier.

## Unresolved questions

- Confirm Option B gate ordering (§1) — blocks finalizing `DecisionState` naming.
- Which adapter goes first in M3 — this report assumes Flywheel/HPC (lower physical risk) but confirm with owner/team capacity.
- Exact durable-store technology for §4/§6/§7 (SQLite vs. canvas-note-embedded metadata) — Phase 3 doc already lists this as an open option; this report assumes SQLite for query-ability but doesn't mandate it.
- Whether `canvus-mcp` itself needs per-tenant deployment now or can defer to Phase 7 (§7, Risk #7) — depends on how many concurrent tenants are actually expected near-term.

## Sources

- [Temporal — AI/human-in-the-loop patterns](https://go.temporal.io/platform-hub/ai-engineering/ai-patterns)
- [Temporal — human-in-the-loop webinar](https://pages.temporal.io/webinar-essential-ingredients-for-humans)
- [AWS Step Functions — callback pattern / task token](https://docs.aws.amazon.com/step-functions/latest/dg/connect-to-resource.html)
- [AWS Step Functions — human approval tutorial](https://docs.aws.amazon.com/step-functions/latest/dg/tutorial-human-approval.html)
- [AWS Step Functions — ValidateStateMachineDefinition](https://docs.aws.amazon.com/step-functions/latest/apireference/API_ValidateStateMachineDefinition.html)
- [AWS Step Functions — TestState mocked dry run](https://docs.aws.amazon.com/step-functions/latest/dg/test-state-isolation.html)
- [AWS Well-Architected — Agentic AI approval guidance](https://docs.aws.amazon.com/wellarchitected/latest/agentic-ai-lens/agentsec04-bp02.html)
- [Cockburn — Hexagonal Architecture](https://alistair.cockburn.us/hexagonal-architecture)
- [AWS Prescriptive Guidance — Hexagonal architectures overview](https://docs.aws.amazon.com/prescriptive-guidance/latest/hexagonal-architectures/overview.html)
- [AWS Prescriptive Guidance — Hexagonal architecture cloud design pattern](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/hexagonal-architecture.html)
- [AWS Prescriptive Guidance — Hexagonal architectures best practices](https://docs.aws.amazon.com/prescriptive-guidance/latest/hexagonal-architectures/best-practices.html)
- [AWS Prescriptive Guidance — Hexagonal architectures examples](https://docs.aws.amazon.com/prescriptive-guidance/latest/hexagonal-architectures/examples.html)
- [FDA — Part 11 scope and application guidance](https://www.fda.gov/regulatory-information/search-fda-guidance-documents/part-11-electronic-records-electronic-signatures-scope-and-application)
- [eCFR — 21 CFR 211.194 (laboratory records)](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-C/part-211/subpart-J/section-211.194)
- [eCFR — 21 CFR 211.22 (quality unit authority)](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-C/part-211/subpart-B/section-211.22)
- [eCFR — 21 CFR Part 11 (electronic records/signatures)](https://www.ecfr.gov/current/title-21/chapter-I/subchapter-A/part-11)
- [FDA — Data integrity guidance for industry](https://www.fda.gov/media/119570/download)
- [Garcia-Molina & Salem — "Sagas" (ACM SIGMOD 1987)](https://doi.org/10.1145/38713.38742)
- [Azure Architecture Center — Compensating Transaction pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/compensating-transaction)
- [AWS Prescriptive Guidance — Saga pattern](https://docs.aws.amazon.com/prescriptive-guidance/latest/modernization-data-persistence/saga-pattern.html)
- [AWS Prescriptive Guidance — Saga orchestration pattern](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/saga-orchestration.html)
- [Microsoft Learn — Cloud-native distributed data / saga styles](https://learn.microsoft.com/en-us/dotnet/architecture/cloud-native/distributed-data)
- [Microsoft Learn — Cancellation vs. compensation vs. transactions](https://learn.microsoft.com/en-us/dotnet/framework/windows-workflow-foundation/modeling-cancellation-behavior-in-workflows)
- [AWS — SaaS architecture fundamentals: tenant isolation](https://docs.aws.amazon.com/whitepapers/latest/saas-architecture-fundamentals/tenant-isolation.html)
- [Microsoft Learn — Multitenant AKS workload identity guidance](https://learn.microsoft.com/en-us/azure/architecture/guide/multitenant/service/aks)
- [Temporal — API docs (namespaces)](https://api-docs.temporal.io/)
- [Camunda — Multi-tenancy concepts](https://docs.camunda.io/docs/components/concepts/multi-tenancy/)
