# Phase 5 — Add Governance & Model Routing

## Context Links

- Canonical requirements: `docs/lab-in-the-loop-use-case-specification.md` (FR-LITL-013/014, NFR-LITL-002/003/009)
- Roadmap: `docs/development-roadmap.md` Phase 4b
- Research: `research/researcher-02-260716-1253-grounding-governance-ingestion.md`
- Decisions: `reports/decision-log-260716-1253-implementation-scope.md`

## Overview

- Priority: P1
- Status: pending
- Effort: 5d
- Description: Put provider selection, data-locality, usage accounting, budgets, and stop policies under harness control while preserving the existing provider adapters. No gateway service or provider rewrite.

## Key Insights

- `AdapterResponse` currently drops provider usage data, so cost governance has no trustworthy input until both adapters capture it.
- Policy must run before data is sent to a provider, not after generation.
- The existing adapter `Protocol` and factory are sufficient for the current provider count; LiteLLM/OpenRouter is deferred until a measured operations trigger.
- Round limits remain a backstop, but cost, token, wall-clock, and no-progress policies need explicit stop reasons.

## Requirements

- FR-LITL-013: stop on round, token/cost, wall-clock, or no-progress thresholds.
- FR-LITL-014 and NFR-LITL-003: provider-neutral workflow and task-based routing without changing orchestration logic.
- NFR-LITL-002: sensitive evidence is restricted to approved providers/endpoints.
- NFR-LITL-009: per-run/per-canvas resource budgets and auditable routing decisions.
- Preserve NFR-LITL-001: secrets remain in environment/config and never enter model context or audit payloads.

## Architecture / Data Flow

```text
TaskContext(stage, canvas, evidence classifications)
  → LocalityPolicy.authorize(candidate provider)
  → RoutingTable.select(stage, allowed providers)
  → GovernanceBudget.reserve(request estimate)
  → Phase 2 side_effect_intent(provider, stage, input hash, idempotency key)
  → existing ModelAdapter.generate()
  → capture Usage(tokens, provider request id, estimated cost)
  → reconcile intent + budget commit + audit event
  → StopPolicy(round | cost | tokens | elapsed | no-progress)
```

Policy is deterministic configuration plus typed models. Provider SDKs remain isolated in existing adapters.

## Related Code Files

- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/models/governance.py` — `Usage`, `Budget`, `RoutingDecision`, `DataClassification`, `StopReason`.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/policy.py` — `RoutingTable`, `LocalityPolicy`, `GovernanceBudget`, `StopPolicy`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/adapters/base.py` — add normalized usage/request metadata to `AdapterResponse`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/adapters/openai_adapter.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/adapters/claude_adapter.py` — preserve SDK usage fields.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/adapters/factory.py` — accept an explicit provider/model selection from policy; remain the adapter constructor.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/config.py` — routing table, provider locality allowlist, budget thresholds, pricing configuration, no-progress and wall-time limits.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/loop.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/orchestrator.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/watch.py` — authorize, reserve, account, and stop with explicit reasons.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/lab_agent/state_store.py` — persist routing, usage, budget, and stop audit events by canvas/run.
- Create: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_policy.py`.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_adapters.py`, `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/tests/test_orchestrator.py` — usage normalization, locality denial, route fallback, and every stop condition.
- Modify: `/home/ntdm/dev/lap-in-the-loop/apps/lab-agent/.env.example`, `/home/ntdm/dev/lap-in-the-loop/docs/setup-and-operations.md`, `/home/ntdm/dev/lap-in-the-loop/docs/system-architecture.md`, `/home/ntdm/dev/lap-in-the-loop/docs/development-roadmap.md`, `/home/ntdm/dev/lap-in-the-loop/docs/lab-in-the-loop-use-case-specification.md`, `/home/ntdm/dev/lap-in-the-loop/docs/project-changelog.md`.

## Implementation Steps

1. Normalize provider usage into `AdapterResponse`; use exact provider counts when available and mark estimates explicitly.
2. Define typed stage names (`setup`, `mock_result`, `loop_decision`, later `in_silico`, `analysis`) and a configuration-backed routing table.
3. Implement locality policy using evidence/source classifications. Deny before the adapter receives restricted content; no permissive fallback.
4. Implement atomic per-run/per-canvas budget reservation and commit. Store provider, model, counts, pricing version, and reason — never prompts or secrets.
5. Route provider calls through Phase 2 prepared intents. On timeout/ambiguous provider response, reconcile request id/status when supported or schedule a budget-bounded retry; never blindly double-submit.
6. Add stop rules for token/cost, elapsed wall time, no-progress signatures, and existing max rounds. Render/audit a distinct closure reason.
7. Rewire factory/loop/orchestrator through policy without embedding provider-specific branches in workflow code.
8. Add policy and adapter tests, including missing usage, unknown pricing, unavailable preferred provider, locality conflicts, ambiguous timeout, and duplicate-submit prevention.
9. Document configuration and operator override boundaries. Define gateway adoption trigger: reconsider only when provider count/operations complexity is measured and current factory becomes a maintenance bottleneck.

## Todo List

- [ ] Adapter usage normalized for OpenAI and Claude
- [ ] Routing/locality/budget/stop policy implemented
- [ ] Task-stage model routing wired through existing factory
- [ ] Provider calls use durable intents/reconciliation and budget-bounded retries
- [ ] Token/cost/wall-time/no-progress stops audited and rendered
- [ ] Locality denial occurs before provider call
- [ ] Pricing and policy configuration documented
- [ ] Policy/usage/stop regression tests pass
- [ ] Architecture, roadmap, operations, canonical spec, and changelog updated

## Success Criteria / Validation

- `cd apps/lab-agent && uv run pytest -q && uv run ruff check lab_agent tests && uv run mypy lab_agent`
- A restricted source cannot be routed to an unauthorized external provider.
- Each provider call records normalized usage or an explicit `usage_unavailable` status.
- Ambiguous provider timeouts do not cause unbounded/double submissions; retries are durable, idempotency-aware, and budget-limited.
- Runs stop deterministically at configured token/cost/wall-time/no-progress thresholds with no further provider or canvas writes.
- Provider/model can vary by task through configuration; orchestration source has no new provider branch.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Cost estimates differ from invoices | Med | Med | Version pricing; preserve provider usage/request ids; label estimates. |
| Locality misclassification leaks data | Low | Critical | Default deny for unknown restricted sources; policy tests; audit authorization before call. |
| Routing complexity duplicates a gateway | Med | Med | Keep table/config small; explicit adoption trigger; no dynamic plugin marketplace. |
| No-progress detection stops valid exploration | Med | Med | Use conservative signatures, configurable consecutive threshold, clear audit reason. |

## Security Considerations

- Locality and budget policy are authorization controls: fail closed on missing/invalid config.
- Logs/audit contain ids, counts, classifications, and decisions — not prompts, source excerpts, API keys, or provider responses.
- Provider credentials stay scoped to existing adapter configuration.

## Next Steps / Dependencies

- Depends on: Phase 4 evidence classifications, Phase 3 artifact security boundary, and Phase 2 durable audit.
- Blocks: Phase 7/8 external adapter routing and safety budgets.
- External gate: organization-approved provider/locality matrix and pricing source.
- Docs impact: major.
