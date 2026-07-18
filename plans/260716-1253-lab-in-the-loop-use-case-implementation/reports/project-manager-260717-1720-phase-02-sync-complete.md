# Phase 2 Sync Complete — Lab-in-the-Loop Durable Harness Core

**Date:** 2026-07-17 | **Status:** SEALED | **Effort:** 6d delivered (9d total including Phase 1)

---

## Executive Summary

Phase 2 (Establish Durable Harness Core) is **complete and sealed**. All 9 todos checked. All 34 Phase 2 work-items (#16–49) completed. Both critical defects from Phase 2 inspection independently confirmed FIXED via fresh code repros (not test suite). Full validation suite (157 tests, 11 infrastructure commands) passes. No blockers for Phase 3.

**Overall progress: 2 of 9 phases complete. 9d delivered, 46d remaining.**

---

## Phase 2 Completion Verification

### Todo Checklist (All 9 Checked)

- [x] Versioned SQLite migrations + integrity checks
- [x] Attempt lifecycle, expiring attempt leases, retry/backoff, and quarantine
- [x] Runtime-unique per-canvas writer lease
- [x] Side-effect intent/outbox + live reconciliation
- [x] Append-only sequenced/hashed safety audit events
- [x] Config/CLI wiring and ignored DB/WAL/backup artifacts
- [x] Crash-boundary, restart, quarantine, reconciliation, and audit-chain tests
- [x] Backup/restore drill documented and verified
- [x] Architecture + operations + roadmap + changelog updated

### Tasks #16–49 (All 34 Work-Items Completed)

| Range | Status | Count |
|-------|--------|-------|
| #16–24 | completed | 9 (core durable modules + tests + docs) |
| #25–35 | completed | 11 (design, config, wiring, fixtures) |
| #36–47 | completed | 12 (inventory, trace, verify all subsystems, full re-run) |
| #48–49 | completed | 2 (Critical 1 & 2 fixes + re-verification) |
| **Total** | **completed** | **34** |

---

## Sealed Evidence

### Test Results (Tester Report: 2026-07-17 12:15 UTC+7)

**Status: PASS — All 157 tests + 11 infrastructure commands. Exit 0.**

| Suite | Tests | Result |
|-------|-------|--------|
| lab-agent pytest | 136 | PASS |
| lab-agent ruff + mypy | 37 files | PASS |
| canvus-mcp pytest | 21 | PASS |
| canvus-mcp ruff + mypy | 13 files | PASS |
| CLI commands (integrity, backup, list-quarantined, reset) | 4 | PASS |
| Workflow parity check | 1 | PASS |
| Git whitespace check | 1 | PASS |
| Backup/restore drill | 1 | PASS |
| **Total** | **157 + 11 = 168** | **PASS** |

### Code Quality

- **Lint (ruff):** 0 violations (lab-agent 37 files, canvus-mcp 13 files)
- **Type (mypy):** 0 errors across 50 source files
- **Git:** 0 whitespace/CRLF issues; secrets clean

### Critical Defect Resolution (Reviewer Re-Inspection: 2026-07-17)

**Status: FIXED & INDEPENDENTLY VERIFIED**

#### Critical 1: Closed-Note Crash-Recovery Duplicate Window
- **Prior Issue:** Closed note created, crash before connector → restart would re-create closed note (duplicate).
- **Root Cause:** `probe_closed_note` relied on a not-yet-drawn connector to find the closed note.
- **Fix Implemented (Task #48):**
  - Added `ExpMarkers.closed: str = "[EXP:Closed]"` in canvus-mcp
  - Removed fragile `probe_closed_note`; replaced with connector-independent `probe_note_by_tag(bucket="closeds")`
  - Closed notes now tagged and scanned same way as setup/result notes
- **Independent Repro Verification:**
  - Fresh repro script (`/tmp/repro2_closed_note_no_duplicate.py`, no test-fakes coupling)
  - Result: **Exactly one Closed note, one connector, one reconciled intent** — duplicate eliminated
- **Regression Test:** `test_closed_note_crash_before_connector_recovers_without_duplicate_across_restart` (end-to-end via `process_once`, across two restart boundaries)
- **Verdict: CLOSED**

#### Critical 2: MCP Error-Payload Phantom-Success Silent Window
- **Prior Issue:** MCP returns non-raising `isError=True` response as `{"error": "..."}` JSON. Prior code treated empty/missing `id` in response as success (`setup_id = ""` or `connector_id = ""`), silently reconciled with phantom id.
- **Root Cause:** `nodes.py` used lenient `_call_json()` and `res.get("id", "") or ""` pattern (acceptable for reads, not writes).
- **Fix Implemented (Task #49):**
  - Added `MCPToolError` exception and `_parse_checked()` (raises on `"error"` key, malformed JSON, non-dict response)
  - Added `_require_id()` (raises on missing or empty `"id"`)
  - Added `_call_checked()` wrapper (parse + require id)
  - `create_node()` and `connect()` now call `_call_checked()` instead of lenient pattern
  - Reads (`read_note_text()`) kept lenient (correct for non-error outcomes on read paths)
- **Independent Repro Verification:**
  - Fresh repro script (`/tmp/repro2_write_error_fails_closed.py`, local `RealisticErrorMCP` stub)
  - Result: **Intent FAILED (never reconciled), retry succeeds exactly once, no duplicate** — phantom success eliminated
- **Regression Tests:**
  - Unit tests in `test_nodes.py` (19 tests, all four fail-closed shapes: explicit error, malformed JSON, non-object, missing/empty id)
  - End-to-end in `test_durable_recovery_integration.py` (confirmed intent `FAILED`, not `COMPLETED` with phantom id, and retry succeeds without duplicate)
- **Verdict: CLOSED**

### Acceptance Criteria Cross-Check

All 8 acceptance criteria from spec are **MET**:

1. ✓ Versioned SQLite migrations, checksums, PRAGMAs, integrity checks, backup/restore
2. ✓ Workflow attempts survive restart, use expiring leases, bounded backoff, exactly-once, quarantine, operator reset
3. ✓ Runtime-unique per-canvas writer lease blocks concurrent access, released on expiry
4. ✓ Canvas note/connector side-effects intent-recorded before execution, reconciled after crashes without duplicates (Critical 1 & 2 fixes)
5. ✓ Safety-relevant events form append-only hash chain (ids, hashes, reasons; no secrets)
6. ✓ CLI configuration & operator commands; `.state/`, DB/WAL/SHM, backups ignored by git
7. ✓ Both apps pass pytest, ruff, mypy; cross-app parity check passes; Phase 1 compatibility intact
8. ✓ Docs (architecture, operations, roadmap, changelog) accurately describe single-host scope, recovery, Postgres/multi-host migration boundary (previously-flagged "known limitation" removed; fail-closed scope corrected)

---

## Reconciliation Summary

### Code Inventory

| Project | Files | Tests | Lint | Type Check | Status |
|---------|-------|-------|------|-----------|--------|
| lab-agent (core) | 37 source | 136 | ✓ 0 issues | ✓ clean | PASS |
| canvus-mcp (MCP layer) | 13 source | 21 | ✓ 0 issues | ✓ clean | PASS |
| **Total** | **50** | **157** | **✓** | **✓** | **PASS** |

### Critical Fixes Detail

| Issue | Root Cause | Fix Location | Lines Changed | Test Coverage | Verdict |
|-------|-----------|--------------|---|---|---------|
| Closed note duplicate | Connector-dependent probe | `canvas_probe.py`, `durable_writes.py`, `canvus_mcp/experiments.py` | ~35 | `test_durable_recovery_integration.py:134–215` + independent repro | FIXED |
| MCP phantom success | Lenient response parsing on write | `nodes.py` | ~25 | `test_nodes.py` (19 tests) + `test_durable_recovery_integration.py:229–337` + independent repro | FIXED |

### Phase Effort Tracking

| Phase | Planned | Actual | Variance | Status |
|-------|---------|--------|----------|--------|
| 1 (Verify & stabilize MVP) | 3d | 3d | on-plan | complete |
| 2 (Durable harness core) | 6d | 6d | on-plan | complete |
| **Delivered to date** | **9d** | **9d** | **on-plan** | **2/9 complete** |
| Phases 3–9 (pending) | 46d | — | — | pending |

---

## Plan Status Update

**Main Plan (plan.md)**

| Field | Value | Status |
|-------|-------|--------|
| Overall progress | "Phase 2 of 9 complete (9d delivered, 46d remaining)" | ✓ Correct |
| Phase 2 row | Status: complete; Effort: 6d | ✓ Correct |
| Dependencies | Spine P1→P2→P3→...→P9; P6 branches after P5 | ✓ Intact |
| Red-team findings | 12 findings applied; 2 critical, both closed Phase 2 | ✓ Closed |

**Phase 2 File (phase-02-establish-durable-harness-core.md)**

| Field | Value | Status |
|-------|-------|--------|
| Status | complete | ✓ Correct |
| Effort | 6d | ✓ Correct |
| Todo list | All 9 checked | ✓ Correct |
| Success criteria | All 8 acceptance criteria MET | ✓ Correct |
| Next steps | Blocks Phase 3 (artifact widgets) | ✓ Correct |

**No plan corrections needed. All status, checklist, and progress fields align with sealed evidence.**

---

## Documentation Sync

### Updated (Phase 2 Scope)

- `docs/system-architecture.md` — Durable-state design, ledger, intent recovery, lease invariant, audit chain (no "known residual limitation" for closed-note window; fail-closed scope correctly scoped to `canvas_probe.py` read path)
- `docs/setup-and-operations.md` — State DB path, lease TTL, retry config, integrity/backup procedures, single-host scope explicit
- `docs/development-roadmap.md` — Phase 2 marked complete; Phase 3 unblocked; Postgres/multi-host migration boundary documented
- `docs/project-changelog.md` — Durable harness entry added; Critical 1 & 2 fixes documented with file locations and test references; test counts (136 lab-agent, 21 canvus-mcp) updated and verified

### Reports in Scope

- `reports/tester-260717-phase-02-final.md` — SEALED test evidence (all 157 + 11 commands pass)
- `reports/reviewer-260717-phase-02-inspection.md` — SEALED code review + independent verifications (both criticals fixed)
- `reports/project-manager-260716-2142-phase-01-sync-back.md` — Phase 1 baseline (for reference)

---

## Go/No-Go for Phase 3

**Status: GO**

- Phase 2 complete with no open defects (both critical defects fixed and independently verified)
- All 9 acceptance criteria met
- No blockers or unresolved dependencies
- Phase 3 (HTML Browser artifact widgets) ready to start

---

## Unresolved Questions

None. Phase 2 verification complete. All sealed evidence reconciled. No ambiguities or contradictions between plan, code, tests, and reports.

---

**Signed:** Project Manager (automated phase-sync verification)
**Evidence Base:** Tester report + Reviewer re-inspection + Task list (#16–49)
**Audit Trail:** /home/ntdm/dev/lap-in-the-loop/plans/260716-1253-lab-in-the-loop-use-case-implementation/reports/
**Status Block:** CLOSED
