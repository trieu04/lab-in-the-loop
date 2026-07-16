# Development Roadmap

## Architecture direction note (proposed, pending owner confirmation)

A 2026-07-16 architecture-recovery review recommended evolving Lab-in-the-Loop toward an independent, provider-neutral harness/orchestrator, with the Claude Code skill as an optional interface only (see [system architecture](system-architecture.md) → "Target harness boundary"). Phases 3–7 below have been strengthened to reflect that direction. **This is a proposed direction, not an approved plan** — the source review's architecture-choice prompt was interrupted and never explicitly ratified by the project owner. None of the phases below are marked complete on the strength of that review alone.

## Status snapshot

| Area | Status | Notes |
|---|---|---|
| Repo split | Files extracted; not committed | New repo initialized at `~/dev/lap-in-the-loop` on branch `main`; no commits exist yet, all files are untracked |
| `canvus-mcp` migration | Complete | Source/tests/docs copied, generated artifacts excluded |
| `lab-agent` migration | Complete | Source/tests/docs copied, generated artifacts excluded |
| Full docs initialization | Complete | README + architecture/workflow/setup/integration/standards/changelog |
| Test verification | Pending | Run after dependency sync in new repo |
| Real robot integration | Future | Mock-only for now |
| Flywheel/in-silico gates | Future | Documented, not implemented |

## Phase 0 — Repository extraction

**Status:** Files extracted; not committed

Goal: move Lab-in-the-Loop work out of `rag-canvus` into a standalone repo.

Completed:

- Created `~/dev/lap-in-the-loop`.
- Initialized git with `main` branch (no commits yet — `git log` reports the branch has no commits, and `git status` shows every file as untracked).
- Copied `apps/canvus-mcp` excluding `.env`, `.venv`, caches, downloads.
- Copied `apps/lab-agent` excluding `.env`, `.venv`, caches.
- Copied full use-case doc.
- Preserved serving-side `{exp:}` patch under `integrations/`.
- Added root `.gitignore`.
- Added full documentation set.

Not yet done: an initial commit. Do not describe this extraction as a committed release until that commit exists.

Success criteria:

- No secrets copied.
- No virtualenv/cache/downloads copied.
- New repo has independent README/docs.

## Phase 1 — Local verification

**Status:** Pending

Goal: prove migrated code runs in the new path.

Tasks:

- Run `uv sync --extra dev` in `apps/canvus-mcp`.
- Run `uv run pytest -q` in `apps/canvus-mcp`.
- Run `uv run ruff check canvus_mcp tests`.
- Run `uv run mypy canvus_mcp`.
- Run `uv sync --extra dev` in `apps/lab-agent`.
- Run `uv run pytest -q` in `apps/lab-agent`.
- Run `uv run ruff check lab_agent tests`.
- Run `uv run mypy lab_agent`.

Success criteria:

- Tests pass or failures are documented with root cause.
- Lint has no syntax/import failures.
- Lockfiles remain consistent.

## Phase 2 — Demo canvas operation

**Status:** Pending

Goal: run one end-to-end mock loop on a demo Canvus canvas.

Tasks:

- Start `canvus-mcp` with dev credentials.
- Register or directly target MCP URL.
- Create demo canvas with `RAGCluster_`, knowledge feeder, idea note, and `Robot_`.
- Run `lab-agent once --canvas <canvas-id>`.
- Confirm setup note creation.
- Connect setup → robot.
- Run once again and confirm result note creation.
- Connect result → setup.
- Run once/watch and confirm continue/stop behavior.

Success criteria:

- Canvas visibly shows setup/result/closed or next round.
- Notes include required first-line markers.
- No duplicate setup/result on repeated scans.

## Phase 3 — Harness contracts and persistent loop state

**Status:** Future

Goal: make loop idempotency durable across process restarts, and define the source-of-truth contracts (schemas, prompts, provider policy, output normalization) that a future independent harness would own instead of `lab-agent` alone — see [system architecture](system-architecture.md) → "Target harness boundary (proposed)".

Options for durable idempotency:

- Store processed loop connector ids in a local SQLite state file.
- Encode processed marker in a canvas note/metadata field.
- Infer processed state entirely from next setup/result/closed descendants.

Preferred initial approach: infer from canvas graph where possible; use local state only when graph inference is insufficient.

Additional tasks:

- Define retry/resume/audit semantics for a failed or interrupted watcher cycle (which step re-runs safely, which does not).
- Decide the authoritative owner of workflow schemas (`ExperimentSetup`/`ExperimentResult`/`LoopDecision`), prompts, and provider policy so the Claude Code skill, `lab-agent`, and the `canvus-serving` `{exp:}` action do not drift independently (see [canvus-serving integration](canvus-serving-integration.md)).

Success criteria:

