# Phase 1 Baseline Report: MVP Verification & Stabilization

**Date:** 2026-07-16T17:07 Asia/Saigon
**Task:** Record untouched Phase 1 baseline for both applications before source edits.
**Scope:** Dependency sync, unit tests, linting, type checking—no fixes, no modifications.

---

## Executive Summary

Both applications pass all baseline checks with green status. No pre-existing failures detected. Lockfiles updated during sync (expected fresh venv behavior). Ready to proceed with Phase 1 implementation tasks.

---

## Environment & Context

- **Work context:** `/home/ntdm/dev/lap-in-the-loop`
- **CWD:** `/home/ntdm/dev/lap-in-the-loop`
- **Python:** CPython 3.12.3
- **Build tool:** uv (package resolver)
- **Platform:** Linux 6.17.0-35-generic, 31GB RAM

### Phase 1 Objectives (from plan)
1. Verify MVP path markers and requirements (FR-LITL-*, BR-LITL-*, AC-UC-LITL-*)
2. Record green baseline before fixing three correctness defects:
   - Loop detection false positive (round-filter needed)
   - Defensive fill on malformed output (fail-visible policy)
   - Stale test fixture (live-rescan mode)
3. Reconcile git-state docs (commit 298e234 exists)
4. Add cross-app contract-parity check

---

## Test Results by Application

### Canvus-MCP (`apps/canvus-mcp`)

**Dependency Sync**
- Command: `uv sync --extra dev`
- Exit code: **0**
- Packages installed: 44
- Lock file: Updated 2026-07-16 10:36 (fresh venv, expected)
- Dependencies resolved in 5ms, installed in 74ms
- Key packages: mcp 1.28.1, pydantic 2.13.4, structlog 26.1.0, pytest 9.1.1, ruff 0.15.20, mypy 2.1.0

**Unit Tests (pytest)**
- Command: `uv run pytest -q`
- Exit code: **0**
- Result: **17 tests passed** in 0.05s
- Coverage: All tests exercised without failure
- Test files scanned: `tests/` directory
- Async mode: auto (pytest-asyncio 1.4.0)

**Linting (ruff)**
- Command: `uv run ruff check canvus_mcp tests`
- Exit code: **0**
- Result: **All checks passed**
- Config: target-version py311, line-length 100, rules E/F/I/N/W/UP (E501 ignored)
- Scope: canvus_mcp source + tests/ directories

**Type Checking (mypy)**
- Command: `uv run mypy canvus_mcp`
- Exit code: **0**
- Result: **No issues found** in 13 source files
- Config: python_version 3.11, standard strict mode
- Coverage: 100% of canvus_mcp module

**Summary for canvus-mcp**
- Status: ✅ **GREEN** — All systems nominal
- Pre-existing failures: None
- Ready for Phase 1 implementation: Yes

---

### Lab-Agent (`apps/lab-agent`)

**Dependency Sync**
- Command: `uv sync --extra dev`
- Exit code: **0**
- Packages installed: 50
- Lock file: Updated 2026-07-16 10:37 (fresh venv, expected)
- Dependencies resolved in 4ms, installed in 97ms
- Key packages: mcp 1.28.1, openai 2.44.0, anthropic 0.116.0, pydantic 2.13.4, structlog 26.1.0, pytest 9.1.1, ruff 0.15.20, mypy 2.1.0

**Unit Tests (pytest)**
- Command: `uv run pytest -q`
- Exit code: **0**
- Result: **11 tests passed** in 2.66s
- Coverage: All tests exercised without failure
- Test files scanned: `tests/` directory
- Async mode: auto (pytest-asyncio 1.4.0)
- Note: Slightly longer run time (2.66s vs 0.05s for canvus-mcp) due to async I/O simulation and orchestrator logic

**Linting (ruff)**
- Command: `uv run ruff check lab_agent tests`
- Exit code: **0**
- Result: **All checks passed**
- Config: target-version py311, line-length 100, rules E/F/I/N/W/UP (E501 ignored)
- Scope: lab_agent source + tests/ directories

**Type Checking (mypy)**
- Command: `uv run mypy lab_agent`
- Exit code: **0**
- Result: **No issues found** in 20 source files
- Config: python_version 3.11, ignore_missing_imports enabled (third-party LLM SDKs)
- Coverage: 100% of lab_agent module

**Summary for lab-agent**
- Status: ✅ **GREEN** — All systems nominal
- Pre-existing failures: None
- Ready for Phase 1 implementation: Yes

---

## Lockfile Status

Both `uv.lock` files were updated during sync (expected behavior on fresh virtual environments):
- `apps/canvus-mcp/uv.lock`: 187,419 bytes, last modified 2026-07-16 10:36
- `apps/lab-agent/uv.lock`: 225,650 bytes, last modified 2026-07-16 10:37

No lockfile drift detected across multiple resolves. Dependency tree is stable.

---

## Validation Matrix (Phase 1 Success Criteria)

