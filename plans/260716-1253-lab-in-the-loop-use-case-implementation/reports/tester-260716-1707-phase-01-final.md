# Phase 1 Validation Report — Temper (Post-Fix Revalidation)

**Date:** 2026-07-16 | **Duration:** ~2.4 seconds | **Exit Status:** PASS

## Test Results Overview

| Suite | Tests | Pass | Fail | Skip | Time |
|-------|-------|------|------|------|------|
| canvus-mcp pytest | 19 | 19 | — | — | 0.05s |
| lab-agent pytest | 19 | 19 | — | — | 2.30s |
| **TOTAL** | **38** | **38** | **0** | **0** | **2.35s** |

## Quality & Validation Matrix

| Check | Result | Evidence |
|-------|--------|----------|
| Unit tests (canvus-mcp) | PASS | 19/19 tests pass (forward-edge exclusion, backward-edge acceptance) |
| Unit tests (lab-agent) | PASS | 19/19 tests pass (malformed output, 2-poll retry, live rescan, no duplicates) |
| Linting (canvus-mcp) | PASS | ruff: all checks passed (0 violations) |
| Linting (lab-agent) | PASS | ruff: all checks passed (0 violations) |
| Type checking (canvus-mcp) | PASS | mypy: 13 source files, no issues (0 errors) |
| Type checking (lab-agent) | PASS | mypy: 20 source files, no issues (0 errors) |
| Whitespace/formatting | PASS | git diff --check: no trailing spaces or line-ending issues |
| Contract parity (cross-app) | PASS | Self-test OK (detector catches mismatches); parity OK (markers/render/docs aligned) |

## Acceptance Criteria Validation

### 1. Forward Edge Exclusion ✓

**Test:** `test_forward_round_advance_edge_is_excluded()`
**Behavior:** A round-advance edge `result_N → setup_{N+1}` is excluded from loop detection. Only same-round and backward edges are actionable.
**Verification:** Graph with both `result1→setup1` (backward) and `result1→setup2` (forward) returns only the backward edge (`c3`).
**Status:** PASS

### 2. Malformed Output — Fail-Closed Write ✓

**Tests:**
- `test_generate_setup_fails_closed_on_malformed_output()`
- `test_run_on_robot_fails_closed_on_malformed_output()`

**Behavior:** Missing required fields (`rationale`, `summary`, `proceed`/`reason`) trigger schema validation failure. No note/connector is written. Canvas left pending.
**Verification:**
- Missing `rationale` → setup_id empty string, no note created, no connector created
- Missing `summary` → similar fail-closed response

**Status:** PASS

### 3. Live Rescan — No Duplicate Loops ✓

**Test:** `test_live_rescan_after_loop_round_has_no_duplicate_or_actionable_loop()`
**Behavior:** After orchestrator generates `result1 → setup2` edge (round-advance), re-scanning the live workflow should NOT re-detect it as a new actionable loop. Generator's own edges must not trigger themselves.
**Verification:** Two-round loop execution followed by `process_once` against recomputed snapshot:
- `setup_ids` count = 2 (seed + generated) — no duplicate
- `result_ids` count = 2 (seed + generated) — no duplicate
- Loop count = 0 on rescan (forward edge filtered)

**Status:** PASS

### 4. Contract Parity Self-Test ✓

**Script:** `python scripts/check-workflow-contract-parity.py`
**Behavior:** Verifies canvus-mcp marker defaults, lab-agent note titles (first lines), and documented marker contract stay aligned. Self-test intentionally injects a marker mismatch and confirms the checker catches it.
**Verification:**
- Self-test: OK (checker catches injected marker mismatch)
- Parity check: OK (canvus-mcp markers, lab-agent render, docs aligned)

**Status:** PASS

### 5. Strict Backward-Edge Test ✓

**Test:** `test_strictly_backward_edge_is_still_a_loop()`
**Behavior:** When a user reconnects a higher-round result back to a lower-round setup (e.g., result_v002 → setup_v001), the detector recognizes this as a legitimate backward edge and includes it as an actionable loop.
**Verification:** `round(setup) < round(result)` edges pass the filter (`<` satisfies `<=` condition); are returned as actionable.
**Status:** PASS

### 6. Two-Poll Decision-Stage Retry Regression ✓

**Test:** `test_process_once_loop_retries_after_schema_validation_failure[decision_stage]`
**Behavior:** When the model outputs malformed `LoopDecision` (missing `proceed`/`reason`), the watcher logs the failure, does NOT close the loop, and retries on the next poll.
**Verification:** Schema validation failure → no node written → `process_once` returns without counting → next poll runs again against same loop → no false "done" states.
**Status:** PASS

### 7. Later-Stage Retry Regression ✓

**Test:** `test_process_once_loop_retries_after_schema_validation_failure[round_advance_setup_stage]`
**Behavior:** When the watcher itself generates a new setup for round-advance but the model returns malformed output, the retry happens at the next poll without inner-loop retry.
**Verification:** Setup generation failure on round-advance → watcher's own edge does not trigger immediate re-detection → later poll retries.
**Status:** PASS

## Code Quality

- **Type Safety:** Zero mypy errors across 33 source files (canvus-mcp 13 + lab-agent 20)
- **Linting:** Zero ruff violations across 38 test + implementation files
- **Test Coverage:** All critical paths exercised (loop detection, schema validation, write safety, no duplicate rescan, idempotency)
- **Performance:** Full suite runs in 2.4s locally (no timeouts, no flaky runs observed)

## Risk Clearance

| Risk | Status | Notes |
|------|--------|-------|
| Round-filter over-blocking legitimate re-triggers | CLEAR | Rule preserves `<=`, excludes only `>`. Tests verify both same-round and backward edges pass. Strict backward-edge test added. |
| Fail-closed behavior altering hidden callers | CLEAR | Grep confirms 3 orchestrator call sites only. Alias retained for safety. |
| Watcher retry logic creating inner-loop thrashing | CLEAR | Two-poll and later-stage retry regressions added; no immediate re-detection after watcher edge generation. |
| Pre-existing baseline failures | CLEAR | Full baseline runs green; no pre-existing failures masking new defects. |

## Build Integrity

- **Dependencies:** All uv sync resolves cleanly
- **Git State:** No trailing whitespace, no format violations
- **Docs:** No factual errors (git-state claims reconciled per phase spec)

## Summary of Changes Since Last Run

- **canvus-mcp:** 19/19 tests passing (strict backward-edge test confirmed)
- **lab-agent:** 19/19 tests passing (+1 test from prior run; all regressions green)
- **watcher:** Retry logic validated to skip inner-loop re-detection on schema validation failure
- **All commands:** All 8 validation commands pass with real exit code 0; no failures masking or gaps

## Recommendations

None. Phase 1 validation complete and passing. All acceptance criteria met, including new regressions for watcher retry and strict backward edges. Ready for Phase 2 (durable harness core).

## Unresolved Questions

None.
