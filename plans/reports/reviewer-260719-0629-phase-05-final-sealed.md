# Phase 5 Final Inspection Cycle 3

## Verdict

**Score: 9.7/10 — SEALED.** Cycle-1 privacy/accounting/docs defects and the cycle-2 OpenAI canonical-endpoint regression are fixed. All nine study-context acceptance criteria are demonstrated. No critical or reachable regression remains.

## Scope

- **Focus:** final Phase 5 governed routing/locality/budget/intent/stop implementation; final endpoint repair; migrations, CLI/watcher/orchestration, tests, configuration, six required docs, and final temper evidence.
- **Working-tree review:** tracked current diff plus untracked Phase 5 modules/tests/migrations/evidence. No source, test, plan, doc, or evidence was modified by this inspection.
- **Validation run now:** lab-agent 406 passed; Ruff pass; mypy pass (75 files). canvus-mcp 37 passed; Ruff pass; mypy pass (14 files). Focused Phase 5 safety/endpoint set 28 passed. Workflow parity and `git diff --check` passed.

## Critical defects

None.

## Cycle-1 and cycle-2 re-verification

1. **Safe persistence of failures — fixed.** Gateway persists fixed categories only (`apps/lab-agent/lab_agent/model_gateway.py:135-155`). Watcher maps all work and cycle errors to fixed codes before persistence/logging (`apps/lab-agent/lab_agent/watch_attempts.py:19-31,59-79`; `apps/lab-agent/lab_agent/watch.py:182-189`). Prompt/provider-error sentinels are absent from durable attempts, intents, and audits (`apps/lab-agent/tests/test_phase5_safety_regressions.py:42-88`; `apps/lab-agent/tests/test_phase5_gateway_regressions.py:61-89`). Terminal artifacts contain enum closure reasons, not error content (`apps/lab-agent/lab_agent/loop_governance.py:88-130`).
2. **Missing-usage estimate — fixed.** Serialized messages, tools, schema, schema name, and configured completion cap are included and labelled `estimated` (`apps/lab-agent/lab_agent/model_request.py:24-43,56-91`; cap wiring at `apps/lab-agent/lab_agent/governance_context.py:60-75`). Both SDK adapters enforce that cap (`apps/lab-agent/lab_agent/adapters/openai_adapter.py:160-178`; `apps/lab-agent/lab_agent/adapters/claude_adapter.py:164-186`).
3. **Unknown price — fixed.** An unavailable rate always denies reservation, whether or not a cost cap exists (`apps/lab-agent/lab_agent/policy/budget.py:68-77`); unbounded known-price dispatch and unbounded unknown-price denial are regression tested (`apps/lab-agent/tests/test_phase5_safety_regressions.py:124-161`).
4. **Canonical/default endpoint compatibility — fixed.** Credential-gated OpenAI default is now `https://api.openai.com/v1` (`apps/lab-agent/lab_agent/provider_endpoints.py:8-24`), factory passes the approved endpoint to the adapter (`apps/lab-agent/lab_agent/adapters/factory.py:16-45`), explicit endpoint wins over legacy base URL and invalid/custom/no-key cases fail closed (`apps/lab-agent/tests/test_provider_endpoint_defaults.py:15-87`). The real OpenAI SDK plus `httpx.MockTransport` proves the canonical request URL is exactly `https://api.openai.com/v1/chat/completions` (`apps/lab-agent/tests/test_openai_sdk_transport.py:10-41`).
5. **Post-reconciliation overshoot — fixed.** Reconciliation reserves, commits, reconciles, then raises before returning output (`apps/lab-agent/lab_agent/model_gateway.py:65-88`); normal response follows the same commit-then-stop order (`:156-175`). Tests prove only the closure is written after overshoot (`apps/lab-agent/tests/test_budget_overshoot.py:97-154`; `apps/lab-agent/tests/test_model_gateway_intents.py:88-123`).
6. **Documentation — fixed.** Architecture, operations, workflow, roadmap, canonical spec, and changelog cover routing/locality/pricing/usage/reservations/intents/retries/reconciliation/redaction/stops/configuration/external gates (`docs/system-architecture.md:141-151,263-265`; `docs/setup-and-operations.md:122-163`; `docs/experiment-workflow.md:196-211`; `docs/development-roadmap.md:175-199`; `docs/lab-in-the-loop-use-case-specification.md:199-200,559-566,581-596`; `docs/project-changelog.md:3-27`). The endpoint correction is consistent in architecture and operations (`docs/system-architecture.md:145`; `docs/setup-and-operations.md:132,157`). Final authoritative docs validation passed (`plans/260716-1253-lab-in-the-loop-use-case-implementation/evidence/temper-results-phase-05-v1-final.json:116-120`).

## Acceptance criteria proven