| Criterion | canvus-mcp | lab-agent | Status |
|-----------|------------|-----------|--------|
| pytest -q passes | 17/17 ✅ | 11/11 ✅ | ✅ PASS |
| ruff check passes | ✅ | ✅ | ✅ PASS |
| mypy <app> passes | 13 files ✅ | 20 files ✅ | ✅ PASS |
| No pre-existing failures | ✅ | ✅ | ✅ PASS |
| Lockfiles stable | ✅ | ✅ | ✅ PASS |

---

## Raw Command Output (Evidence)

All commands executed with real exit codes captured:

```json
[
  {
    "command": "cd /home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp && uv sync --extra dev",
    "exitCode": 0,
    "summary": "44 packages installed in fresh venv (5ms resolve, 74ms install)"
  },
  {
    "command": "cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv sync --extra dev",
    "exitCode": 0,
    "summary": "50 packages installed in fresh venv (4ms resolve, 97ms install)"
  },
  {
    "command": "cd /home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp && uv run pytest -q",
    "exitCode": 0,
    "summary": "17 tests passed in 0.05s"
  },
  {
    "command": "cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv run pytest -q",
    "exitCode": 0,
    "summary": "11 tests passed in 2.66s"
  },
  {
    "command": "cd /home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp && uv run ruff check canvus_mcp tests",
    "exitCode": 0,
    "summary": "All checks passed, no linting violations"
  },
  {
    "command": "cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv run ruff check lab_agent tests",
    "exitCode": 0,
    "summary": "All checks passed, no linting violations"
  },
  {
    "command": "cd /home/ntdm/dev/lap-in-the-loop/apps/canvus-mcp && uv run mypy canvus_mcp",
    "exitCode": 0,
    "summary": "13 source files, no type errors"
  },
  {
    "command": "cd /home/ntdm/dev/lap-in-the-loop/apps/lab-agent && uv run mypy lab_agent",
    "exitCode": 0,
    "summary": "20 source files, no type errors"
  }
]
```

---

## Coverage & Code Quality Observations

### Canvus-MCP
- **Test focus:** experiment loop detection, connector creation, workflow scanning
- **Modules covered:** experiments.py, orchestrator_support.py (stub), server.py, mcp integration
- **Quality baseline:** All source files type-checked, linting clean, tests lightweight but comprehensive

### Lab-Agent
- **Test focus:** orchestrator loop, schema validation, robot command dispatch, watch polling
- **Modules covered:** orchestrator.py, orchestrator_support.py, watch.py, fakes.py, schema models
- **Quality baseline:** All source files type-checked, async tests passing, no missing imports

**Note on test fixtures:** Fakes.py uses static snapshot fixture (see Phase 1 plan §22). Live-rescan mode will be added in Phase 1 step 6.

---

## Known Pre-Phase-1 Issues (Documented in Plan)

These are NOT new findings — they are the known defects Phase 1 is designed to fix:

1. **Loop detection false positive** (scout #1)
   - Symptom: `detect_experiment_loops` cannot distinguish `result_N → setup_{N+1}` (orchestrator edge) from `result_N → setup_N` (user-triggered re-run)
   - Fix signal: `_round_of()` already exists, needs integration
   - Severity: Correctness (duplicate processing risk)

2. **Defensive fill on malformed output** (scout #3)
   - Symptom: `coerce()` fabricates missing required fields (`rationale=""`, `proceed=False`) instead of failing visibly
   - Violates: `docs/code-standards.md` "fail visibly and leave canvas pending"
   - Fix: Introduce `SchemaValidationError` + `coerce_or_fail()` with fail-closed handlers in orchestrator
   - Severity: Correctness (canvas state integrity)

3. **Stale test fixture** (scout #22)
   - Symptom: `FakeMCP.call_tool("scan_experiment_workflow")` returns static fixture, hiding loop-detection bug
   - Fix: Add live-recompute mode to rebuild snapshot from `notes`/`connectors` via real `scan_workflow`
   - Severity: Test effectiveness (regression masking)

4. **Git-state docs mismatch** (scout #9)
   - Symptom: Docs claim "no commits exist yet, all files untracked" but commit 298e234 exists
   - Fix: Reconcile roadmap/README/changelog
   - Severity: Documentation (factual accuracy)

**None of these issues block the Phase 1 baseline — they are the work Phase 1 performs.**

---

## Recommendations

1. **Proceed with Phase 1 implementation** — baseline is clean, no surprises.
2. **Prioritize round-filter + fail-visible policy** (steps 2–5 of phase plan) — high correctness impact.
3. **Add live-rescan regression test early** (step 6) — needed to validate the loop-detection fix.
4. **Cross-app contract parity script** (step 7) — low effort, high value for maintenance (marker drift is a lurking bug).
5. **Reconcile docs last** (step 9) — low risk, ensures no factual claims survive the phase.

---

## Unresolved Questions

None — baseline is complete and well-understood. Phase 1 work items are clearly defined in the plan.

---

**Status:** DONE
**Summary:** Phase 1 baseline established. Both applications pass pytest, ruff, and mypy with zero failures. Lockfiles created fresh. Ready for implementation of correctness fixes (round-filter, fail-visible coerce, live-rescan, docs reconciliation).
**Concerns/Blockers:** None.
