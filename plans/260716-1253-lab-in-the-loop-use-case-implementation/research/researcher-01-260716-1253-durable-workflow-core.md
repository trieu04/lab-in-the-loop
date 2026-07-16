# Research: Durable Workflow Core (Local Verification, Idempotency, Loop-Trigger Correctness, Fail-Visible Output, Shared Contracts)

Date: 2026-07-16. Scope: near-term foundation only (roadmap Phase 1–3). Does not cover in-silico/approval/Flywheel (Phase 4-6) — see companion report `researcher-02-260716-1253-grounding-governance-ingestion.md`.

## Summary

Current durable-idempotency gap (`FR-LITL-019` MVP, `[MVP]` session-local only; `NFR-LITL-006` `[Future]`) has two **distinct** root causes that the roadmap text conflates into one "SQLite state file" line item:

1. **No cross-restart durability** — `processed_loops` is a plain in-memory `set[str]` in `lab_agent/watch.py:91`, reset on every `once`/process restart.
2. **A correctness bug, not just a durability gap** — the orchestrator's own round-advance connector (`result_N → setup_N+1`, `lab_agent/orchestrator.py:159`) is graph-isomorphic to a user's loop-trigger connector (`result_N → setup_N`, same round). `canvus_mcp/experiments.py:detect_experiment_loops` cannot tell them apart; on the next scan it will re-detect the orchestrator's own edge as an unprocessed "loop" and call `run_loop` again with a **stale, mismatched result** (round-N result paired against round-(N+1) setup). Confirmed by static-trace of the code path; not caught by tests because `tests/fakes.py:FakeMCP` returns a fixed `workflow` snapshot and never re-derives it from created notes/connectors (scout report Critical Finding #1).

Recommendation: fix (2) first — it's a cheap, local, zero-infra graph-structure fix — then add a small SQLite ledger for (1) plus provenance/audit, without building the full `[Proposed]` harness state machine (that remains unratified — see `docs/system-architecture.md` § "Target harness boundary"). Pair with a fail-visible policy fix in `orchestrator_support.coerce` (currently the opposite of the documented policy in `docs/code-standards.md`).

---

## 1. Loop-trigger correctness — user edge vs. orchestrator-generated edge (acceptance criterion #2)

### Traced bug

`detect_experiment_loops` (`apps/canvus-mcp/canvus_mcp/experiments.py:100-130`) matches **any** connector where `src` is `[EXP:Result` and `dst` is `[EXP:Setup`, with no regard to round number or who drew it. Two edges satisfy this predicate:

