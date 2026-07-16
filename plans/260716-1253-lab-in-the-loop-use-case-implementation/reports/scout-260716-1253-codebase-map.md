# Workshop Survey Report

## Scope

Implementation planning for `docs/lab-in-the-loop-use-case-specification.md` at `--level max`.

## Relevant Files

### `apps/lab-agent`

- `apps/lab-agent/lab_agent/watch.py` — workflow polling, pending-trigger dispatch, session-local `processed_loops`.
- `apps/lab-agent/lab_agent/orchestrator.py` — idea→setup, mock execution, continue/stop loop.
- `apps/lab-agent/lab_agent/nodes.py` — sole canvas write surface and note state rendering.
- `apps/lab-agent/lab_agent/loop.py` — bounded read-tool/model loop and structured emission.
- `apps/lab-agent/lab_agent/tool_bridge.py` — model-facing read allowlist; blocks writes.
- `apps/lab-agent/lab_agent/models/experiment.py` — current setup/result/decision contracts.
- `apps/lab-agent/lab_agent/models/states.py` — nine target states; three have runtime transitions.
- `apps/lab-agent/lab_agent/adapters/` — provider-neutral protocol plus OpenAI/Claude implementations.
- `apps/lab-agent/lab_agent/config.py` — provider, model, polling, tool-step, and round bounds.
- `apps/lab-agent/tests/` — fake MCP/scripted adapter and unit coverage for orchestrator, bridge, adapters.

### `apps/canvus-mcp`

- `apps/canvus-mcp/canvus_mcp/experiments.py` — pure marker classification, graph scan, pending triggers, loop back-edge detection.
- `apps/canvus-mcp/canvus_mcp/ragcluster.py` — connector index and RagCluster graph traversal.
- `apps/canvus-mcp/canvus_mcp/config.py` — configurable canvas marker vocabulary.
- `apps/canvus-mcp/canvus_mcp/tools/experiments.py` — MCP wrappers for workflow scan/detection.
- `apps/canvus-mcp/canvus_mcp/tools/content.py` — note/widget/PDF/image/asset reads.
- `apps/canvus-mcp/canvus_mcp/tools/widgets.py` — note/browser/image/connector writes.
- `apps/canvus-mcp/canvus_mcp/downloads.py` — persisted downloads with MIME and hash metadata.
- `apps/canvus-mcp/tests/` — pure workflow, graph, and download tests; MCP tool layer lacks direct tests.

### Project contracts

- `docs/lab-in-the-loop-use-case-specification.md` — canonical target requirements and unresolved owner decisions.
- `docs/development-roadmap.md` — implementation sequence Phase 1–7.
- `docs/system-architecture.md` — current boundary and proposed harness-first target.
- `docs/experiment-workflow.md` — marker, connector, idempotency, and tool contracts.
- `docs/code-standards.md` — provider-neutral, typed, read/write-separated implementation rules.
- `docs/setup-and-operations.md` — environment and manual demo operation.
- `docs/project-changelog.md` — current limitations and documentation history.
- `integrations/canvus-serving-experiment-prepare/` — separate `{exp:}` action; not the primary loop.

## Dependency and Data Flow

```text
Canvus REST
  → canvus-sdk
  → canvus-mcp tools
  → scan_experiment_workflow snapshot
  → lab-agent watch dispatcher
  → model/read-tool loop
  → typed setup/result/decision
  → orchestrator-only note/connector writes
  → connector graph becomes durable workflow state
```

New workflow gates require coordinated changes in both apps: canvus-mcp must classify/detect the graph state; lab-agent must validate transitions, call external adapters, and perform authorized writes.

## Existing Reusable Patterns

- Pure graph scanning before network/tool wrappers.
- Typed Pydantic boundary models.
- Provider adapter protocol.
- Strict model-read/orchestrator-write separation.
- Connector presence as state-transition evidence.
- Focused render/prompt/config modules.
- In-memory MCP/model fakes for deterministic orchestration tests.

## Critical Findings

1. `result → setup` edges created by the orchestrator are structurally identical to user loop triggers. A later live scan can re-detect generated edges and cascade; static workflow fakes do not expose this.
2. `processed_loops` is session-local and reset for `once`; restart-safe idempotency is absent.
3. `orchestrator_support.coerce` fills malformed required outputs, conflicting with the documented fail-visible/leave-pending policy.
4. Workflow vocabulary and contracts are split between both apps and the optional serving integration; Phase 3 needs a single owner.
5. Six of nine `DecisionState` values have no transition logic; post-silico review states are absent.
6. No source exists for in-silico, human approval, Flywheel, real lab execution, knowledge versioning, cost governance, async ingestion, or multi-user observability.
7. Tool/client/server layers and real graph re-scan behavior lack integration coverage.
8. CI, deployment packaging, health checks, and demo-seeding automation do not exist.
9. README/roadmap/changelog git-state text is stale: commit `298e234` exists while the working tree remains untracked.

## Estimated Change Surface

- Full target: both apps, new shared contract/policy modules, external adapters/services, tests, operational artifacts, and synchronized docs.
- Strongly phased: verification/correctness → durable harness state/contracts → grounding/governance/ingestion → in-silico/approval → execution/analysis/versioning → production hardening.
- Whole-spec implementation exceeds eight files, three phases, and two new services; a master plan with milestone gates is required.

## Unresolved Questions

- Ratify harness-first architecture or keep evolving `lab-agent` without a formal harness boundary?
- Which target schema fields become first-class now?
- Canonical approval ordering and required state transitions?
- How to distinguish user-authored loop triggers from orchestrator-generated round edges?
- Fail closed on malformed model output or preserve defensive coercion?
- Which external systems/endpoints are available for in-silico, Flywheel, lab execution, and knowledge versioning?
