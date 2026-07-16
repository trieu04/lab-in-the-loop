# Source Session Recovery — 958a41ff-c7bc-4992-8559-5bcf9e229f6d

## Scope and evidence

- Source transcript: `/home/ntdm/.claude/projects/-home-ntdm-dev-rag-canvus/958a41ff-c7bc-4992-8559-5bcf9e229f6d.jsonl`
- Source project: `/home/ntdm/dev/rag-canvus`
- Session activity: approximately 2026-07-16 10:26–10:34 Asia/Saigon.
- Recovery used the transcript plus read-only checks of files explicitly referenced by it.
- No facts below are attributed to the source session unless supported by its messages/tool records.

## User goals

1. Decide whether Lab-in-the-Loop should be implemented primarily as a Claude Code skill or as an independent agent.
2. Re-evaluate that choice using meeting notes about “harness engineering.”
3. Preserve model-provider independence so the workflow can run with Claude, OpenAI, Ollama, vLLM, or internal endpoints without requiring every user to hold a Claude license.
4. Define the production boundary connecting Canvus, internal knowledge, model execution, Flywheel/lab tools, safety rules, and resource governance.

## Final session conclusion

The final response strengthened the initial “hybrid agent + skill” recommendation into a harness-first architecture:

- **Core product:** an independent, model-independent Lab-in-the-Loop harness/orchestrator.
- **Runtime:** a headless service/agent that watches Canvus and coordinates workflow state and tools.
- **Claude Code skill:** an optional developer/operator/demo client over the same harness, not the production runtime or source of truth.
- **Models:** Claude, OpenAI, Ollama, vLLM, and internal models are replaceable adapters behind a common interface.
- **Shared integration seam:** Canvus MCP/API and shared schemas/tools, with business logic kept out of the skill.

The session’s shorthand verdict was: **“skill is interface; harness is product.”**

### Decision status caveat

- An `AskUserQuestion` prompt offering four architecture options was rejected/interrupted and received no selection.
- The user then supplied additional meeting evidence and asked the assistant to decide from it.
- The harness-first conclusion is therefore the **final session recommendation/conclusion**, but the transcript contains no later explicit user ratification such as “approved” or “proceed.”

## Responsibilities assigned to the harness

The final response proposed that the independent harness own:

- Canvus event watching/scanning.
- Durable workflow state and a state machine.
- Idempotency, retry, resume, failure recovery, and audit/version history.
- Policy and approval gates.
- Token/resource budgets, model routing, cost thresholds, loop limits, and stop conditions.
- Retrieval and grounding against internal wiki, knowledge graph, acronym dictionary, documents, and experiment history.
- Context packaging and evidence tracking.
- Model adapter selection and structured-output normalization.
- Tool/action routing to Canvus, Flywheel, in-silico simulation, robotic/human lab execution, and knowledge updates.
- Async, chunked, cached, resumable multimodal ingestion rather than sending all data to one model call.

The proposed wet-lab safety sequence was:

```text
AI design → in-silico validation → scientist review → lab lead approval → wet lab
```

## Existing implementation discovered by the session

These were existing artifacts found by read-only inspection, not work created in the session:

- `/home/ntdm/dev/rag-canvus/apps/lab-agent/`
  - Existing headless watcher/orchestrator with `once` and `watch` CLI paths.
  - Existing `ModelAdapter` protocol and OpenAI/Claude adapters.
  - OpenAI adapter supports OpenAI-compatible `base_url` endpoints, covering Ollama/vLLM/Azure-compatible deployments.
  - Current loop connector idempotency is session-local/in-memory and is lost on restart.
- `/home/ntdm/dev/rag-canvus/.claude/skills/lab-in-the-loop/SKILL.md`
  - Existing Claude Code skill that mirrors the headless watcher and uses the same Canvus MCP workflow tools.
  - Suitable for interactive/demo/operator use, but intrinsically tied to a Claude Code session.
- `/home/ntdm/dev/rag-canvus/apps/canvus-mcp/`
  - Existing shared MCP substrate for canvas reads/writes, workflow scanning, and loop detection.
  - Identified as the load-bearing integration boundary shared by the skill and agent.
- `/home/ntdm/dev/rag-canvus/apps/canvus-serving/`
  - Existing FastAPI/SQLite scanner, queue, lease/heartbeat, and worker scaffolding.
  - Existing `experiment_prepare.py` is a narrow OpenAI-specific `{exp: ...}` action, not the full model-independent closed loop.
- `/home/ntdm/dev/rag-canvus/docs/UC-Lab-in-the-Loop.md`
  - Already documents model agnosticism, grounding, scalability, approval gates, Flywheel/in-silico integration, loop bounds, and versioned knowledge updates.

Read-only recovery checks confirmed these referenced files still exist. Current project files also confirm the session’s key observations: the lab watcher keeps `processed_loops` in memory, the adapter factory exposes `openai` and `claude`, and the serving queue provides more durable claim/dedup/heartbeat mechanics.

## Files created or changed in the source session

**None.**

Evidence:

- The transcript contains no `Write`, `Edit`, or shell mutation tool call.
- All three file-history snapshots contain empty `trackedFileBackups` maps.
- The only repository inspection was a direct `Read` plus a read-only `Explore` agent.