- **User trigger** (documented happy path, `docs/experiment-workflow.md:71`): `[EXP:Result vNNN] → [EXP:Setup vNNN]` — **same round**.
- **Orchestrator round-advance edge** (`apps/lab-agent/lab_agent/orchestrator.py:159`, inside `run_loop`'s CONTINUE branch): `nodes.connect(mcp, canvas_id, result_id, next_setup_id)` — `result_id` is round N, `next_setup_id` is round **N+1**.

Both are `result→setup` connectors; `_round_of()` (already implemented, `canvus_mcp/experiments.py:71-73`) is the only signal that distinguishes them, and it is currently unused by the detector. On the watcher's next poll, the orchestrator's own edge appears in `snap["loops"]` as a brand-new, never-processed `loop_connector_id`. `run_loop` is invoked again with `setup_id=next_setup_id` (round N+1, real) but `result_id=result_id` (round N, stale) — the model decides against a mismatched pair, and depending on its answer this can create a duplicate `[EXP:Closed]` or a spurious extra round, silently corrupting the canvas graph. `tests/test_orchestrator.py` never exercises this because `FakeMCP.call_tool("scan_experiment_workflow", ...)` always returns the constructor's static `workflow` dict (`tests/fakes.py:48-49`) — it never re-derives from `self.notes`/`self.connectors` after `create_note`/`create_connector` calls, so a live re-scan is untested.

### Recommendation

Fix at the **graph-structure level**, no schema/infra change:

```
loop trigger (actionable) := connector where src=[EXP:Result], dst=[EXP:Setup], round(dst) <= round(src)
```

i.e., `detect_experiment_loops` (or a filter in `scan_workflow`) should only surface edges where the destination setup's round is **not newer** than the source result's round. User same-round triggers (`round(dst) == round(src)`) pass; orchestrator forward-advance edges (`round(dst) == round(src) + 1`) are excluded structurally. This is the cheapest fix (pure function, `_round_of` already exists) and needs no provenance tagging, no canvas metadata field, no new persisted store. Ship it as a standalone fix regardless of the SQLite-ledger decision below — it is a correctness bug independent of durability.

Residual gap after this fix: it only filters *forward* edges; it does not (and should not, per docs' "manual re-trigger" intent) block a user from manually re-connecting an *already-processed* same-round edge after a restart — that is a durability problem, handled in §2.

**Files touched by this fix:** `apps/canvus-mcp/canvus_mcp/experiments.py` (`detect_experiment_loops`), `apps/canvus-mcp/tests/test_experiments.py` (add a case with a forward `result1→setup2` edge to prove it's excluded), `docs/experiment-workflow.md` § "Result to setup loop" (document the round(dst) <= round(src) rule explicitly — it is currently implicit only in prose).

---

## 2. Durable idempotency — three approaches compared (acceptance criterion #1)

| Dimension | A: Graph-only inference | B: Canvas-marker/metadata | C: Local SQLite ledger |
|---|---|---|---|
| New infra | None | None | One file (`lab_agent/state.db`) |
| Cross-restart durability | Partial — works for "does downstream artifact exist" (idea→setup, setup→result already do this), fails for "was this exact connector id already reacted to" | Same failure mode as A **plus** no write surface exists to update a marker after creation (see below) | Full — `UNIQUE` constraint on `loop_connector_id`/idempotency key survives restart (SQLite docs, atomic `INSERT ... ON CONFLICT DO NOTHING`) |
| Audit trail (NFR-LITL-008) | None — no record of *when*/*why* a trigger fired | None | Natural fit — append-only event table |
| Consistency risk | None — canvas is sole source of truth | Low but see feasibility issue | Must not become a second competing state machine — must stay a ledger *about* canvas events, not a replica of canvas state |
| Feasibility check | N/A | **Blocked**: `apps/canvus-mcp/canvus_mcp/tools/widgets.py` exposes only `create_note`/`create_browser`/`create_image`/`create_connector` — no `update_note`/`update_connector` tool exists anywhere in `canvus_mcp/tools/*.py` (grep-verified). A Note's body can only carry a "processed" marker if written at creation time (already done via the `Status:` line, `lab_agent/nodes.py:64`); retrofitting a marker onto an *existing* note/connector needs a new canvus-mcp write tool — cross-app scope creep for what should be a lab-agent-local concern. Connector fields (`line_color`/`line_width`/`connector_type`/tip styles) are visual, not a metadata channel — repurposing them is fragile and a user's cosmetic edit could silently corrupt idempotency state. | N/A |
| Effort | ~0 (already partially exists) | Medium, and blocked without a new canvus-mcp tool | Small (single table, single module, matches single-writer/single-process shape of `lab-agent`) |
| Fits existing architecture | Yes — reinforces "canvas is durable source of truth" (`system-architecture.md`) | Conflicts with existing display/control-plane separation, plus tool gap above | Yes — `lab-agent` is already the sole write-caller (`lab_agent/nodes.py`); a local ledger just recording "which connector ids I created / already reacted to" is a natural extension of that same module, no new actor |

### Staged recommendation

**Stage 1 (now, zero infra):** §1's round-based structural filter. Ships alone; fixes the correctness bug.

**Stage 2 (Phase 3, small and local):** Add a SQLite ledger owned by `lab-agent` (new module, e.g. `apps/lab-agent/lab_agent/state.py`), single file, WAL mode, one writer (the watcher process):

```sql
CREATE TABLE processed_loops (
    loop_connector_id TEXT PRIMARY KEY,
    round             INTEGER NOT NULL,
    decision          TEXT,             -- 'continue' | 'stop' | 'backstop'
    processed_at      TEXT NOT NULL
);
CREATE TABLE orchestrator_edges (       -- provenance: connectors *this process* created
    connector_id  TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,        -- 'idea->setup' | 'result->setup(advance)' | 'robot->result' | 'result->closed'
    created_at    TEXT NOT NULL
);
```

`watch.py:process_once` reads `processed_loops` from this table instead of a bare `set[str]` on startup (one query, ~10 lines of change); `nodes.connect` (or its caller) inserts into `orchestrator_edges` whenever the orchestrator itself draws a connector, so `orchestrator_edges` gives a *second*, defense-in-depth signal for §1 (any connector id present in `orchestrator_edges` is never a user trigger, regardless of round arithmetic) — cheap belt-and-suspenders once the table exists anyway. Use `BEGIN IMMEDIATE` + `INSERT ... ON CONFLICT(loop_connector_id) DO NOTHING` for the atomic idempotent-claim pattern (see Stripe/SQLite sources below) — this also directly satisfies "no duplicate setup/result processing" (`BR-LITL-002`) with a real uniqueness constraint instead of a Python `if cid in processed_loops` check that races across concurrent polls only in theory today (single-process watcher) but is the right primitive going forward.

**Stage 3 (deferred, `[Proposed]`, requires owner ratification):** The full "independent harness/orchestrator" state machine — token/cost governance, multi-user isolation, retry/resume across arbitrary steps, policy gates — described in `docs/system-architecture.md` § "Target harness boundary (proposed)". **Do not build this now.** It is explicitly unratified in the docs and is a much larger surface (new service boundary, not a ledger). Building it prematurely violates YAGNI against an architecture direction the owner has not confirmed (`docs/lab-in-the-loop-use-case-specification.md` §18 item 3).

### Sources consulted (durable idempotency / SQLite)

- SQLite WAL mode documentation — https://www.sqlite.org/wal.html
- SQLite PRAGMA reference (`synchronous`, `busy_timeout`) — https://www.sqlite.org/pragma.html
- SQLite UPSERT (`ON CONFLICT`) — https://sqlite.org/lang_upsert.html
- SQLite transaction isolation — https://sqlite.org/isolation.html
- Python `sqlite3` module docs (connection/transaction control) — https://docs.python.org/3/library/sqlite3.html
- Stripe idempotent-requests API design — https://docs.stripe.com/api/idempotent_requests
- Stripe engineering blog on idempotency — https://stripe.com/blog/idempotency
- Temporal architecture docs (when durable-execution engines are justified vs. a local state machine) — https://docs.temporal.io/ and https://github.com/temporalio/temporal/blob/main/docs/architecture/README.md

These corroborate (not merely inform) the staged call: authoritative SQLite guidance describes exactly this single-process/single-writer/local-durability shape as the intended WAL use case; Stripe's pattern is the industry-reference shape for the `UNIQUE`-constraint idempotency-claim table above; the Temporal comparison confirms a full durable-execution engine is justified only once cross-service coordination, long external waits, and compensation logic exist — none of which apply to Lab-in-the-Loop's Phase 3 scope, reinforcing "do not build Stage 3 yet."

---

## 3. Fail-visible structured-output policy and retry behavior (acceptance criterion #3)

**Direct contradiction found:** `docs/code-standards.md` § "Structured output" states explicitly: *"If a provider returns invalid schema, fail visibly and leave the canvas state pending so a retry can happen safely."* The actual implementation does the opposite:

```python
# apps/lab-agent/lab_agent/orchestrator_support.py:11-20
def coerce(model_cls: type[BaseModel], parsed: dict[str, Any]) -> Any:
    """Validate model output, filling missing required fields defensively."""
    try:
        return model_cls.model_validate(parsed)
    except ValidationError:
        data = dict(parsed)
        for name, fld in model_cls.model_fields.items():
            if fld.is_required() and name not in data:
                data[name] = False if fld.annotation is bool else ""
        return model_cls.model_validate(data)
```

`coerce` silently fabricates required fields (`rationale=""`, `proceed=False`, `summary=""`) instead of raising, then the orchestrator writes a note built from this fabricated content — violating both the documented policy and `BR-LITL-003` (don't self-generate content without grounding) since an empty `rationale`/`summary` is worse than "no note at all": it looks like a legitimate, reviewed setup on the canvas.

### Recommended policy (aligns with the already-authoritative `code-standards.md`)

1. `coerce` raises (e.g. a small `SchemaValidationError`) instead of filling defaults, on any `ValidationError`.
2. Callers in `orchestrator.py` (`generate_setup`, `run_on_robot`, `_decide`) catch this at the call site and **do not write a node** — leave the canvas exactly as it was (no `[EXP:Setup]`/`[EXP:Result]`/`[EXP:Closed]` created), log a structured `structlog` warning with the raw parsed payload for diagnosis, and return control to the watcher loop cleanly (same "log and continue polling" shape `run_watch` already uses for `Exception` at `watch.py:96-97`).
3. Retry is then "free" and already correct: because no note was ever created, the next poll's `scan_experiment_workflow` still reports the same pending trigger (`ideas_needing_setup`/`setups_needing_run`/`loops`) and the same idea/setup/loop is retried on the next cycle — this is the existing documented behavior for "Model does not emit correct schema" in `docs/experiment-workflow.md` § "Failure handling" (*"current run fails; next cycle can retry pending canvas state"*) and in the use-case spec's exception table (`lab-in-the-loop-use-case-specification.md:260`). No new retry-count/backoff mechanism is required for this failure mode at this stage — the poll interval (`LAB_AGENT_WATCH_POLL_SECONDS`, default 30s) is the retry cadence, and `LAB_AGENT_LOOP_MAX_ROUNDS` is the only additional backstop already in place. Adding per-call retry/backoff/attempt-count now would be premature (YAGNI) unless a specific unbounded-retry failure mode is observed — flag but do not build.
4. Once the Stage-2 SQLite ledger exists (§2), an `audit_events` table (or extending `orchestrator_edges`) can log `schema_validation_failed` events with the raw payload for observability (`NFR-LITL-008`) — nice-to-have, not required to close this gap.

**Files touched:** `apps/lab-agent/lab_agent/orchestrator_support.py` (`coerce`), `apps/lab-agent/lab_agent/orchestrator.py` (3 call sites), `apps/lab-agent/tests/test_orchestrator.py` (new test: malformed model output → no note created, no connector drawn, `counts` for that step stays 0).

---

## 4. Shared contract ownership without over-engineering (acceptance criterion #4)

Scout report Critical Finding #4: "Workflow vocabulary and contracts are split between both apps and the optional serving integration; Phase 3 needs a single owner." Concretely split across:

- `apps/lab-agent/lab_agent/models/experiment.py` — `ExperimentSetup`/`ExperimentResult`/`LoopDecision` Pydantic models (the schema).
- `apps/lab-agent/lab_agent/prompts.py` — the system prompts that shape what the model emits for those schemas.
- `apps/canvus-mcp/canvus_mcp/experiments.py` — `ExpMarkers` (marker-string vocabulary: `RAGCluster_`, `Robot_`, `[EXP:Setup`, `[EXP:Result`, `{idea:`) — a **different but coupled** contract (canvas string vocabulary vs. Python schema).
- `integrations/canvus-serving-experiment-prepare/` — a third, independent consumer of a related-but-distinct `{exp: ...}` marker (explicitly *not* the same as `{idea: ...}`, per `docs/code-standards.md` line 38).

**Recommendation — smallest fix, not a new package:** do not create a third shared library/service (that would be over-engineering the "Proposed" harness prematurely). Instead:

1. **Schema ownership stays in `lab_agent/models/experiment.py`** — it is already the single Pydantic source; `canvus-mcp` does not import it (correctly decoupled, since `canvus-mcp` only deals in canvas markers/strings, never in Setup/Result/Decision objects). No change needed here beyond documenting the boundary explicitly in `docs/code-standards.md` (already partially done).
2. **Marker-vocabulary ownership stays in `canvus_mcp/experiments.py:ExpMarkers`** — it is already a single `@dataclass(frozen=True)`; the risk is `lab_agent/nodes.py` (`EXP_SETUP = "[EXP:Setup"`, `EXP_RESULT = "[EXP:Result"`, `CLOSED = "[EXP:Closed]"`) hand-duplicates the same string constants independently. **This is the one real DRY violation worth fixing now**: extract these three literal markers into a tiny shared constant (simplest option: `canvus_mcp.experiments.ExpMarkers` is already importable by `lab-agent` if it depends on `canvus_mcp` as a library, or — if the two apps must stay dependency-independent — duplicate is acceptable **only if** a test in each app asserts the strings match (a "constants must agree" cross-app test), which is far cheaper than introducing a new shared package. Check first whether `apps/lab-agent` already has a dependency edge on `canvus_mcp`/`canvus-sdk` (it does, indirectly, via `canvus-mcp` being the MCP server it talks to over HTTP — but not as a Python import) before deciding; if no import dependency exists today, keep it that way (network boundary is intentional) and add the cross-app constants-parity test instead of introducing a coupling.
3. **`{exp:}` serving integration stays explicitly out-of-scope** — already correctly documented as a separate marker/action; no shared-contract work needed there per current scope statement (`docs/lab-in-the-loop-use-case-specification.md` § "Out-of-scope").
4. Defer a formal shared "contracts package" (e.g. a `lab-in-the-loop-contracts` pip-installable module) until/unless a third consumer beyond `lab-agent`/`canvus-mcp` needs the Pydantic schemas directly (currently none does) — YAGNI.

---

## 5. FR/NFR/BR → recommendation → file mapping

| ID | Requirement | Recommendation (this report) | File(s) |
|---|---|---|---|
| FR-LITL-019 | Idempotency: no duplicate setup/result/loop processing | Stage 1 round-filter (§1) + Stage 2 SQLite ledger (§2) | `apps/canvus-mcp/canvus_mcp/experiments.py`; new `apps/lab-agent/lab_agent/state.py`; `apps/lab-agent/lab_agent/watch.py` |
| BR-LITL-002 | Idea/setup/loop processed only once | Same as above; atomic `INSERT ... ON CONFLICT DO NOTHING` replaces `if cid in processed_loops` | `apps/lab-agent/lab_agent/watch.py:70-75` |
| NFR-LITL-006 | Reliability: retry/resume, durable idempotency across restart | Stage 2 SQLite ledger | `apps/lab-agent/lab_agent/state.py` (new), `watch.py` |
| NFR-LITL-008 | Observability: structured logs/audit | `audit_events`/`orchestrator_edges` tables (byproduct of §2), keep using existing `structlog` calls | `apps/lab-agent/lab_agent/state.py` (new) |
| BR-LITL-003 | Don't self-generate ungrounded content | Fail-visible `coerce` fix (§3) — stop fabricating empty required fields | `apps/lab-agent/lab_agent/orchestrator_support.py`, `orchestrator.py` |
| "Model does not emit correct schema" exception (UC §10.1) | Leave canvas pending, retry next cycle | Same fix, relies on existing poll cadence, no new retry/backoff needed | `apps/lab-agent/lab_agent/orchestrator.py` |
| Roadmap Phase 3 "single documented contract governs schemas/prompts/policy" | Keep schema/marker ownership split as-is (already correctly separated); fix the one duplicated-constant DRY gap with a parity test, not a new package | `apps/lab-agent/lab_agent/nodes.py` (constants), `apps/canvus-mcp/canvus_mcp/experiments.py:ExpMarkers` |
| Roadmap Phase 1 "Local verification" | Out of this report's scope but blocking prerequisite — `uv sync`/`pytest`/`ruff`/`mypy` in both apps have not been run in this repo yet (Status: Pending) | `apps/canvus-mcp/`, `apps/lab-agent/` |

---

## 6. Test plan

### Unit (fast, no network — extend existing fake-based suites)

- `apps/canvus-mcp/tests/test_experiments.py`: add a case where `result1→setup1` (same round, real trigger) coexists with `result1→setup2` (forward round-advance edge) in one graph; assert `detect_experiment_loops` returns only the same-round edge.
- `apps/lab-agent/tests/test_orchestrator.py`: 
  - malformed/incomplete model output for each of `ExperimentSetup`/`ExperimentResult`/`LoopDecision` → assert no `create_note`/`create_connector` call occurs (extend `FakeMCP` assertions), and that a warning log is emitted (use `structlog`'s test capture or `caplog`).
  - new: **live re-scan simulation** — extend `tests/fakes.py:FakeMCP.call_tool("scan_experiment_workflow", ...)` to optionally *recompute* `loops`/`ideas_needing_setup`/`setups_needing_run` from `self.notes`/`self.connectors` (using the real `canvus_mcp.experiments.scan_workflow`/`ConnectorIndex` against the fake's recorded widgets) rather than always returning the static fixture — this closes the exact test gap that let the §1 bug through. Add a regression test: run `run_loop` for 2 rounds, then call `process_once` again against the *live* recomputed snapshot, assert `counts["loops"] == 0` (the orchestrator's own edge must not be re-processed).
- New `apps/lab-agent/tests/test_state.py` (Stage 2): SQLite ledger — insert/duplicate-insert idempotency (`UNIQUE` constraint holds), reload-from-disk after simulated restart (`processed_loops` populated from DB, not empty set).

### Integration (in-process, real SQLite file + fake MCP, no live Canvus)

- Full `once`-equivalent cycle against `FakeMCP` with the live-recompute mode above: idea→setup→robot-run→loop-continue→loop-stop, asserting the canvas graph shape (`mcp.notes`, `mcp.connectors`) matches the documented happy path in `docs/experiment-workflow.md`, with SQLite ledger correctly populated at each step and durable across a simulated process restart (new `MCPClient`+ledger, same file).
- `canvus-mcp` layer: current gap noted by scout report ("MCP tool layer lacks direct tests") — add a minimal integration test invoking the actual `@mcp.tool()`-registered functions in `canvus_mcp/tools/experiments.py`/`widgets.py` against a stub Canvus SDK client, since today only the pure `experiments.py`/`ragcluster.py` graph logic is unit-tested, not the MCP tool wrappers themselves.

### E2E (real Canvus dev server — roadmap Phase 2 prerequisite)

- Roadmap Phase 2's manual demo-canvas run, extended with a **restart-mid-loop** step: start `lab-agent watch`, let it process idea→setup→robot→result, connect result→setup, kill the watcher mid-`run_loop` (or right after round 1 completes), restart `watch`, confirm (a) no duplicate round is created, (b) the round-advance edge is not re-detected as a new user trigger. This is the direct E2E proof for FR-LITL-019/NFR-LITL-006 and for the §1 bug fix.
- Confirm `uv run pytest -q`, `uv run ruff check`, `uv run mypy` pass in both apps (Phase 1, currently Pending per roadmap status snapshot) — prerequisite gate before any of the above E2E work is meaningful.

---

## 7. Migration / rollback risks

- **`coerce` behavior change (§3)** is a breaking behavior change for any caller currently relying on defensive fill (none identified in the read code paths — `orchestrator.py`'s three call sites are the only callers). Rollback: revert `coerce` to the try/except-fill version; no schema/data migration involved (no persisted state depends on the old behavior).
- **SQLite ledger (§2)** is additive — `watch.py` degrades gracefully to today's in-memory `set()` if the ledger module isn't wired in yet (feature-flag or simple `if` around the load-from-DB call during rollout). Risk: if the SQLite file path is not `.gitignore`d, could accidentally get committed — must be added to `.gitignore` alongside the existing `.env`/`.venv`/`downloads/` exclusions (`docs/code-standards.md` git-hygiene checklist references this pattern already). No migration needed since there is no prior persisted state to migrate *from* — this is Phase 3, first time durable state is introduced.
- **Round-filter fix in `detect_experiment_loops` (§1)** changes what counts as a "loop" on any *existing* demo canvas that already has forward round-advance edges sitting unprocessed from before the fix — on first run after upgrade, those edges will simply stop appearing in `snap["loops"]` (correct, since they were never legitimate triggers) — no data loss, but any consumer currently (incorrectly) depending on that duplicate-processing behavior for effect would need re-verification. Low risk given it's pre-Phase-2 (no production canvas/data exists yet per repo status).
- **Rollback ordering**: ship §1 (pure function, no state) before §2 (adds a file) before §3 (behavior change to `coerce`) — each is independently revertible; no combined migration script needed at this scale.

---

## 8. Decisions requiring owner confirmation

1. **Ledger location/lifecycle**: single SQLite file per `lab-agent` process (e.g. `~/.local/state/lab-agent/state.db` or repo-relative `apps/lab-agent/.state/state.db`)? Per-canvas file vs. one file with a `canvas_id` column (matters once NFR multi-user isolation, Phase 7, is tackled — recommend `canvas_id` column now to avoid a later migration, but this is a call the owner/implementer should make explicitly, not assume).
2. **Whether to accept the cross-app "duplicate constant + parity test" approach (§4.2)** vs. formalizing an import dependency from `lab-agent` onto `canvus_mcp` for `ExpMarkers` — currently the two apps are decoupled at the network boundary (MCP HTTP) and this report recommends preserving that; confirm this is still desired before Phase 4+ work multiplies the shared-vocabulary surface.
3. **Retry/backoff semantics beyond "next poll cycle retries"** (§3.3) — confirmed sufficient for this near-term foundation, but confirm no stronger SLA (e.g., exponential backoff, max-attempt alerting) is expected before Phase 7 observability work.
4. Reconfirm scout report's still-open items that bear directly on this report's scope: (a) fail-closed-on-malformed-output is now resolved by this report's §3 recommendation — confirm accepted; (b) "how to distinguish user vs. orchestrator-generated edges" is resolved by this report's §1 round-filter — confirm accepted before implementation.
5. Use-case spec §18 item 3 (harness-first ratification) remains unresolved and out of this report's recommendation — Stage 3 (§2) explicitly deferred pending that decision.

## Unresolved questions

- Does `apps/lab-agent` currently have (or is it permitted to add) a Python import dependency on `apps/canvus-mcp`'s `canvus_mcp` package, or must the two remain import-decoupled (network-only) as they appear to be today? Affects §4.2's exact implementation.
- Exact SQLite file path/retention/rotation policy (§8.1) — no existing convention in the repo to infer from (no other persisted-state file exists in either app today).
- Whether "manual re-trigger" (`docs/system-architecture.md`/UC alt-flow) is ever intended to connect a result to a **different, already-existing** future-round setup (not just the same-round setup) — if so, the round-filter in §1 (`round(dst) <= round(src)`) would need loosening; current documented example (`experiment-workflow.md:71`) only shows same-round, so this report assumes same-round-only is correct, but the owner should confirm this reading of the "manual re-trigger" alt-flow in `lab-in-the-loop-use-case-specification.md:248`.
