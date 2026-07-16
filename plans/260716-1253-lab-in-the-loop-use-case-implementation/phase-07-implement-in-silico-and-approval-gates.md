# Phase 7 — Implement In-Silico & Approval Gates

## Context Links

- Canonical requirements: `docs/lab-in-the-loop-use-case-specification.md` (UC-LITL-03, FR-LITL-003/004/005, BR-LITL-006/007/009, §11, §15)
- Research: `research/researcher-03-260716-1253-safe-execution-integrations.md`
- Decisions: `reports/decision-log-260716-1253-implementation-scope.md` (authorization order and mocks-first)
- Foundation: Phase 2 durable audit/store, Phase 3 Browser artifacts, Phase 4 evidence, Phase 5 policy/budgets

## Overview

- Priority: P1 (safety boundary)
- Status: pending
- Effort: 8d
- Description: Add a typed in-silico validation stage and durable, proposal-bound scientist/lab-lead approvals. The phase ends at `APPROVED_FOR_WET_LAB`; it does not execute a real lab action.

## Key Insights

- Approval cannot be represented only by an enum. Durable evidence must record actor, role, decision, timestamp, rationale, and the exact proposal hash.
- A changed setup invalidates prior validation and approvals automatically.
- The mandatory order is: AI design → in-silico → scientist authorization → lab-lead approval → wet lab. Preliminary screening is optional and non-authorizing.
- In-silico endpoints are unavailable, so a deterministic mock/dry-run adapter and shared contract tests land before any real adapter.

## Requirements

- UC-LITL-03 and FR-LITL-003: predicted outcome, confidence/uncertainty, assumptions, risk flags, recommended changes, `proceed|revise|reject`.
- FR-LITL-004: explicit scientist review of the in-silico result.
- FR-LITL-005: explicit lab-lead resource/safety approval after scientist authorization.
- BR-LITL-006, BR-LITL-007, BR-LITL-009: no wet-lab route without ordered, durable authorization.
- NFR-LITL-001/006/008: fail-closed transitions, restart-safe evidence, auditable decisions.

## Architecture / Data Flow

```text
Evidence-backed ExperimentSetup (proposal_hash)
  → InSilicoAdapter.validate(request) [mock first]
  → InSilicoResult: proceed | revise | reject
      revise/reject → state + reason; return to design/close
      proceed → NEEDS_SCIENTIST_REVIEW
  → human-authored ScientistApproval bound to proposal_hash + result_hash
  → NEEDS_LAB_LEAD_APPROVAL
  → human-authored LabLeadApproval bound to same hashes
  → APPROVED_FOR_WET_LAB (terminal output of this phase)
```

Every transition is checked against the durable ledger and current hashes. The model cannot create human approval records. Canvas free text, note title, or connector presence alone never proves identity; production authorization stays blocked until an authenticated actor/role source validates the approval.

## Related Code Files

- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/models/validation.py` — `InSilicoRequest`, `InSilicoResult`, `ValidationDecision`, `GateApproval`, `ApprovalRole`, hashes.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/integrations/__init__.py` and `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/integrations/in_silico.py` — `InSilicoAdapter` Protocol, deterministic mock, disabled real-adapter placeholder with readiness checks.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/integrations/identity.py` — `IdentityProvider` Protocol for authenticated actor id, role, credential domain, and approval verification; static dev provider is non-production only.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/approval.py` — transition guard, hash verification, stale-approval invalidation, and identity-provider-backed human authorization.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/models/states.py` — explicit lifecycle including `IN_SILICO_RUNNING`, `IN_SILICO_COMPLETE`, `NEEDS_SCIENTIST_REVIEW`, `NEEDS_LAB_LEAD_APPROVAL`, `APPROVED_FOR_WET_LAB`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/state_store.py` — append-only validation runs and gate approvals; unique current proposal/result hashes; no overwrite.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/orchestrator.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/watch.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/nodes.py` — dispatch validation/review transitions and pending-review artifacts without real execution.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/policy.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/config.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/prompts.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/artifact_store.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/artifact_render.py` — route/budget validation and render Validation/Approvals/Audit tabs in Browser artifacts.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/config.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/experiments.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/canvus_mcp/tools/experiments.py` — add configurable in-silico/review/approval markers and pure pending-transition detection.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_approval.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_in_silico_adapter.py`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_orchestrator.py`, `/home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp/tests/test_experiments.py` — full gate order, stale hash, duplicate approval, rejection/revision, restart cases.
- Modify: `/home/ntdm/dev/lap-in-the-loop/docs/lab-in-the-loop-use-case-specification.md`, `/home/ntdm/dev/lap-in-the-loop/docs/experiment-workflow.md`, `/home/ntdm/dev/lap-in-the-loop/docs/system-architecture.md`, `/home/ntdm/dev/lap-in-the-loop/docs/setup-and-operations.md`, `/home/ntdm/dev/lap-in-the-loop/docs/code-standards.md`, `/home/ntdm/dev/lap-in-the-loop/docs/development-roadmap.md`, `/home/ntdm/dev/lap-in-the-loop/docs/project-changelog.md`.

## Implementation Steps

1. Define validation and approval models. Hash canonical serialized setup and in-silico result; record algorithm/version.
2. Add the in-silico Protocol and deterministic mock covering proceed/revise/reject, timeout, invalid schema, and provider failure. Real implementation remains disabled without endpoint/security/SLA configuration.
3. Revise `DecisionState` and centralize allowed transitions. Reject skips, reversals, and transitions with missing evidence.
4. Extend the state store with append-only validation/approval records and unique idempotency keys. A proposal change marks previous approvals stale without deleting history.
5. Add an `IdentityProvider` boundary. Production approval requires authenticated actor id + authorized role + credential domain; a static/dev identity provider is visibly non-production and cannot enable real execution.
6. Add Canvus marker/connector detection for validation Browser artifacts and human review Notes. Treat Note input as approval request/evidence pointer; verify identity/role through the provider before creating `GateApproval`. Free text and model outputs cannot authorize. Render current validation and approval history as artifact tabs.
7. Wire orchestrator/watch handlers through Phase 5 policy and budget. On revise/reject/failure, write/audit the reason and never expose an executable wet-lab transition.
8. Add contract and state-machine tests: ordered happy path, optional preliminary rejection, stale hash, wrong role, duplicate, restart, timeout, and fail-closed invalid output.
9. Update docs with exact state diagram, human responsibilities, mock status, and external real-adapter entry criteria.

## Todo List

- [ ] In-silico and approval contracts added
- [ ] Mock `InSilicoAdapter` and contract tests pass
- [ ] State lifecycle and transition guard implemented
- [ ] Identity-provider boundary and production identity readiness gate added
- [ ] Durable proposal/result/actor-bound approval evidence added
- [ ] Canvus pending-validation/review markers detected
- [ ] Scientist then lab-lead order enforced; stale approvals rejected
- [ ] Phase ends at `APPROVED_FOR_WET_LAB`; no real lab call exists
- [ ] Safety, workflow, architecture, operations, roadmap, spec, and changelog updated

## Success Criteria / Validation

- `cd apps/canvus-mcp && uv run pytest -q && uv run ruff check canvus_mcp tests && uv run mypy canvus_mcp`
- `cd apps/lab-agent && uv run pytest -q && uv run ruff check lab_agent tests && uv run mypy lab_agent`
- In-silico output contains the complete UC-LITL-03 contract and fails closed on malformed output.
- `APPROVED_FOR_WET_LAB` is unreachable without current-hash, identity-verified scientist and lab-lead approvals in order.
- A free-text note or unverified Canvas author cannot produce authorization; production mode stays blocked when identity integration is unavailable.
- Editing the setup invalidates all prior validation/approval evidence for authorization purposes while preserving audit history.
- No test or code path invokes a real robot/wet-lab system.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Canvas identity is mistaken for authenticated approver identity | Med | Critical | IdentityProvider verification is mandatory in production; free text/marker/connector alone never authorizes. |
| Stale approval authorizes changed proposal | Low | Critical | Proposal/result hashes on every gate and transition; mutation invalidation tests. |
| State enum becomes the only audit record | Med | High | Append-only GateApproval/validation records are authoritative evidence; enum is current projection only. |
| Mock adapter is mistaken for scientific validation | Med | High | Prominent mock/dry-run labels in state, note, config, and docs; real gate disabled by default. |

## Security Considerations

- Approval actions require an authenticated, role-authorized identity source. Development/static identity is marked non-production and cannot unlock a real lab adapter; model tools remain read-only.
- Store hashes, actor ids, roles, decisions, and rationale; never store credentials. Add tamper-evident event sequencing where practical.
- No external in-silico data transfer before Phase 4 locality authorization.

## Next Steps / Dependencies

- Depends on: Phases 2–5. Phase 6 is optional unless validation consumes ingested media.
- Blocks: Phase 8 real/dry-run execution adapters.
- External gate: validated in-silico endpoint, identity/role source, scientific owner, and compliance requirements.
- Docs impact: major.