- Restarting watcher does not reprocess old loop connectors.
- Manual canvas edits can intentionally trigger a new round.
- A single documented contract governs schemas/prompts/policy across all three current implementations.

## Phase 4 — Stronger grounding

**Status:** Future

Goal: improve scientific context quality before setup generation, moving from canvas/RagCluster-only context toward internal wiki/knowledge-graph/acronym grounding.

Tasks:

- Add richer RagCluster feeder summarization.
- Support PDF excerpt selection and citation in setup notes.
- Add acronym/term uncertainty section to setup output.
- Track knowledge source ids in setup/result notes.
- Integrate an internal wiki/knowledge-graph retrieval source and an acronym dictionary so the model is not limited to canvas-local context; enforce that ambiguous domain terms are looked up rather than guessed (UC §9.1, §13.2).

Success criteria:

- Every setup can cite the internal notes/PDFs/widgets it used.
- Ambiguous terms are visible, not silently guessed.

## Phase 4b — Token/resource governance and model routing

**Status:** Future

Goal: give the harness/orchestrator control over cost, provider selection, and where data is allowed to flow.

Tasks:

- Add token/resource budgets and cost thresholds per run and per canvas.
- Add task-based model routing (e.g. cheaper model for result rendering, stronger model for setup design) on top of the existing `openai`/`claude` adapter factory.
- Add data-locality controls so sensitive canvas content can be restricted to specific providers/endpoints.
- Extend loop stop policies beyond `LAB_AGENT_LOOP_MAX_ROUNDS`: cost-based stop, wall-clock stop, no-progress stop.

Success criteria:

- A run can be capped by token/cost budget, not only round count.
- Model/provider choice can vary per task type without code changes.

## Phase 4c — Async multimodal ingestion

**Status:** Future

Goal: support PDF/image/video/table ingestion at scale without blocking the loop on one large model call (UC §11).

Tasks:

- Chunk large documents/media instead of sending them whole to one model call.
- Cache extracted/summarized content so repeated scans do not re-process unchanged assets.
- Make ingestion resumable and track progress for long-running extractions.
- Add modality-specific extraction (PDF text, image/video description, tabular data) ahead of setup generation.

Success criteria:

- A multi-day/large-asset ingestion case can resume after interruption without redoing completed chunks.
- Ingestion progress is observable, not a single opaque long-running call.

## Phase 5 — In-silico validation gate

**Status:** Future

Goal: add a digital-twin/in-silico step before wet lab execution.

Workflow target:

```text
AI Experiment Design
→ In Silico Simulation
→ Predicted Outcome + Confidence
→ Proceed / Revise / Reject
→ Human Approval
→ Lab Execution
```

Success criteria:

- Setup can be routed to an in-silico validation node.
- Result includes predicted outcome, assumptions, risk flags, and recommendation.
- Wet-lab execution remains blocked until approval gate is explicit.

## Phase 6 — In-silico, human approval, Flywheel, and lab integration

**Status:** Future

Goal: replace mock robot results with real execution/result ingestion where authorized, gated by explicit review and approval.

Approval sequence (this repo's canonical ordering — see the [original meeting vision](notes/use-case-lab-in-the-loop.md) §8 and the [canonical use case specification](lab-in-the-loop-use-case-specification.md) for the reconciled gate ordering):

```text
AI design → in-silico validation → scientist review → lab lead approval → wet lab
```

Candidate integrations:

- robotic lab scheduler;
- Flywheel data upload event;
- micro-CT segmentation/quantification gear;
- HPC/R/Python analysis pipeline;
- knowledge update/versioning service.

Safety requirements:

- explicit scientist review and lab-lead approval before wet-lab execution;
- audit log;
- dry-run mode;
- rollback/abort path;
- no autonomous wet-lab execution without durable authorization.

Success criteria:

- Real result artifacts link back to setup id and round.
- Analysis output is ingested into canvas/knowledge store.
- Next-round decision uses measured results, not mock data.
- No wet-lab step runs without a recorded scientist-review and lab-lead-approval decision.

## Phase 7 — Production hardening, multi-user deployment, and observability

**Status:** Future

Tasks:

- Add structured logs with run ids.
- Add metrics: scans, setups created, results created, decisions, failures.
- Add health checks for MCP and model providers.
- Add integration tests with a fake Canvus/MCP server.
- Package both apps for deployment.
- Add CI.
- Support multiple concurrent users/canvases with per-user/per-canvas isolation of loop state and credentials.
- Add observability (dashboards/alerts) for watcher liveness, error rate, and per-canvas loop progress across users.

Success criteria:

- Repo can be tested in CI.
- Watcher failures are observable.
- Deployment docs cover restart and recovery.
- Multiple users/canvases can run concurrently without state or credential leakage between them.
