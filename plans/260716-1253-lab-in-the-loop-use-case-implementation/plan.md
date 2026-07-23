---
title: "Lab-in-the-Loop Use-Case Implementation"
description: "Milestone-gated blueprint to build the full Lab-in-the-Loop closed-loop drug-discovery workflow atop the existing canvus-mcp + lab-agent apps."
status: in-progress
priority: P1
effort: "55d core delivered; real adapters remain child-plan gates"
branch: feat/phase-04-grounding-evidence
tags: [lab-in-the-loop, harness, workflow, safety-gates, drug-discovery]
blockedBy: []
blocks: []
work_type: feature
spec_waived: "SDD mode disabled (takumi.sddMode: off)"
created: 2026-07-16
progress: "Phase 7 complete; Phase 8 complete (55d delivered); Phase 9 pending"
---

# Lab-in-the-Loop Use-Case Implementation

Incremental harness-first build of the full target spec; no rewrite. External integrations use typed mocks/dry-run before gated real adapters.
Contracts stay additive and MVP-compatible; generated artifacts become dynamic HTML Browser widgets backed by canonical JSON/metadata. Level: `max`.

## Phases

| # | Phase | Effort | Status | Covers |
|---|---|---|---|---|
| 1 | [Verify & stabilize MVP](phase-01-verify-and-stabilize-mvp.md) | 3d | complete | FR-019, BR-002/003, AC-02-004 + correctness fixes |
| 2 | [Durable harness core](phase-02-establish-durable-harness-core.md) | 6d | complete | FR-019, NFR-006/008 |
| 3 | [HTML Browser artifact widgets](phase-03-upgrade-generated-artifacts-to-browser-widgets.md) | 7d | complete | FR-002/009/010/021, NFR-004/005/008/010 |
| 4 | [Grounding & evidence](phase-04-strengthen-grounding-and-evidence.md) | 5d | complete | FR-001/015/016, NFR-004, BR-003 |
| 5 | [Governance & model routing](phase-05-add-governance-and-model-routing.md) | 5d | complete | FR-013/014, NFR-002/003/009 |
| 6 | [Resumable multimodal ingestion](phase-06-build-resumable-multimodal-ingestion.md) | 6d | complete | FR-020, NFR-007 |
| 7 | [In-silico & approval gates](phase-07-implement-in-silico-and-approval-gates.md) | 8d | complete | UC-03, FR-003/004/005, BR-006/007/009 |
| 8 | [Execution, analysis & knowledge](phase-08-integrate-execution-analysis-and-knowledge.md) | 8d | **complete** | FR-006/007/008/009/010/017/018, BR-008 |
| 9 | [Production & multi-user hardening](phase-09-harden-production-and-multi-user-operations.md) | 7d | pending | NFR-006/008/010, ACT-13 |

## Dependencies

- Spine: P1→P2→P3→P4→P5→P6→P7→P8→P9.
- Phase 7 complete. Phase 8 complete (7A mocks/contracts delivered); real adapters remain external child-plan gates.
- Execute numerically by default; no `--parallel` because phases share workflow/artifact/state files.

## Key Decisions

- See [confirmed decision log](reports/decision-log-260716-1253-implementation-scope.md): harness-first, SQLite recovery, fail-visible outputs, ordered approvals, Browser artifacts, mocks-first, YAGNI.

## External Gates

- [ ] Phase 6 external gates remain open: live credentials/deployment, hosted CI and authoritative statement/branch coverage tooling, large-format validation plus capacity/backup/disk-monitoring/retention policy, video/non-CSV/TSV spreadsheets, and measured-trigger multi-host queue/object-storage migration.
- Real integration APIs/SLAs, identity/safety approval, locality/pricing policy, and reachable HTTPS/private artifact-service base URL remain external. Mocks/dry-run ship first.

## Phase 6 Closure Evidence

- Final local validation: Canvus-MCP full **134 passed**, focused **77**; lab-agent full **480 passed**, focused **122**. Ruff, mypy, compileall, lock checks, package builds, workflow parity, whitespace, and Phase 7 isolation passed.
- Final inspection seal: **9.7/10**, `criticalCount: 0`, `decision: SEALED`; C1–C2, H1–H8, and M1–M5 closed; contract intact; no reachable regressions.
- Statement/branch coverage remains unclaimed. Evidence: `evidence/temper-results.json`, `evidence/inspection-verdict.json`, and `reports/reviewer-260720-0134-phase-06-final-reinspection.md`.

## Red Team Review

- Session 2026-07-16: 12 findings applied (3 Critical, 5 High, 4 Medium; 0 rejected).
- Closed crash-loss, side-effect reconciliation, identity, retry-storm, credential-isolation, MCP-auth, audit-integrity, estimate, parity, and prompt-injection gaps.
- Full adjudication: [Red-team blueprint review](reports/red-team-260716-1253-blueprint-review.md).

## Validation Log

### Session 1 — 2026-07-16
**Trigger:** Owner added HTML Browser widgets for generated artifacts. **Questions asked:** 2.

1. **[Scope]** Phạm vi thay Note bằng HTML Browser widget nên là gì?
   - Options: Generated artifacts only | Toàn bộ workflow notes | Setup/Result only
   - **Answer:** Generated artifacts only (Recommended) — preserves simple Note-based idea/human input.
2. **[Architecture]** HTML/data của Browser widget nên được cung cấp theo mô hình nào?
   - Options: Dynamic artifact service | Generated static HTML | Inline data URL
   - **Answer:** Dynamic artifact service (Recommended) — canonical JSON/metadata stays versioned and Browser URL remains stable.

**Impact:** Added Phase 3; renumbered later phases; core estimate 48d→55d; propagated Browser artifact/store/render/security/E2E work through Phases 4, 7, 8, 9.

## Overall success criteria

- Every source FR/NFR/BR/UC is covered by a phase or explicitly assigned to an external-gate/deferred real-adapter milestone.
- All safety gates fail closed and write durable, auditable evidence; no autonomous real wet-lab action.
- Each phase leaves both apps green: `uv run pytest -q`, `uv run ruff check`, `uv run mypy` pass.
- Docs stay in sync (README, architecture, roadmap, workflow, operations, code-standards, canonical spec, changelog).
