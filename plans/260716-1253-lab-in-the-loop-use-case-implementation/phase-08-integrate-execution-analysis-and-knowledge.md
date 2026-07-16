# Phase 8 — Integrate Execution, Analysis & Knowledge

## Context Links

- Canonical requirements: `docs/lab-in-the-loop-use-case-specification.md` (FR-LITL-006/007/008/009/010/017/018, BR-LITL-008, NFR-LITL-005)
- Research: `research/researcher-03-260716-1253-safe-execution-integrations.md`
- Approval prerequisite: `phase-07-implement-in-silico-and-approval-gates.md`
- Existing mock workflow: `apps/lab-agent/lab_agent/orchestrator.py`

## Overview

- Priority: P1 for safety contracts; P2 for real adapters
- Status: pending
- Effort: 8d core mocks/contracts; real adapters estimated only after external API discovery
- Description: Add pluggable Flywheel/HPC analysis, lab-execution, and versioned-knowledge adapters. Ship contract-tested mocks/dry-run first. Real execution stays disabled until endpoint, identity, authorization, safety, retention, and abort criteria are met.

## Key Insights

- Physical execution is not transactionally reversible. "Rollback" means abort pending work, stop downstream steps, preserve artifacts, and append compensating audit events — never pretend the experiment did not happen.
- Analysis is the lowest physical-risk real adapter and should be integrated before robot/lab execution.
- Knowledge updates must be append-only versions linked to setup, execution, data, analysis, interpretation, and approvals.
- External adapters must not bypass the Phase 6 proposal-hash authorization chain.

## Requirements

- FR-LITL-006/007: authorized human/robot execution and raw-result artifact capture; mock remains clearly labeled.
- FR-LITL-008, FR-LITL-017: Flywheel/HPC job lifecycle, failure visibility, retained data path, safe rerun.
- FR-LITL-009: interpretation against the original hypothesis with evidence and caveats.
- FR-LITL-010, BR-LITL-008, NFR-LITL-005: append-only, reproducible knowledge versions with provenance metadata.
- FR-LITL-018: conflict artifact preserves old/new hypotheses and evidence; never overwrite history.
- BR-LITL-006/009: no real lab action without current durable approvals.

## Architecture / Data Flow

```text
APPROVED_FOR_WET_LAB + proposal_hash
  → LabExecutionAdapter.submit(dry_run by default)
  → ExecutionRun + raw ArtifactRef[]
  → FlywheelAdapter.submit/analyze → AnalysisRun + derived ArtifactRef[]
  → interpretation model (grounded in setup + measured results)
  → KnowledgeAdapter.append_version(provenance)
      ├─ consistent → version + next-round decision
      └─ conflict → append ConflictRecord + preserve both hypotheses
```

Each adapter implements submit/status/cancel-or-abort/result and idempotency-key semantics through the Phase 2 intent/reconciliation layer. Mocks and real adapters share the same contract suite.

## Delivery Sub-Milestones

1. **7A — Shared contracts + mocks:** all three Protocols, dry-run implementations, contract tests, lifecycle persistence.
2. **7B — Flywheel/HPC sandbox:** first real adapter because it has the lowest physical risk; requires a child plan after API/retention/SLA discovery.
3. **7C — Versioned knowledge store:** real append/version/conflict adapter; requires a child plan after storage/versioning policy discovery.
4. **7D — Robot/wet-lab sandbox:** last real adapter; requires separate safety review, child plan, identity readiness, abort runbook, and explicit enablement approval.

The 8d estimate covers 7A. The master plan is not fully complete until applicable 7B–7D child plans are approved and executed; unavailable integrations remain visible external gates, not silently accepted mocks.

## Related Code Files

- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/models/execution.py` — `ArtifactRef`, `ExecutionRequest/Run`, `AnalysisRequest/Run`, `KnowledgeVersion`, `ConflictRecord`, lifecycle enums.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/integrations/lab_execution.py` — `LabExecutionAdapter` Protocol + mock/dry-run implementation + disabled real factory hook.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/integrations/flywheel.py` — `FlywheelAdapter` Protocol + mock job lifecycle + real readiness hook.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/integrations/knowledge.py` — `KnowledgeAdapter` Protocol + local append-only mock version store.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/execution_orchestrator.py` — approval check, idempotent submit/poll/abort, artifact handoff.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/knowledge_update.py` — provenance assembly, append-only update, conflict detection/recording.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/orchestrator.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/watch.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/nodes.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/models/states.py` — dispatch external lifecycle without growing one oversized module.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/state_store.py` — execution/analysis/job/artifact/version/conflict records and idempotency keys.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/policy.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/config.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/prompts.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/artifact_store.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/artifact_render.py` — feature flags, routing, budgets, measured-result interpretation, and Execution/Analysis/Versions/Conflict tabs.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/config.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/experiments.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/tools/experiments.py` — execution/analysis/knowledge/conflict marker detection.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/contracts/test_lab_execution_contract.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/contracts/test_flywheel_contract.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/contracts/test_knowledge_contract.py`.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_execution_orchestrator.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_knowledge_update.py`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_orchestrator.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/tests/test_experiments.py`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/README.md`, `/home/ntdm/dev/lap-in-the-loop/docs/lab-in-the-loop-use-case-specification.md`, `/home/ntdm/dev/lap-in-the-loop/docs/system-architecture.md`, `/home/ntdm/dev/lap-in-the-loop/docs/experiment-workflow.md`, `/home/ntdm/dev/lap-in-the-loop/docs/setup-and-operations.md`, `/home/ntdm/dev/lap-in-the-loop/docs/code-standards.md`, `/home/ntdm/dev/lap-in-the-loop/docs/development-roadmap.md`, `/home/ntdm/dev/lap-in-the-loop/docs/project-changelog.md`.

