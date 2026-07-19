# Phase 5 V1 Final Tempering

## Scope

- Diff-aware mode: analyzed 30 tracked changed files plus untracked Phase 5 implementation/tests/evidence.
- Full-suite escalation: provider/runtime/config/docs/governance changes have high fan-out; ran both complete app suites.
- No source, test, documentation, plan, credential, network, commit, or push changes made by this tempering.

## Test Results Overview

| Gate | Result | Duration / detail |
|---|---:|---|
| lab-agent full pytest | PASS | 406 passed, 1 existing Starlette/httpx deprecation warning, 3.90s |
| lab-agent Ruff | PASS | `lab_agent tests` clean |
| lab-agent mypy | PASS | 75 source files |
| canvus-mcp full pytest | PASS | 37 passed, 0.29s |
| canvus-mcp Ruff | PASS | `canvus_mcp tests` clean |
| canvus-mcp mypy | PASS | 14 source files |
| workflow contract parity | PASS | self-test and real parity check clean |
| Phase 5 governance matrix | PASS | 131/131 four consecutive runs: 2.07s, 2.09s, 2.08s, 2.06s; no flakes |
| endpoint/factory/adapter suite | PASS | 15/15, 1.43s |
| endpoint suite collection | PASS | 15 collected, includes `test_canonical_openai_endpoint_requests_v1_chat_completions` |
| diff whitespace check | PASS | clean |

Executed test cases: 982 across deliberate overlapping full/focused/repeat runs; 982 passed, 0 failed, 0 skipped. Collection-only gate did not execute tests.

## Endpoint V1 Inspection

PASS. `test_openai_sdk_transport.py` constructs the real `AsyncOpenAI` SDK with `httpx.MockTransport`, invokes `chat.completions.create`, and has exact list equality asserting only `https://api.openai.com/v1/chat/completions`.

PASS. `test_provider_endpoint_defaults.py` exactly proves:

- credential-gated canonical default is `https://api.openai.com/v1`;
- explicit `provider_endpoints["openai"]` wins over `openai_base_url`;
- legacy base-url-only configuration builds the adapter with that legacy URL;
- no-key configuration has no implicit endpoint and raises before adapter creation;
- invalid endpoint and unknown/custom provider are locality-denied and factory-rejected.

This closes cycle-2's reachable non-`/v1` OpenAI default regression. The MockTransport test was collected and executed; no live provider/credential use occurred.

## Study-Context Acceptance Audit

| # | Status | Evidence |
|---:|---|---|
| 1 | PASS | Adapter usage normalization tests cover exact OpenAI/Claude counts, absent payloads, and Claude cache tokens; gateway estimates unavailable usage conservatively. |
| 2 | PASS | Gateway regression proves constructed-adapter ordered routing, locality fallback, and no post-dispatch provider fallback. |
| 3 | PASS | Locality policy and locality-closure tests prove unknown/restricted/invalid-endpoint denial before provider calls. |
| 4 | PASS | Durable budget accounting tests prove restart-restored holds, idempotent reserve/commit/release, and no restart bypass. |
| 5 | PASS | Safety and loop-stop regressions prove unknown pricing denial without a cap, conservative missing-usage handling, reservation denial, post-response overshoot, and no follow-on writes. |
| 6 | PASS | Intent tests prove submitted/executed/reconciled handling, retry timing, typed deterministic/transient/ambiguous outcomes, reconciliation, and no blind redispatch. |
| 7 | PASS | Loop/locality tests prove distinct max-round, token, cost, wall-time, no-progress, locality, and reservation closure paths, audit reasons, and rendered terminal output. |
| 8 | PASS | Full 406-test lab-agent suite plus lint/mypy pass; focused matrix covers fallback, locality, accounting, retries, duplicate prevention, overshoot, and no-write-after-stop. |
| 9 | PASS | Authoritative docs validator passes seven-doc local links/line caps, 13 config names, required Phase 5 headings/status markers, stale-wording bans, `406/406`, `37/37`, `131/131`, four/no-flake, and endpoint `15/15` changelog evidence. |

## Cycle Defect Reverification

- Cycle 1 raw provider/prompt persistence: PASS; sentinel tests assert both prompt and provider-error text absent from persisted attempts, intents, and audits.
- Cycle 1 conservative unavailable-usage accounting: PASS; oversized tool/schema reservation test proves payload-inclusive estimate blocks dispatch; configured SDK output cap is covered.
- Cycle 1 unknown pricing with no numeric cap: PASS; test asserts denial before adapter invocation and `cost_unavailable` audit reason.
- Cycle 1 governed configuration/docs migration: PASS for stated local contract; canonical credential defaults, explicit endpoint precedence, legacy override, fail-closed no-key/custom paths, and docs contract validated.
- Cycle 1 stale documentation: PASS; authoritative semantic validator passed.
- Cycle 1 reconciled overshoot ordering: PASS; intent test asserts post-reconciliation budget exception and zero fresh adapter calls; loop regression proves closure-only canvas write.
- Cycle 2 canonical endpoint compatibility: PASS; exact real-SDK MockTransport URL assertion proves `/v1/chat/completions`.

## Coverage Metrics

Not measured. Project has no coverage tool/threshold configured; no coverage claim made.

## Performance Metrics

- Full lab-agent: 3.90s.
- Full canvus-mcp: 0.29s.
- Repeated 131-test governance matrix spread: 0.03s (2.06–2.09s); no flake signal.
- No benchmark or memory-leak harness exists; none claimed.

## Build Status

PASS.

- `/tmp/lab-agent-phase5-v1-final-dist-40b0e05b`: fresh sdist and wheel built.
- `/tmp/canvus-mcp-phase5-v1-final-dist-26c8246e`: fresh sdist and wheel built.
- No build warnings reported.

## Critical Issues

None. One non-blocking test-run warning remains: Starlette deprecates the current TestClient/httpx combination in `tests/test_artifact_server.py:14`; not introduced by endpoint change and does not fail tests.

## Recommendations

1. Add coverage tooling/threshold before claiming coverage confidence.
2. Update the Starlette/httpx test-client dependency usage before its deprecation becomes an error.
3. Keep the MockTransport canonical-URL test when modifying provider endpoint normalization.

## Unresolved Questions

None.

**Status:** DONE
**Summary:** All mandated Phase 5 V1 gates pass: 406/406 lab-agent, 37/37 canvus-mcp, four stable 131/131 governance runs, 15/15 endpoint suite, static checks, parity, docs contract, diff check, and fresh builds. Canonical OpenAI SDK traffic is explicitly proven to use `/v1/chat/completions`.
**Concerns/Blockers:** None.