The current `rag-canvus` working tree contains modified and untracked files, including the Lab-in-the-Loop artifacts, but the source session cannot be credited with those changes because it performed no file writes. Their authorship/timing is outside the transcript evidence.

## Commands, tools, and tests run

Completed source-session operations:

- Invoked the brainstorm flow.
- Invoked the `scan-codebase` skill in read-only mode.
- Invoked the `claude-api` skill for conceptual clarification.
- Read `docs/UC-Lab-in-the-Loop.md`.
- Ran one synchronous read-only `Explore` agent over `apps/lab-agent`, `apps/canvus-mcp`, `apps/canvus-serving`, and the use-case document.
- Attempted an `AskUserQuestion` architecture-choice prompt; the user rejected/interrupted it.

**No Bash commands, builds, linters, type checks, or tests were run in the source session.** No test outcome was produced.

## Completed work versus proposals

### Completed

- Repository/use-case reconnaissance.
- Identification of the existing skill, headless agent, MCP substrate, and serving queue/action paths.
- Architectural comparison of skill-first, agent-first, service-first, and hybrid approaches.
- Final harness-first recommendation based on the meeting notes.
- Identification of architecture drift risk across three implementations.

### Proposed only; not implemented or verified

- A standalone harness core with policy, retrieval, routing, audit, and durable state.
- Provider adapters beyond the current OpenAI/Claude factory choices.
- Mandatory acronym/internal-knowledge grounding enforcement.
- Token/resource governance and task-based model routing.
- Multimodal chunking/caching/resumable ingestion.
- In-silico and human approval gates.
- Flywheel/robotic lab integration and versioned knowledge updates.
- Production multi-user deployment and observability.
- The six-phase roadmap in the final response.

### Failed/interrupted attempt

- The multiple-choice architecture confirmation was not answered; the tool call was rejected and the request was interrupted.

## Unresolved issues

1. Where durable orchestration state should live: `lab-agent`, `canvus-serving`’s SQLite queue, or a new harness service/tool boundary.
2. Whether `canvus-serving/app/actions/experiment_prepare.py` is a separate lightweight entry point or should eventually be superseded by the harness.
3. Where human approval and in-silico gates attach in the runtime flow.
4. Which component is the authoritative source of workflow schemas, prompts, provider policy, and output normalization.
5. How to prevent drift among the Claude Code skill, `lab-agent`, and serving-side experiment action.
6. Whether Ollama/vLLM/internal providers must receive first-class named adapters or are intentionally supported only through OpenAI-compatible endpoints.
7. Exact production contracts for retrieval, data locality, token budgets, model routing, multimodal ingestion, and audit/versioning.
8. Explicit owner confirmation of the harness-first decision remains absent from the transcript.

## Documentation implications for `lap-in-the-loop`

The current repository already documents a standalone `lab-agent`, model-agnostic intent, grounding, future approval gates, and Flywheel/in-silico work. The recovered session adds a stronger product-positioning decision that should be made explicit.

### `README.md`

Add a prominent architecture statement near the overview:

- The product is an **independent harness/orchestrator**, not a Claude Code skill.
- `lab-agent` is the current harness runtime implementation.
- Claude Code/MCP registration is optional and acts only as a developer/operator/demo control surface.
- Model providers are adapters; no production workflow may require a Claude license.

Also distinguish the current mock-loop MVP from the intended production harness responsibilities: durable state, grounding, governance, approvals, multimodal scale, and external action routing.

### `docs/system-architecture.md`

Expand the architecture from the current two-app runtime diagram into an explicit harness boundary containing:

- event watcher;
- workflow state machine;
- policy/approval engine;
- resource/token governance;
- retrieval/grounding and context packaging;
- model adapter/router;
- action/tool router;
- audit/versioning.

Document the Claude Code skill as an external client, not a core runtime component or business-logic owner.

### `docs/decisions/`

Create an architecture decision record for “Independent harness core; Claude Code skill as optional interface.” Because explicit user ratification is absent, initially mark the ADR as **Proposed/Pending owner confirmation** unless the project owner separately confirms it.

### `docs/development-roadmap.md`

Add or strengthen phases for:

1. Harness contracts and source-of-truth schemas.
2. Durable state, idempotency, retry/resume, and audit.
3. Internal knowledge/wiki/KG/acronym grounding.
4. Token governance, model routing, data locality, and stop policies.
5. Async multimodal ingestion, chunking, caching, and progress tracking.
6. In-silico, human approval, Flywheel, and lab integrations.
7. Multi-user deployment and observability.

### Other docs

- `docs/use-case-lab-in-the-loop.md` already contains most meeting requirements; add the harness-first positioning and cross-link the ADR rather than duplicating the full discussion.
- `docs/setup-and-operations.md` should describe Claude Code as optional and include provider-neutral runtime operation when the harness contracts are finalized.
- `docs/project-changelog.md` should record the architecture decision only when the documentation change is actually made.

## Recovery conclusion

The source session made no code or documentation changes. Its durable output is an architectural conclusion: evolve Lab-in-the-Loop as an independent, provider-neutral harness/orchestrator; retain Claude Code only as a thin optional operator/development interface; centralize workflow logic, policy, grounding, and schemas in the harness and shared tool boundaries.