## Implementation Steps

1. Define execution/analysis/knowledge contracts with explicit statuses, artifact ids/hashes/locations, provider job ids, idempotency keys, and typed failure reasons.
2. Implement mocks/dry-run adapters and a reusable contract-test suite. Mock outputs must remain visibly labeled and cannot satisfy a real-execution audit predicate.
3. Add `execution_orchestrator.py`: verify current Phase 6 approvals and proposal hash before submit; reserve Phase 4 budget; persist a Phase 2 side-effect intent before the external call; reconcile provider status/idempotency key after timeout/restart before retrying.
4. Implement submit/status/abort semantics. For non-reversible work, append compensation/incident events and block downstream steps rather than deleting history.
5. Integrate Flywheel/HPC mock lifecycle first; retain raw data path on failure and allow idempotent rerun with a new attempt linked to the original job.
6. Implement knowledge append/version and provenance. Add conflict detection that creates a versioned Conflict Browser artifact and keeps both versions; expose provenance and differences in dedicated tabs.
7. Add measured-result interpretation and next-round decision using real artifacts when present; mock results cannot be presented as measured evidence.
8. After each external API discovery, create a separate child plan for Flywheel/HPC, knowledge store, or lab execution. Include exact API schemas, failure semantics, contract-test fixtures, rollout/rollback, owner approval, and a revised effort estimate.
9. For each real adapter, run a readiness checklist before enabling: API/schema, credentials, data locality, SLA/timeouts, idempotency, sandbox, abort behavior, retention, domain-owner validation, and incident response.
10. Update all affected docs and changelog; label which adapters are mock, dry-run, sandbox, or production.

## Todo List

- [ ] Execution/analysis/knowledge contracts added
- [ ] Mock/dry-run adapters pass shared contract tests
- [ ] Approval-bound execution orchestration is restart-safe
- [ ] Job failure/rerun/abort and artifact retention implemented
- [ ] Append-only knowledge versions and provenance implemented
- [ ] Conflict records preserve old and new hypotheses
- [ ] Measured vs mock evidence is never conflated
- [ ] Real-adapter readiness gates documented and default-disabled
- [ ] Separate child-plan template/trigger defined for Flywheel, knowledge, and lab adapters
- [ ] README and all affected docs/changelog updated

## Success Criteria / Validation

- `cd apps/canvus-mcp && uv run pytest -q && uv run ruff check canvus_mcp tests && uv run mypy canvus_mcp`
- `cd apps/lab-agent && uv run pytest -q && uv run ruff check lab_agent tests && uv run mypy lab_agent`
- Mock end-to-end: approved setup → dry-run execution → mock analysis → interpretation → append-only knowledge version → next-round/close.
- Missing/stale approvals, feature flag off, identity readiness failure, or adapter readiness failure cause zero real submit calls.
- Ambiguous submit timeout is reconciled by provider job/idempotency key before any retry.
- Failed analysis keeps artifact/data references and supports an idempotent linked retry.
- Knowledge updates never overwrite a prior version; conflicts create durable records.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Accidental real lab submit | Low | Critical | Default-disabled real adapter, dry-run default, approval/hash check, dual feature gates, contract tests, operator confirmation. |
| Retry duplicates external job | Med | High | Persist intent/idempotency key before submit; reconcile provider job id; adapter contract requires idempotency semantics. |
| Artifact/data locality violation | Med | Critical | Phase 4 policy before upload/download; allowlisted endpoints; retention and encryption readiness gate. |
| Knowledge conflict is silently overwritten | Low | High | Append-only versions, conflict predicate, immutable provenance and tests. |

## Security Considerations

- Separate credentials per adapter; do not place them on canvas or in audit payloads.
- Human authorization evidence and external job ids are security-sensitive; enforce canvas/tenant scope and least privilege.
- Real lab integration needs explicit safety review and incident/abort runbook before enablement.

## Next Steps / Dependencies

- Depends on: Phases 2, 3, 5, and 7; Phase 4 evidence; Phase 6 when large artifacts require ingestion.
- Blocks: Phase 9 production readiness and full E2E.
- External gates: real Flywheel/HPC, lab/robot, and versioned knowledge-store contracts. Each requires an approved child plan; implementation effort is TBD after discovery.
- Docs impact: major.