1. **OpenAI and Claude responses record normalized provider usage or an explicit conservative usage-unavailable estimate without inventing exact counts.** OpenAI and Claude normalize counts or mark unavailable (`apps/lab-agent/lab_agent/adapters/openai_adapter.py:42-58`; `apps/lab-agent/lab_agent/adapters/claude_adapter.py:42-61`); gateway converts unavailable to labelled estimate only (`apps/lab-agent/lab_agent/model_gateway.py:156-163`).
2. **Task-stage routing selects only constructed eligible adapters, falls back deterministically when a preferred adapter is unavailable, and keeps provider branches out of workflow orchestration.** Gateway intersects preferences with constructed adapters before policy selection (`apps/lab-agent/lab_agent/model_gateway.py:46-56`); ordered routing is deterministic (`apps/lab-agent/lab_agent/policy/routing.py:37-60`); no provider fallback follows dispatch (`apps/lab-agent/lab_agent/model_gateway.py:129-155`; `apps/lab-agent/tests/test_phase5_gateway_regressions.py:92-168`).
3. **Unknown, restricted, or endpoint-incompatible evidence classification is denied before any provider receives content, using configurable provider and endpoint locality policy.** Missing metadata is unknown (`apps/lab-agent/lab_agent/models/governance.py:33-41`); locality denies missing/invalid endpoint, unlisted provider, unknown, restricted, or disallowed classes before adapter dispatch (`apps/lab-agent/lab_agent/policy/locality.py:24-68`; `apps/lab-agent/lab_agent/model_gateway.py:46-54,124-132`).
4. **Per-run token and cost budgets reset for each workflow trigger while durable per-canvas accounting prevents restart-based budget bypass.** New trigger run state resets at `apps/lab-agent/lab_agent/governance_context.py:46-55`; transactional reservation checks cover run and canvas envelopes (`apps/lab-agent/lab_agent/state/budget_reservations.py:52-88`); restart/idempotency coverage is at `apps/lab-agent/tests/test_durable_budget_accounting.py:26-77`.
5. **Unknown pricing, missing usage, reservation denial, and post-response budget overshoot fail closed and permit no subsequent provider or canvas writes.** Pricing denial is pre-dispatch (`apps/lab-agent/lab_agent/policy/budget.py:68-92`), missing usage is conservatively estimated (`apps/lab-agent/lab_agent/model_gateway.py:156-163`), and committed exhaustion raises before output can cause a follow-on write (`:163-175`; `apps/lab-agent/tests/test_budget_overshoot.py:97-154`).
6. **Durable provider intents honor terminal status and next_retry_at, distinguish deterministic from transient or ambiguous failures with typed SDK errors, and prevent blind duplicate submission.** Intent state updates are durable (`apps/lab-agent/lab_agent/state/intents.py:86-159`); terminal/submitted/ambiguous/not-due/exhausted guards are explicit (`apps/lab-agent/lab_agent/model_gateway.py:60-116,129-155`); restart/reconciliation coverage is at `apps/lab-agent/tests/test_submitted_intents.py:79-160` and legacy migration coverage at `apps/lab-agent/tests/test_submitted_intent_migration.py:24-52`.
7. **Max rounds, token, cost, wall-time, no-progress, locality denial, and reservation denial produce distinct audited and rendered closure outcomes.** Typed reasons are defined in `apps/lab-agent/lab_agent/models/governance.py:54-64`, evaluated in `apps/lab-agent/lab_agent/loop_governance.py:60-72`, and rendered/audited in `:88-164`; loop regressions cover all relevant stop ordering (`apps/lab-agent/tests/test_loop_stops.py:82-134`; `apps/lab-agent/tests/test_phase5_loop_regressions.py:87-116`).
8. **The lab-agent test, lint, and type-check suites pass with regression coverage for adapter fallback, locality, accounting, retry scheduling, duplicate-submit prevention, threshold overshoot, and no-write-after-stop behavior.** Current inspection passed 406 tests, Ruff, and mypy; coverage is present in `apps/lab-agent/tests/test_model_gateway.py`, `test_phase5_gateway_regressions.py`, `test_durable_budget_accounting.py`, and `test_budget_overshoot.py`.
9. **Architecture, roadmap, operations, canonical specification, changelog, and configuration documentation accurately describe the implemented governance boundaries and external approval and pricing gates.** All six surfaces accurately state the local/source implementation and preserve the operator-owned provider approval, pricing, endpoint validation, and invoice-reconciliation limitations (`docs/system-architecture.md:145-151,298-307`; `docs/setup-and-operations.md:122-163`; `docs/development-roadmap.md:175-199`; `docs/lab-in-the-loop-use-case-specification.md:559-566,581-596`; `docs/project-changelog.md:3-27`; `apps/lab-agent/.env.example:23-52`).

## Contract, security, and quality assessment

- Provider-neutral gateway, pre-dispatch locality, no post-dispatch replay, restart-safe reservations/intents, distinct no-write-after-stop closures, migration atomicity, CLI/watcher integration, artifact/grounding compatibility, and parity were examined and hold.
- Atomic intent/reservation transitions use `BEGIN IMMEDIATE` (`apps/lab-agent/lab_agent/state/intents.py:53-104`; `apps/lab-agent/lab_agent/state/budget_reservations.py:59-87`). Migration application is checksum-guarded and transactional (`apps/lab-agent/lab_agent/state/connection.py:121-154`).
- No secret-bearing dotenv is tracked; `.env.example` has blank credentials (`apps/lab-agent/.env.example:7-15`). No raw prompt/provider errors, provider response bodies, or secrets were found in durable governed records.
- No SQL interpolation of external values, authorization bypass, N+1 database loop, or unbounded retry path found. New governance modules are purpose-split and bounded; no unnecessary provider/gateway abstraction was introduced.

## Concerns and refinements

- One unrelated test dependency warning remains: Starlette warns that its `TestClient` use of `httpx` is deprecated. It does not affect Phase 5 behavior or sealing; handle in normal dependency maintenance.
- Coverage is not measured because no coverage tool/threshold is configured; test pass counts are not a coverage claim.

## Docs impact

**Major, complete.** The documentation changed substantially to describe the governed execution contract and operator/external gates. Cycle 3 found no required additional doc changes.

## Unresolved questions

None.

**Status:** DONE
**Summary:** All nine Phase 5 acceptance criteria and associated production contracts are proven; endpoint behavior is now validated with the real OpenAI SDK transport path.
**Concerns/Blockers:** No deploy-blocking concern. Non-blocking Starlette/httpx deprecation warning and unmeasured coverage remain.
